"""Operational qualification (OQ): does the installed program do what it must, run as a real server on this computer?

It starts the server on a free local port with a scratch database and drives it through HTTP like a user would, with fixed data and answers that do not come from the program
(computed here with numpy): login and the roles, import, the analysis, the report and its archive check, the audit chain, backup and restore, persistence across a restart of
the database, and the built-in verification report of the program (about 900 checks against published values). Nothing of the site database is touched.
Performance qualification (PQ) is not here: it is the customer's proof with its own parts, gauges and limits.
"""

from __future__ import annotations

import http.cookiejar
import json
import math
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

from spc.qualification.iq import check

PASSWORD = "Oq-Check#2026-secret"
LSL, USL = 9.5, 10.5


def reference_values() -> list[float]:
    """125 readings in 25 subgroups of 5: a fixed pattern, so the expected indices can be calculated here without the program."""
    return [round(10 + ((i * 7919) % 21 - 10) / 100, 4) for i in range(125)]


def reference_csv() -> bytes:
    lines = ["lot,diameter"] + [f"L{i // 5 + 1},{v:.4f}" for i, v in enumerate(reference_values())]
    return "\n".join(lines).encode()


class Client:
    def __init__(self, base: str):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def request(self, method: str, path: str, json_body=None, raw: bytes | None = None, params: dict | None = None):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data, headers = None, {}
        if json_body is not None:
            data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        elif raw is not None:
            data, headers["Content-Type"] = raw, "application/octet-stream"
        if self.csrf and method != "GET":
            headers["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=120) as r:
                body, status, hdrs = r.read(), r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            body, status, hdrs = e.read(), e.code, dict(e.headers)
        try:
            parsed = json.loads(body) if body[:1] in (b"{", b"[") else body
        except ValueError:
            parsed = body
        return status, hdrs, parsed

    def login(self, username: str, password: str):
        status, hdrs, body = self.request("POST", "/api/auth/login", {"username": username, "password": password})
        if status == 200:
            self.csrf = body["csrf"]
        return status, body


class Server:
    def __init__(self, app):
        import uvicorn

        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(300):
            if self.server.started and self.server.servers:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("the server did not start")
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=15)


def run_oq(workdir: str | Path | None = None, progress=None, artifacts: dict | None = None) -> list[dict]:
    """Run all operational checks. `progress(text)` is called before each group. `artifacts` receives files to keep with the record (the built-in verification report as HTML)."""
    from spc import maintenance
    from spc.api.app import create_app
    from spc.auth import AuthService
    from spc.auth.audit import Audit
    from spc.db import Database

    say = progress or (lambda _t: None)
    out: list[dict] = []
    own = None
    if workdir is None:
        own = tempfile.TemporaryDirectory(prefix="spc-oq-")
        workdir = own.name
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    db_file = work / "oq.sqlite3"
    try:
        db = Database(db_file)
        auth = AuthService(db)
        for name, role in (("oq-admin", "admin"), ("oq-engineer", "engineer"), ("oq-viewer", "viewer")):
            auth.create_user(name, PASSWORD, role, "")
        app = create_app(db, auth=auth)
        values = np.array(reference_values())
        with Server(app) as srv:
            anon, admin, eng, view = (Client(srv.base) for _ in range(4))
            say("access")
            st, hdrs, _ = anon.request("GET", "/")
            csp = hdrs.get("Content-Security-Policy", hdrs.get("content-security-policy", ""))
            out.append(check("oq_page", "the web page is served with a content security policy", f"HTTP {st}", "pass" if st == 200 and "default-src 'self'" in csp else "fail"))
            out.append(check("oq_requires_login", "an API call without login is refused", f"HTTP {anon.request('GET', '/api/datasets')[0]}", "pass" if anon.request("GET", "/api/datasets")[0] == 401 else "fail"))
            bad = Client(srv.base).login("oq-admin", "wrong password")[0]
            out.append(check("oq_wrong_password", "a wrong password is refused", f"HTTP {bad}", "pass" if bad == 401 else "fail"))
            codes = [c.login(u, PASSWORD)[0] for c, u in ((admin, "oq-admin"), (eng, "oq-engineer"), (view, "oq-viewer"))]
            out.append(check("oq_login", "three users sign in", str(codes), "pass" if codes == [200, 200, 200] else "fail"))
            params = {"value": "diameter", "subgroup": "lot", "filename": "oq.csv"}
            deny = view.request("POST", "/api/datasets", raw=reference_csv(), params=params)[0]
            out.append(check("oq_role_viewer", "a viewer cannot import data", f"HTTP {deny}", "pass" if deny == 403 else "fail"))
            deny2 = eng.request("GET", "/api/audit")[0]
            out.append(check("oq_role_audit", "an engineer cannot read the audit trail", f"HTTP {deny2}", "pass" if deny2 == 403 else "fail"))
            say("analysis")
            st, _, ds = eng.request("POST", "/api/datasets", raw=reference_csv(), params=params)
            out.append(check("oq_import", "125 values are imported", f"HTTP {st}, {ds.get('summary', {}).get('n_effective') if isinstance(ds, dict) else ''} values", "pass" if st == 200 and ds["summary"]["n_effective"] == 125 else "fail"))
            if st != 200:
                return out
            body = {"lsl": LSL, "usl": USL, "stage": "production"}
            st, _, res = eng.request("POST", f"/api/datasets/{ds['id']}/analyze", body)
            ok = st == 200
            sd, mean = float(values.std(ddof=1)), float(values.mean())
            pp = (USL - LSL) / (6 * sd)
            ppk = min(USL - mean, mean - LSL) / (3 * sd)
            for key, expected in (("mean", mean), ("sd", sd), ("p", pp), ("pk", ppk)):
                got = res["indices"][key] if ok else math.nan
                out.append(check(f"oq_index_{key}", f"{expected:.10g} (calculated here)", f"{got:.10g}", "pass" if ok and abs(got - expected) <= 1e-9 * max(1.0, abs(expected)) else "fail"))
            st2, _, res2 = eng.request("POST", f"/api/datasets/{ds['id']}/analyze", body)
            same = st2 == 200 and res2 == res
            out.append(check("oq_repeatable", "the same data and settings give the same result", "identical" if same else "different", "pass" if same else "fail"))
            say("report")
            st, _, rep = eng.request("POST", f"/api/datasets/{ds['id']}/reports", {"analysis": {"lsl": LSL, "usl": USL, "model": "A1", "characteristic_class": "major"},
                                                                                  "meta": {"process": "OQ", "machine": "OQ", "unit": "mm", "target": 10.0}, "language": "en"})
            out.append(check("oq_report", "a report with a SHA-256 digest is made", f"HTTP {st}", "pass" if st == 200 and len(rep.get("digest", "")) == 64 else "fail"))
            if st == 200:
                _, _, archive = eng.request("GET", rep["urls"]["archive"])
                st_a, _, verdict = eng.request("POST", "/api/archive/check", raw=archive if isinstance(archive, bytes) else json.dumps(archive).encode())
                out.append(check("oq_archive_check", "the archive of the report checks as intact", json.dumps(verdict)[:120], "pass" if st_a == 200 and verdict.get("integrity_ok") and verdict.get("reproduced") else "fail"))
                tampered = json.loads(archive) if isinstance(archive, bytes) else archive
                tampered["dataset"]["values"][0] += 0.5
                _, _, verdict2 = eng.request("POST", "/api/archive/check", json_body=tampered)
                out.append(check("oq_archive_tamper", "a changed archive is found", json.dumps(verdict2)[:120], "pass" if isinstance(verdict2, dict) and verdict2.get("integrity_ok") is False else "fail"))
            say("verification")
            st, _, run = eng.request("POST", "/api/validation/runs")
            vr = run if isinstance(run, dict) else {}
            ver = vr.get("verification", {})
            out.append(check("oq_builtin_verification", "the built-in verification report: no check fails", f"{len(ver.get('checks', []))} checks, {ver.get('failed')} failed, {ver.get('known')} known",
                             "pass" if st == 200 and ver.get("failed") == 0 and len(ver.get("checks", [])) > 500 else "fail", f"digest {vr.get('digest', '')[:16]}"))
            if artifacts is not None and st == 200 and isinstance(run, dict):
                for lang in ("en", "zh-TW"):
                    sr, _, page = admin.request("GET", f"/api/validation/runs/{run['id']}/report", params={"lang": lang})
                    if sr == 200 and isinstance(page, bytes):
                        artifacts[f"verification-report-{lang}.html"] = page
            say("audit")
            st, _, chain = admin.request("GET", "/api/audit/verify")
            out.append(check("oq_audit_chain", "the audit chain is unbroken after these actions", f"{chain.get('entries')} entries", "pass" if st == 200 and chain.get("ok") else "fail"))
        say("backup")
        info = maintenance.backup(db_file, work / "backup")
        restored = work / "restored.sqlite3"
        r = maintenance.restore(info["file"], restored)
        out.append(check("oq_backup_restore", "a backup restores to a database with an unbroken audit chain", f"{r['audit_entries']} entries, sha256 {info['sha256'][:16]}", "pass" if r["audit_ok"] and info["audit_ok"] else "fail"))
        db.close()
        say("persistence")
        db2 = Database(db_file)
        n = db2.one("SELECT COUNT(*) AS n FROM datasets")["n"]
        again = Audit(db2).verify()
        db2.close()
        out.append(check("oq_persistence", "after the database is closed and opened again the data and the chain are there", f"{n} dataset(s), chain {'ok' if again['ok'] else 'broken'}", "pass" if n == 1 and again["ok"] else "fail"))
    except Exception as exc:  # a qualification that cannot run is a failed one, with the reason
        out.append(check("oq_error", "the operational checks run", f"{type(exc).__name__}: {exc}", "fail"))
    finally:
        if own is not None:
            try:
                own.cleanup()
            except OSError:
                pass
    return out

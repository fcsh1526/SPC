"""Installation and operational qualification, backup and restore, first setup, the release manifest."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from spc import maintenance, setup as spc_setup
from spc.admin import main as admin_main
from spc.api.__main__ import read_config
from spc.auth import AuthService
from spc.db import Database
from spc.qualification import iq as IQ
from spc.qualification import manifest as M
from spc.qualification import oq as OQ
from spc.qualification import report as R
from spc.qualification.__main__ import main as qualify_main

PW = "Correct-Horse-Battery-9!"


def fake_package(tmp_path: Path) -> Path:
    root = tmp_path / "pkg"
    (root / "web" / "static" / "i18n").mkdir(parents=True)
    (root / "a.py").write_text("print(1)\n")
    (root / "web" / "static" / "index.html").write_text("<html></html>")
    (root / "web" / "static" / "app.js").write_text("1")
    for n in ("en", "zh-TW"):
        (root / "web" / "static" / "i18n" / f"{n}.json").write_text("{}")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "a.cpython-311.pyc").write_bytes(b"x")
    return root


def database(tmp_path: Path, name="a.sqlite3") -> Path:
    p = tmp_path / name
    db = Database(p)
    AuthService(db).create_user("ada", PW, "admin", "")
    db.close()
    return p


# ------------------------------------------------------------------ the manifest

def test_the_manifest_finds_changed_missing_and_added_files(tmp_path):
    root = fake_package(tmp_path)
    m = M.build(root, dependencies={"numpy": "1"})
    assert "__pycache__/a.cpython-311.pyc" not in m["files"] and set(m["files"]) >= {"a.py", "web/static/app.js"}
    assert M.verify(m, root)["ok"] and M.verify(m, root)["intact"]
    (root / "a.py").write_text("print(2)\n")
    (root / "web" / "static" / "app.js").unlink()
    (root / "new.py").write_text("x")
    v = M.verify(m, root)
    assert not v["ok"] and v["modified"] == ["a.py"] and v["missing"] == ["web/static/app.js"] and v["extra"] == ["new.py"]
    forged = {**m, "version": "9.9.9"}
    assert not M.verify(forged, root)["intact"]  # the manifest itself was changed
    # the manifest file is not part of what it lists, and it can be written and read
    written = M.write(root, dependencies={})
    assert M.load(root / M.MANIFEST_NAME)["digest"] == written["digest"] and "RELEASE.json" not in written["files"]


# ------------------------------------------------------------------ IQ

def by_id(checks):
    return {c["id"]: c for c in checks}


def test_iq_of_the_real_program_against_its_own_manifest(tmp_path):
    m = M.build()
    path = tmp_path / "RELEASE.json"
    path.write_text(json.dumps(m))
    c = by_id(IQ.run_iq(manifest_path=path, require_manifest=True, data_dir=tmp_path))
    assert [k for k, v in c.items() if v["status"] == "fail"] == []
    assert c["files_intact"]["status"] == "pass" and c["manifest_digest"]["status"] == "pass" and c["packages_locked"]["status"] == "pass"
    # a file that differs from the build is a failure
    m["files"]["web/static/app.js"] = "0" * 64
    m["digest"] = M.digest_of(m)
    path.write_text(json.dumps(m))
    c = by_id(IQ.run_iq(manifest_path=path, require_manifest=True, data_dir=tmp_path))
    assert c["files_intact"]["status"] == "fail" and "app.js" in c["files_intact"]["note"]
    # a manifest whose digest does not match its content
    m["version"] = "8.8.8"
    path.write_text(json.dumps(m))
    c = by_id(IQ.run_iq(manifest_path=path, require_manifest=True, data_dir=tmp_path))
    assert c["manifest_digest"]["status"] == "fail" and c["program_version"]["status"] == "fail"


def test_iq_without_a_manifest_warns_in_development_and_fails_for_a_release(tmp_path):
    assert by_id(IQ.run_iq(manifest_path=tmp_path / "none.json", data_dir=tmp_path))["manifest"]["status"] == "warn"
    assert by_id(IQ.run_iq(manifest_path=tmp_path / "none.json", require_manifest=True, data_dir=tmp_path))["manifest"]["status"] == "fail"


def test_iq_reads_the_site_database_without_changing_it(tmp_path):
    p = database(tmp_path)
    before = p.read_bytes()
    c = by_id(IQ.run_iq(db_path=p))
    assert c["database_integrity"]["status"] == "pass" and c["database_schema"]["status"] == "pass" and c["database_audit_chain"]["status"] == "pass"
    assert p.read_bytes() == before
    garbage = tmp_path / "bad.sqlite3"
    garbage.write_bytes(b"this is not a database" * 50)
    assert by_id(IQ.run_iq(db_path=garbage))["database"]["status"] == "fail"
    newer = database(tmp_path, "newer.sqlite3")
    conn = sqlite3.connect(newer)
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    assert by_id(IQ.run_iq(db_path=newer))["database_schema"]["status"] == "fail"
    broken = database(tmp_path, "broken.sqlite3")
    conn = sqlite3.connect(broken)
    conn.execute("UPDATE audit SET action = 'changed' WHERE id = 1")
    conn.commit()
    conn.close()
    assert by_id(IQ.run_iq(db_path=broken))["database_audit_chain"]["status"] == "fail"


# ------------------------------------------------------------------ OQ

def test_oq_runs_the_real_server_and_every_check_passes():
    artifacts = {}
    checks = OQ.run_oq(artifacts=artifacts)
    c = by_id(checks)
    assert [k for k, v in c.items() if v["status"] != "pass"] == []
    assert {"oq_login", "oq_role_viewer", "oq_index_pk", "oq_archive_tamper", "oq_builtin_verification", "oq_backup_restore", "oq_persistence"} <= set(c)
    assert "900" in c["oq_builtin_verification"]["actual"] or int(c["oq_builtin_verification"]["actual"].split()[0]) > 500
    assert set(artifacts) == {"verification-report-en.html", "verification-report-zh-TW.html"}


# ------------------------------------------------------------------ the record and the command

def test_the_record_has_a_digest_that_shows_a_change():
    rec = R.make_record("iqoq", [IQ.check("python_version", ">= 3.10", "3.11", "pass"), IQ.check("manifest", "x", "y", "warn", "look")], IQ.host_info(), {"digest": "d" * 64})
    assert rec["verdict"] == "warn" and rec["counts"] == {"fail": 0, "warn": 1, "info": 0, "pass": 1} and R.verify_record(rec)
    tampered = json.loads(json.dumps(rec))
    tampered["checks"][1]["status"] = "pass"
    assert not R.verify_record(tampered)
    for lang, word in (("en", "PASSED, with items"), ("zh-TW", "通過，另有項目需人員審閱")):
        page = R.render(rec, lang)
        assert word in page and rec["digest"] in page and "<script" not in page and "PQ" in page
    assert R.make_record("iq", [IQ.check("a", 1, 2, "fail")], {}, None)["verdict"] == "fail"


def test_spc_qualify_writes_records_and_the_exit_code_follows_the_result(tmp_path, capsys):
    out = tmp_path / "q"
    assert qualify_main(["iq", "--out", str(out), "--data-dir", str(tmp_path)]) == 0  # a development copy has no manifest: a warning only
    files = sorted(p.name for p in out.iterdir())
    assert any(f.endswith(".json") for f in files) and any(f.endswith(".en.html") for f in files) and any(f.endswith(".zh-TW.html") for f in files)
    record = next(out.glob("IQ-*.json"))
    assert qualify_main(["verify", str(record)]) == 0
    data = json.loads(record.read_text())
    data["verdict"] = "pass"
    record.write_text(json.dumps(data))
    assert qualify_main(["verify", str(record)]) == 1
    assert qualify_main(["iq", "--out", str(tmp_path / "q2"), "--require-manifest", "--manifest", str(tmp_path / "missing.json"), "--data-dir", str(tmp_path)]) == 1
    assert qualify_main(["verify", str(tmp_path / "nothing.json")]) == 2


# ------------------------------------------------------------------ backup and restore

def test_backup_and_restore_round_trip_with_the_audit_chain(tmp_path):
    p = database(tmp_path)
    b = maintenance.backup(p, tmp_path / "bk")
    assert Path(b["file"]).is_file() and Path(b["file"] + ".sha256").read_text().split()[0] == b["sha256"] == maintenance.sha256_file(Path(b["file"]))
    assert b["integrity"] == "ok" and b["audit_ok"]
    target = tmp_path / "restored.sqlite3"
    r = maintenance.restore(b["file"], target)
    assert r["audit_ok"] and r["audit_entries"] == b["audit_entries"] + 1 and r["kept_previous"] is None
    db = Database(target)
    actions = [e["action"] for e in AuthService(db).audit.list(10)]
    db.close()
    assert actions[0] == "restore_performed"
    # restoring over an existing database keeps it
    r2 = maintenance.restore(b["file"], target)
    assert r2["kept_previous"] and Path(r2["kept_previous"]).is_file()
    # the source database recorded the backup
    src = Database(p)
    assert "backup_made" in [e["action"] for e in AuthService(src).audit.list(10)]
    src.close()


def test_restore_refuses_a_damaged_changed_or_newer_backup(tmp_path):
    p = database(tmp_path)
    b = maintenance.backup(p, tmp_path / "bk")["file"]
    bad = tmp_path / "bad.sqlite3"
    bad.write_bytes(b"not a database" * 100)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.restore(bad, tmp_path / "x.sqlite3")
    with pytest.raises(maintenance.MaintenanceError) as e:
        maintenance.restore(tmp_path / "nothing", tmp_path / "x.sqlite3")
    assert e.value.code == "file_not_found"
    changed = tmp_path / "changed.sqlite3"
    shutil.copy(b, changed)
    Path(str(changed) + ".sha256").write_text("0" * 64 + "  changed\n")
    with pytest.raises(maintenance.MaintenanceError) as e:
        maintenance.restore(changed, tmp_path / "x.sqlite3")
    assert e.value.code == "backup_digest_differs"
    newer = tmp_path / "newer.sqlite3"
    shutil.copy(b, newer)
    conn = sqlite3.connect(newer)
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    with pytest.raises(maintenance.MaintenanceError) as e:
        maintenance.restore(newer, tmp_path / "x.sqlite3")
    assert e.value.code == "backup_newer"
    chain = tmp_path / "chain.sqlite3"
    shutil.copy(b, chain)
    conn = sqlite3.connect(chain)
    conn.execute("UPDATE audit SET action = 'changed' WHERE id = 1")
    conn.commit()
    conn.close()
    with pytest.raises(maintenance.MaintenanceError) as e:
        maintenance.restore(chain, tmp_path / "x.sqlite3")
    assert e.value.code == "backup_audit_broken"
    assert not (tmp_path / "x.sqlite3").exists()


def test_the_admin_command_makes_and_restores_backups(tmp_path, capsys):
    p = database(tmp_path)
    assert admin_main(["--db", str(p), "backup", "--to", str(tmp_path / "bk")]) == 0
    file = next((tmp_path / "bk").glob("*.sqlite3"))
    assert admin_main(["--db", str(tmp_path / "new.sqlite3"), "restore", str(file), "--yes"]) == 0
    assert admin_main(["--db", str(tmp_path / "new2.sqlite3"), "restore", str(tmp_path / "none"), "--yes"]) == 1
    assert "restored from" in capsys.readouterr().out


# ------------------------------------------------------------------ first setup and the settings file

def test_setup_makes_the_folders_database_administrator_and_settings(tmp_path):
    pw = tmp_path / "pw.txt"
    pw.write_text(PW + "\n")
    assert spc_setup.main(["--data-dir", str(tmp_path / "d"), "--admin", "boss", "--password-file", str(pw), "--host", "0.0.0.0", "--port", "8443"]) == 0
    assert not pw.exists()  # the password file is deleted after it is read
    d = tmp_path / "d"
    assert all((d / n).is_dir() for n in ("backup", "qualification", "certs"))
    cfg = json.loads((d / "spc.json").read_text())
    assert cfg == {"db": str(d / "spc.sqlite3"), "host": "0.0.0.0", "port": 8443}
    db = Database(d / "spc.sqlite3")
    assert [(u.username, u.role) for u in AuthService(db).list_users()] == [("boss", "admin")]
    db.close()
    # run again: nothing is replaced, no second user
    assert spc_setup.main(["--data-dir", str(d)]) == 0
    assert json.loads((d / "spc.json").read_text()) == cfg
    assert read_config(str(d / "spc.json")) == cfg


def test_setup_needs_an_administrator_for_a_new_database_and_can_make_a_certificate(tmp_path):
    with pytest.raises(SystemExit):
        spc_setup.setup(tmp_path / "e")
    r = spc_setup.setup(tmp_path / "t", "boss", PW, tls="self-signed", port=8443)
    s = r["settings"]
    assert Path(s["ssl_certfile"]).read_bytes().startswith(b"-----BEGIN CERTIFICATE-----") and Path(s["ssl_keyfile"]).is_file() and s["secure_cookies"] is True
    with pytest.raises(SystemExit):
        spc_setup.setup(tmp_path / "f", "boss", PW, tls="files", cert=str(tmp_path / "no.crt"), key=str(tmp_path / "no.key"))


def test_the_settings_file_is_checked(tmp_path):
    p = tmp_path / "c.json"
    for text in ("not json", json.dumps([1]), json.dumps({"port": "8000"}), json.dumps({"colour": "red"}), json.dumps({"port": True})):
        p.write_text(text)
        with pytest.raises(SystemExit):
            read_config(str(p))
    p.write_text(json.dumps({"port": 9000, "host": "127.0.0.1"}))
    assert read_config(str(p)) == {"port": 9000, "host": "127.0.0.1"}


# ------------------------------------------------------------------ the Windows packaging files (they cannot run here: check that what they call exists)

def test_the_windows_packaging_calls_commands_and_options_that_exist():
    import importlib
    import re
    import subprocess
    import sys

    base = Path(__file__).resolve().parents[1] / "packaging" / "windows"
    text = (base / "spc.iss").read_text() + (base / "build.ps1").read_text() + (base / "register-task.ps1").read_text()
    modules = set(re.findall(r"-m (spc(?:\.\w+)+)", text))
    assert {"spc.setup", "spc.admin", "spc.qualification", "spc.api"} <= modules
    for m in modules:
        importlib.import_module(m)
    helps = {m: subprocess.run([sys.executable, "-m", m, "--help"], capture_output=True, text=True).stdout for m in ("spc.setup", "spc.qualification", "spc.api", "spc.admin")}
    for option in ("--data-dir", "--admin", "--password-file", "--host", "--port", "--tls"):
        assert option in helps["spc.setup"] and option in text
    assert "--config" in helps["spc.api"] and "-m spc.api --config" in text
    q = subprocess.run([sys.executable, "-m", "spc.qualification", "all", "--help"], capture_output=True, text=True).stdout
    for option in ("--out", "--db", "--require-manifest", "--quiet"):
        assert option in q and option in text
    assert "manifest --root" in text and "backup --to" in text and "backup" in helps["spc.admin"]
    for cmd in (base / "bin").glob("*.cmd"):
        module = re.search(r"-m (spc[\w.]*)", cmd.read_text()).group(1)
        importlib.import_module(module)
    # the files the build copies exist
    for name in ("register-task.ps1", "unregister-task.ps1", "spc.iss", "build.ps1"):
        assert (base / name).is_file()
    assert (Path(__file__).resolve().parents[1] / "docs" / "INSTALLATION.md").is_file()

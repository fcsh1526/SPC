"""Installation qualification (IQ): is the program on this computer the program that was released, with the packages it was tested with, and can it keep its data here?

The checks only look; the site database is opened read only. Without `db_path` a scratch database in a temporary folder checks that the program can create one.
Each check is a dict: id, expected, actual, status ('pass', 'fail', 'warn', 'info') and note. A 'fail' fails the qualification; a 'warn' needs a person to look.
"""

from __future__ import annotations

import getpass
import os
import platform
import shutil
import socket
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import spc
from spc.db.database import SCHEMA_VERSION
from spc.qualification import manifest as M

REQUIRED = {"numpy": "1.24", "scipy": "1.11", "fastapi": "0.110", "uvicorn": "0.27", "openpyxl": "3.1"}
OPTIONAL = ("asyncua", "cryptography", "httpx")
MIN_PYTHON = (3, 10)
MIN_FREE_BYTES = 1 << 30


def check(id_: str, expected, actual, status: str, note: str = "") -> dict:
    return {"id": id_, "expected": expected, "actual": actual, "status": status, "note": note}


def _vtuple(v: str) -> tuple:
    out = []
    for part in v.replace("-", ".").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def host_info() -> dict:
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    return {"host": socket.gethostname(), "user": user, "platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version(),
            "python_executable": sys.executable, "program": spc.__version__, "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timezone": datetime.now().astimezone().tzname() or ""}


def run_iq(db_path: str | os.PathLike | None = None, manifest_path: str | os.PathLike | None = None, require_manifest: bool = False, data_dir: str | os.PathLike | None = None,
           root: str | os.PathLike | None = None) -> list[dict]:
    out: list[dict] = []
    # the interpreter
    py = sys.version_info[:3]
    out.append(check("python_version", f">= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}", platform.python_version(), "pass" if py >= MIN_PYTHON else "fail"))
    out.append(check("python_bits", "64 bit", f"{8 * struct_size()} bit", "pass" if struct_size() == 8 else "warn", "a 32 bit Python limits the memory of large datasets"))
    # the packages
    installed = M.installed_distributions()
    for name, minimum in REQUIRED.items():
        v = installed.get(name)
        out.append(check(f"package_{name}", f">= {minimum}", v or "missing", "pass" if v and _vtuple(v) >= _vtuple(minimum) else "fail"))
    for name in OPTIONAL:
        out.append(check(f"package_{name}", "optional", installed.get(name, "not installed"), "info"))
    # the release manifest: the files and the locked package versions
    manifest = M.load(manifest_path)
    if manifest is None:
        out.append(check("manifest", "RELEASE.json present", "not found", "fail" if require_manifest else "warn",
                         "a release carries RELEASE.json; a development copy does not, and its files cannot be compared with a build"))
    elif manifest.get("unreadable"):
        out.append(check("manifest", "RELEASE.json readable", "unreadable", "fail"))
    else:
        v = M.verify(manifest, Path(root) if root else None)
        out.append(check("manifest_digest", manifest.get("digest"), "intact" if v["intact"] else "the manifest was changed", "pass" if v["intact"] else "fail"))
        out.append(check("program_version", manifest.get("version"), spc.__version__, "pass" if manifest.get("version") == spc.__version__ else "fail"))
        out.append(check("files_intact", f"{v['files']} files as built", "all match" if v["ok"] else f"missing {len(v['missing'])}, modified {len(v['modified'])}, extra {len(v['extra'])}",
                         "pass" if v["ok"] else "fail", "; ".join((v["missing"] + v["modified"] + v["extra"])[:10])))
        locked = manifest.get("dependencies") or {}
        differ = {k: (locked[k], installed.get(k)) for k in locked if installed.get(k) != locked[k]}
        out.append(check("packages_locked", f"{len(locked)} packages as tested", "all match" if not differ else f"{len(differ)} differ", "pass" if not differ else "warn",
                         "; ".join(f"{k} {a} -> {b}" for k, (a, b) in list(differ.items())[:10])))
    # the program's own files
    static = (Path(root) if root else M.package_root()) / "web" / "static"
    missing = [n for n in ("index.html", "app.js", "i18n/en.json", "i18n/zh-TW.json") if not (static / n).is_file()]
    out.append(check("web_files", "index.html, app.js, i18n en and zh-TW", "all present" if not missing else "missing " + ", ".join(missing), "pass" if not missing else "fail"))
    # the data
    if db_path:
        out += _site_database(Path(db_path))
    else:
        out += _scratch_database()
    folder = Path(data_dir) if data_dir else (Path(db_path).resolve().parent if db_path else Path(tempfile.gettempdir()))
    out.append(_writable(folder))
    try:
        free = shutil.disk_usage(folder if folder.exists() else folder.parent).free
        out.append(check("disk_free", f">= {MIN_FREE_BYTES >> 20} MiB", f"{free >> 20} MiB", "pass" if free >= MIN_FREE_BYTES else "warn"))
    except OSError as exc:
        out.append(check("disk_free", f">= {MIN_FREE_BYTES >> 20} MiB", f"unknown ({exc})", "warn"))
    now = datetime.now(timezone.utc)
    out.append(check("clock", "UTC time and zone recorded", f"{now.strftime('%Y-%m-%d %H:%M:%S')} UTC, zone {datetime.now().astimezone().tzname()}", "info",
                     "the audit trail and reports use UTC; check the computer's clock against a time source"))
    return out


def struct_size() -> int:
    import struct

    return struct.calcsize("P")


def _site_database(path: Path) -> list[dict]:
    from spc.maintenance import MaintenanceError, inspect_file

    if not path.exists():
        return [check("database", "the database file exists", f"{path} does not exist", "warn", "it is created by the setup")]
    try:
        info = inspect_file(path)
    except MaintenanceError as exc:
        return [check("database", "a readable SQLite file", str(exc), "fail")]
    out = [check("database_integrity", "ok", info["integrity"], "pass" if info["integrity"] == "ok" else "fail"),
           check("database_schema", f"<= {SCHEMA_VERSION}", str(info["schema_version"]), "pass" if info["schema_version"] <= SCHEMA_VERSION else "fail",
                 "the database is newer than this program" if info["schema_version"] > SCHEMA_VERSION else ("it is migrated when the server starts" if info["schema_version"] < SCHEMA_VERSION else ""))]
    out.append(check("database_audit_chain", "unbroken", f"{info['audit_entries']} entries" if info["audit_ok"] else f"broken at entry {info['audit_broken_at']}", "pass" if info["audit_ok"] else "fail"))
    if os.name == "posix":
        mode = path.stat().st_mode & 0o777
        out.append(check("database_permissions", "no access for group and others", oct(mode), "pass" if mode & 0o077 == 0 else "warn", "the file holds password hashes and all data"))
    return out


def _scratch_database() -> list[dict]:
    from spc.db import Database

    with tempfile.TemporaryDirectory() as d:
        try:
            db = Database(Path(d) / "iq.sqlite3")
            version = db.one("PRAGMA user_version")[0]
            db.execute("CREATE TABLE _iq (x INTEGER)")
            db.execute("INSERT INTO _iq VALUES (1)")
            ok = db.one("SELECT x FROM _iq")["x"] == 1
            db.close()
        except Exception as exc:
            return [check("database_create", "a new database can be made and written", f"{type(exc).__name__}: {exc}", "fail")]
    return [check("database_create", f"schema {SCHEMA_VERSION}", f"schema {version}", "pass" if version == SCHEMA_VERSION and ok else "fail")]


def _writable(folder: Path) -> dict:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".iq-", delete=True) as f:
            f.write(b"x")
        return check("data_folder_writable", str(folder), "writable", "pass")
    except OSError as exc:
        return check("data_folder_writable", str(folder), f"not writable ({exc})", "fail")

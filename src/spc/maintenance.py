"""Backup and restore of the database file, and the first-time setup of a data folder.

A backup is made with the SQLite online backup, so it is consistent while the server runs. It is checked (integrity check, the audit chain) and written with a SHA-256 file next to it.
A restore checks the same things, keeps the file it replaces, and writes both events into the audit chain of the restored database.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from spc.auth.audit import Audit
from spc.db.database import SCHEMA_VERSION, Database

SYSTEM_USER = "spc-maintenance"


class MaintenanceError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def inspect_file(path: str | os.PathLike) -> dict:
    """Open a database file read only and check it: integrity, schema version and the audit chain. Nothing is changed."""
    p = Path(path)
    if not p.is_file():
        raise MaintenanceError("file_not_found", f"{p} does not exist")
    try:
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise MaintenanceError("not_a_database", str(exc)) from None
    try:
        conn.row_factory = sqlite3.Row
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise MaintenanceError("not_a_database", str(exc)) from None
        chain = {"ok": True, "entries": 0}
        if integrity == "ok" and version >= 1:
            class _ReadOnly:  # Audit.verify only reads
                def all(self, sql, params=()):
                    return conn.execute(sql, params).fetchall()

            chain = Audit(_ReadOnly()).verify()
        return {"integrity": integrity, "schema_version": version, "audit_ok": bool(chain["ok"]), "audit_entries": chain["entries"], "audit_broken_at": chain.get("broken_at")}
    finally:
        conn.close()


def backup(db_path: str | os.PathLike, dest_dir: str | os.PathLike) -> dict:
    """Write `spc-backup-<UTC time>.sqlite3` and its `.sha256` into `dest_dir`; the event goes into the audit chain of the source database."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"spc-backup-{_stamp()}.sqlite3"
    n = 1
    while target.exists():
        n += 1
        target = dest_dir / f"spc-backup-{_stamp()}-{n}.sqlite3"
    db = Database(db_path)
    try:
        with db._lock:
            out = sqlite3.connect(str(target))
            try:
                db._conn.backup(out)
            finally:
                out.close()
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        info = inspect_file(target)
        if info["integrity"] != "ok" or not info["audit_ok"]:
            target.unlink(missing_ok=True)
            raise MaintenanceError("backup_failed", f"the copy did not pass the check: {info}")
        digest = sha256_file(target)
        (target.parent / (target.name + ".sha256")).write_text(f"{digest}  {target.name}\n", encoding="utf-8")
        Audit(db).append("backup_made", username=SYSTEM_USER, target=target.name, detail={"sha256": digest, "audit_entries": info["audit_entries"], "schema_version": info["schema_version"]})
    finally:
        db.close()
    return {"file": str(target), "sha256": digest, **info}


def restore(backup_file: str | os.PathLike, db_path: str | os.PathLike) -> dict:
    """Replace the database with a backup. The backup must pass the checks and must not be newer than this program; the replaced file is kept beside it."""
    src, dst = Path(backup_file), Path(db_path)
    info = inspect_file(src)
    if info["integrity"] != "ok":
        raise MaintenanceError("backup_damaged", f"integrity check: {info['integrity']}")
    if not info["audit_ok"]:
        raise MaintenanceError("backup_audit_broken", f"the audit chain is broken at entry {info['audit_broken_at']}")
    if info["schema_version"] > SCHEMA_VERSION:
        raise MaintenanceError("backup_newer", f"the backup has schema {info['schema_version']}, this program knows {SCHEMA_VERSION}: install the newer program first")
    sidecar = src.parent / (src.name + ".sha256")
    digest = sha256_file(src)
    if sidecar.is_file():
        recorded = sidecar.read_text(encoding="utf-8").split()[0].lower()
        if recorded != digest:
            raise MaintenanceError("backup_digest_differs", "the SHA-256 file does not match the backup")
    kept = None
    if dst.exists():
        kept = dst.with_name(f"{dst.name}.before-restore-{_stamp()}")
        shutil.move(str(dst), str(kept))
    for suffix in ("-wal", "-shm"):
        stale = Path(str(dst) + suffix)
        if stale.exists():
            stale.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    try:
        os.chmod(dst, 0o600)
    except OSError:
        pass
    db = Database(dst)  # migrates an older backup to the schema of this program
    try:
        Audit(db).append("restore_performed", username=SYSTEM_USER, target=src.name, detail={"sha256": digest, "replaced": kept.name if kept else "", "schema_version": info["schema_version"]})
        chain = Audit(db).verify()
    finally:
        db.close()
    return {"restored_from": str(src), "kept_previous": str(kept) if kept else None, "sha256": digest, "audit_ok": chain["ok"], "audit_entries": chain["entries"]}

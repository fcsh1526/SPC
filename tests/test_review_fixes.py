"""The findings of the software review: backup and restore safety, the server lock, upload and workbook limits, the cost of searches, the login trail and the
type 1 spread of older measurement systems."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile

import pytest

from spc import maintenance
from spc.auth import AuthService
from spc.core import doe_optimal as O
from spc.data.csv_io import DataImportError
from spc.data.xlsx_io import load_xlsx
from spc.data.csv_io import ColumnMap
from spc.db import Database, serverlock
from tests.conftest import PASSWORD, logged_in_client, make_app
from tests.test_api import err
from tests.test_profiles import old_database


def test_a_backup_never_creates_a_database_from_a_wrong_path(tmp_path):
    with pytest.raises(maintenance.MaintenanceError) as e:
        maintenance.backup(tmp_path / "typo.sqlite3", tmp_path / "bk")
    assert e.value.code == "file_not_found"
    assert not (tmp_path / "typo.sqlite3").exists()
    assert not (tmp_path / "bk").exists() or not list((tmp_path / "bk").glob("*.sqlite3"))


def test_a_backup_does_not_migrate_an_older_database(tmp_path):
    path = tmp_path / "old.sqlite3"
    old_database(path, 12)
    r = maintenance.backup(path, tmp_path / "bk")
    assert r["schema_version"] == 12 and r["audit_recorded"] is False
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 12  # the source was only read
    conn.close()


def test_a_restore_is_refused_while_a_server_holds_the_database(tmp_path):
    db_path = tmp_path / "live.sqlite3"
    db = Database(db_path)
    AuthService(db).create_user("ada", PASSWORD, "admin", "")
    db.close()
    b = maintenance.backup(db_path, tmp_path / "bk")
    lock = serverlock.acquire(db_path)
    try:
        assert serverlock.held(db_path)
        with pytest.raises(serverlock.ServerRunning):
            serverlock.acquire(db_path)  # a second server on the same file
        with pytest.raises(maintenance.MaintenanceError) as e:
            maintenance.restore(b["file"], db_path)
        assert e.value.code == "server_running"
    finally:
        lock.close()
    assert not serverlock.held(db_path)
    assert maintenance.restore(b["file"], db_path)["audit_ok"]


def test_an_upload_without_a_length_is_stopped_at_the_limit():
    client = logged_in_client(make_app(max_upload=1000))

    def body():
        for _ in range(50):
            yield b"x" * 100

    r = client.post("/api/preview", content=body())
    assert r.status_code == 413 and err(r)["code"] == "file_too_large"


def test_a_workbook_that_unpacks_to_far_more_than_it_weighs_is_refused():
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", b"\0" * (40 * 1024 * 1024))  # 40 MB of zeros pack to a few kB
    with pytest.raises(DataImportError) as e:
        load_xlsx(raw.getvalue(), ColumnMap(value="x"))
    assert e.value.issues[0].code == "xlsx_too_large"


def test_a_search_that_would_run_for_hours_is_refused_with_a_reason():
    with pytest.raises(ValueError, match="too large"):
        O.d_optimal(8, "quadratic", 200, levels=3, starts=100)
    with pytest.raises(ValueError, match="too large"):
        O.mixture_d_optimal(8, "special_cubic", 200, m=6, starts=100)
    assert O.d_optimal(3, "main", 8)["d_efficiency"] == pytest.approx(1.0)  # the usual sizes are not touched


def test_logins_with_names_that_do_not_exist_do_not_fill_the_audit_trail():
    app = make_app()
    auth = app.state.auth
    before = auth.audit.verify()["entries"]
    for i in range(20):
        with pytest.raises(Exception):
            auth.authenticate(f"nobody{i}", "wrong password")
    assert auth.audit.verify()["entries"] == before
    for _ in range(5):  # a real account: every failure, and the lock, are on record
        with pytest.raises(Exception):
            auth.authenticate("eng", "wrong password")
    entries = [e for e in auth.audit.list(50) if e["action"] == "login_failed"]
    assert len(entries) == 5 and entries[0]["detail"]["locked"] is True
    for _ in range(10):  # tries during the lock are written once
        with pytest.raises(Exception):
            auth.authenticate("eng", "wrong password")
    assert len([e for e in auth.audit.list(50) if e["action"] == "login_blocked"]) == 1


def test_a_measurement_system_from_before_the_spread_setting_keeps_6_s():
    app = make_app()
    eng = logged_in_client(app, "eng")
    r = eng.post("/api/msa", json={"record": {"name": "old gauge", "characteristic": "bore", "unit": "mm", "kind": "variable", "resolution": 0.001, "tolerance": 1.0}})
    sid = r.json()["system"]["id"]
    db = app.state.db
    row = db.one("SELECT data FROM msa_systems WHERE id = ?", (sid,))
    data = json.loads(row["data"])
    data["policy"].pop("cg_spread")  # as stored by a version before the setting existed
    db.execute("UPDATE msa_systems SET data = ? WHERE id = ?", (json.dumps(data), sid))
    import numpy as np

    x = np.random.default_rng(1).normal(0, 1, 50)
    x = ((x - x.mean()) / x.std(ddof=1) * 0.0375).tolist()
    s = eng.post(f"/api/msa/{sid}/studies", json={"kind": "type1", "date": "2026-10-01", "input": {"values": x, "reference": 0.0}})
    res = s.json()["system"]["studies"][0]["result"]
    assert res["spread"] == 6 and res["cg"] == pytest.approx(0.8889, abs=1e-3)
    assert s.json()["gate"]["checks"]["type1"]["spread"] == 6

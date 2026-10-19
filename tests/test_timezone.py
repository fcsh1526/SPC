"""The zone of the screens (display_timezone): data stay in UTC, the screens get the plant's zone, a wrong zone stops the server,
a change of the zone is in the audit trail, and the IQ records it."""

import json

import pytest

from spc import timezone as tz
from spc.api import create_app
from spc.auth import Audit
from spc.db import Database
from tests.conftest import logged_in_client, make_app
from tests.test_monitor import CONFIG, OK_SAMPLE, PARAMS


def test_meta_gives_the_display_timezone():
    assert make_app().state.display_timezone == "UTC"
    assert create_app(display_timezone="UTC").state.display_timezone == "UTC"
    app = make_app()
    app.state.display_timezone = "Asia/Taipei"
    from fastapi.testclient import TestClient

    assert TestClient(app).get("/api/meta").json()["display_timezone"] == "Asia/Taipei"
    with pytest.raises(tz.InvalidTimezone):
        create_app(display_timezone="Mars/Base")


def test_an_unknown_zone_stops_the_server_before_it_starts(tmp_path):
    from spc.api.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["--db", str(tmp_path / "x.sqlite3"), "--display-timezone", "Mars/Base"])
    assert "Mars/Base" in str(exc.value)
    cfg = tmp_path / "spc.json"
    cfg.write_text(json.dumps({"db": str(tmp_path / "x.sqlite3"), "display_timezone": "Nowhere/Town"}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["--config", str(cfg)])
    assert "Nowhere/Town" in str(exc.value)


def test_times_are_stored_in_utc_whatever_the_display_zone():
    app = make_app()
    app.state.display_timezone = "Asia/Taipei"
    eng = logged_in_client(app, "eng")
    mid = eng.post("/api/monitors", json={"config": {**CONFIG, "first_article_approval": False}, "source": PARAMS}).json()["monitor"]["id"]
    eng.post(f"/api/monitors/{mid}/ack")
    assert eng.post(f"/api/monitors/{mid}/points", json={"values": OK_SAMPLE, "taken_at": "2026-10-01T10:00:00+08:00"}).status_code == 200
    assert eng.post(f"/api/monitors/{mid}/points", json={"values": OK_SAMPLE}).status_code == 200
    points = eng.get(f"/api/monitors/{mid}").json()["points"]
    assert points[0]["taken_at"] == "2026-10-01T02:00:00Z"
    assert points[1]["taken_at"].endswith("Z")
    assert logged_in_client(app, "admin").get("/api/audit/verify").json()["ok"]


def test_a_change_of_the_zone_is_written_into_the_audit_trail():
    db = Database()
    audit = Audit(db)
    assert tz.record_change(audit, "UTC") is True
    assert tz.record_change(audit, "UTC") is False  # a restart with the same zone writes nothing
    assert tz.record_change(audit, "Asia/Taipei") is True
    entries = [e for e in audit.list() if e["action"] == "display_timezone_set"]
    assert [e["detail"] for e in entries] == [{"from": "UTC", "to": "Asia/Taipei"}, {"from": None, "to": "UTC"}]
    assert audit.verify()["ok"]


def test_setup_writes_the_zone_and_refuses_an_unknown_one(tmp_path, monkeypatch):
    from spc.setup import main

    pw = tmp_path / "pw.txt"
    pw.write_text("Setup-Zone-2610!x\n", encoding="utf-8")
    assert main(["--data-dir", str(tmp_path / "a"), "--admin", "admin", "--password-file", str(pw), "--display-timezone", "Asia/Taipei"]) == 0
    assert json.loads((tmp_path / "a" / "spc.json").read_text(encoding="utf-8"))["display_timezone"] == "Asia/Taipei"
    pw.write_text("Setup-Zone-2610!x\n", encoding="utf-8")
    monkeypatch.setattr(tz, "detect", lambda: "Europe/Berlin")
    assert main(["--data-dir", str(tmp_path / "b"), "--admin", "admin", "--password-file", str(pw)]) == 0
    assert json.loads((tmp_path / "b" / "spc.json").read_text(encoding="utf-8"))["display_timezone"] == "Europe/Berlin"
    pw.write_text("Setup-Zone-2610!x\n", encoding="utf-8")
    assert main(["--data-dir", str(tmp_path / "c"), "--admin", "admin", "--password-file", str(pw), "--display-timezone", "Mars/Base"]) == 1


def test_detect_gives_a_known_zone():
    tz.validate(tz.detect())
    assert tz.validate(tz.WINDOWS_ZONES["Taipei Standard Time"]) == "Asia/Taipei"
    assert all(tz.validate(v) for v in tz.WINDOWS_ZONES.values())


def test_iq_records_the_display_timezone(tmp_path):
    from spc.qualification.iq import _display_timezone_check

    cfg = tmp_path / "spc.json"
    cfg.write_text(json.dumps({"display_timezone": "Asia/Taipei"}), encoding="utf-8")
    c = _display_timezone_check(cfg)
    assert c["id"] == "display_timezone" and c["actual"] == "Asia/Taipei" and c["status"] == "info"
    cfg.write_text(json.dumps({"display_timezone": "Mars/Base"}), encoding="utf-8")
    assert _display_timezone_check(cfg)["status"] == "fail"
    assert _display_timezone_check(tmp_path / "missing.json")["status"] == "info"

import json
import os
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from spc.api import create_app
from spc.api.app import PUBLIC
from spc.auth import AuthService, hash_password, verify_password
from spc.auth.passwords import PasswordPolicyError, check_policy
from spc.auth.service import IDLE_SECONDS, LOCK_SECONDS, MAX_AGE_SECONDS, MAX_FAILURES
from spc.db import Database
from tests.conftest import CHEAP, PASSWORD, login, logged_in_client, make_app
from tests.test_api import csv_text, upload


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def err(r):
    return r.json()["error"]


# ------------------------------------------------------------------ everything is protected by default

def test_every_api_route_needs_a_login_except_the_public_ones(app):
    client = TestClient(app)
    checked = 0
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path.startswith(("/api/docs", "/api/openapi")):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, path) in PUBLIC:
                continue
            url = path.replace("{key}", "x").replace("{rid}", "x").replace("{user_id}", "1")
            r = client.request(method, url)
            assert r.status_code == 401 and err(r)["code"] == "not_authenticated", (method, path, r.status_code)
            checked += 1
    assert checked > 20


def test_the_page_and_meta_are_public_and_say_whether_setup_is_needed(app):
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/api/meta").json()["setup_needed"] is False
    assert TestClient(create_app(Database())).get("/api/meta").json()["setup_needed"] is True


def test_meta_shows_the_session_only_to_a_signed_in_browser(app):
    assert TestClient(app).get("/api/meta").json()["session"] is None
    client = logged_in_client(app)
    session = client.get("/api/meta").json()["session"]
    assert session["user"]["username"] == "eng" and session["csrf"] == client.headers["X-CSRF-Token"]


def test_api_answers_are_not_cached(app):
    assert logged_in_client(app).get("/api/datasets").headers["cache-control"] == "no-store"


# ------------------------------------------------------------------ login

def test_login_sets_a_http_only_strict_cookie_and_gives_a_csrf_token(app):
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"username": "eng", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["user"]["role"] == "engineer" and r.json()["csrf"]
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie
    assert client.get("/api/auth/me").json()["user"]["username"] == "eng"


def test_secure_flag_follows_the_setting(app):
    app = make_app(secure_cookies=True)
    r = TestClient(app).post("/api/auth/login", json={"username": "eng", "password": PASSWORD})
    assert "secure" in r.headers["set-cookie"].lower()


def test_wrong_password_unknown_name_and_disabled_user_look_the_same(app):
    client = TestClient(app)
    for name, pw in (("eng", "wrong password!"), ("nobody", PASSWORD)):
        r = client.post("/api/auth/login", json={"username": name, "password": pw})
        assert r.status_code == 401 and err(r)["code"] == "invalid_credentials"
    admin = logged_in_client(app, "admin")
    uid = next(u["id"] for u in admin.get("/api/users").json()["users"] if u["username"] == "view")
    admin.patch(f"/api/users/{uid}", json={"active": False})
    r = client.post("/api/auth/login", json={"username": "view", "password": PASSWORD})
    assert r.status_code == 401 and err(r)["code"] == "invalid_credentials"


def test_names_are_case_insensitive(app):
    assert TestClient(app).post("/api/auth/login", json={"username": "ENG", "password": PASSWORD}).status_code == 200


def test_lockout_after_repeated_failures_and_release_after_the_wait():
    clock = Clock()
    app = make_app(clock=clock)
    client = TestClient(app)
    bad = {"username": "eng", "password": "nope nope nope"}
    for _ in range(MAX_FAILURES):
        assert client.post("/api/auth/login", json=bad).status_code == 401
    r = client.post("/api/auth/login", json={"username": "eng", "password": PASSWORD})  # right password, still locked
    assert r.status_code == 429 and err(r)["code"] == "login_locked" and 0 < err(r)["params"]["retry_after"] <= LOCK_SECONDS
    clock.now += LOCK_SECONDS + 1
    assert client.post("/api/auth/login", json={"username": "eng", "password": PASSWORD}).status_code == 200


def test_unknown_names_are_locked_too_so_the_answer_does_not_tell_which_exist():
    app = make_app()
    client = TestClient(app)
    for _ in range(MAX_FAILURES):
        client.post("/api/auth/login", json={"username": "ghost", "password": "nope nope nope"})
    assert err(client.post("/api/auth/login", json={"username": "ghost", "password": "x"}))["code"] == "login_locked"


def test_admin_can_unlock(app):
    client = TestClient(app)
    for _ in range(MAX_FAILURES):
        client.post("/api/auth/login", json={"username": "view", "password": "nope nope nope"})
    admin = logged_in_client(app, "admin")
    uid = next(u["id"] for u in admin.get("/api/users").json()["users"] if u["username"] == "view")
    assert admin.post(f"/api/users/{uid}/unlock").status_code == 200
    assert client.post("/api/auth/login", json={"username": "view", "password": PASSWORD}).status_code == 200


def test_a_good_login_resets_the_failure_count():
    app = make_app()
    client = TestClient(app)
    for _ in range(MAX_FAILURES - 1):
        client.post("/api/auth/login", json={"username": "eng", "password": "nope nope nope"})
    assert client.post("/api/auth/login", json={"username": "eng", "password": PASSWORD}).status_code == 200
    for _ in range(MAX_FAILURES - 1):
        assert client.post("/api/auth/login", json={"username": "eng", "password": "nope nope nope"}).status_code == 401


# ------------------------------------------------------------------ sessions and csrf

def test_logout_ends_the_session(app):
    client = logged_in_client(app)
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_changing_state_needs_the_csrf_token(app):
    client = logged_in_client(app)
    token = client.headers.pop("X-CSRF-Token")
    r = client.post("/api/datasets", params={"value": "diameter"}, content=csv_text())
    assert r.status_code == 403 and err(r)["code"] == "csrf_failed"
    client.headers["X-CSRF-Token"] = "wrong"
    assert client.post("/api/datasets", params={"value": "diameter"}, content=csv_text()).status_code == 403
    client.headers["X-CSRF-Token"] = token
    assert client.post("/api/datasets", params={"value": "diameter"}, content=csv_text()).status_code == 200
    assert client.get("/api/datasets").status_code == 200  # reading needs no token


def test_idle_and_maximum_age_end_a_session():
    clock = Clock()
    app = make_app(clock=clock)
    client = logged_in_client(app)
    clock.now += IDLE_SECONDS - 10
    assert client.get("/api/auth/me").status_code == 200  # activity keeps it alive
    clock.now += IDLE_SECONDS - 10
    assert client.get("/api/auth/me").status_code == 200
    clock.now += IDLE_SECONDS + 10
    assert client.get("/api/auth/me").status_code == 401
    client = logged_in_client(app)
    for _ in range(MAX_AGE_SECONDS // (IDLE_SECONDS // 2) + 2):
        clock.now += IDLE_SECONDS // 2
        client.get("/api/auth/me")
    assert client.get("/api/auth/me").status_code == 401  # active the whole time, still ends at the maximum age


def test_the_database_keeps_only_a_hash_of_the_session_token(app):
    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "eng", "password": PASSWORD})
    token = client.cookies.get("spc_session")
    rows = app.state.db.all("SELECT token_hash FROM sessions")
    assert rows and all(token not in r["token_hash"] for r in rows)


def test_a_new_login_replaces_the_old_session_cookie(app):
    client = TestClient(app)
    login(client)
    first = client.cookies.get("spc_session")
    login(client)
    assert client.cookies.get("spc_session") != first
    other = TestClient(app)
    other.cookies.set("spc_session", first)
    assert other.get("/api/auth/me").status_code == 401


# ------------------------------------------------------------------ roles

def test_viewer_can_read_and_analyse_but_not_change(app):
    owner = logged_in_client(app, "eng")
    ds = upload(owner).json()
    viewer = logged_in_client(app, "view")
    base = f"/api/datasets/{ds['id']}"
    assert viewer.get(base).status_code == 200
    assert viewer.get(f"{base}/rows").status_code == 200
    assert viewer.get("/api/datasets").json()["datasets"][0]["id"] == ds["id"]
    assert viewer.post(f"{base}/analyze", json={}).status_code == 200
    assert viewer.post("/api/targets", json={"stage": "machine", "characteristic_class": "major", "n": 30}).status_code == 200
    for r in (
        viewer.post("/api/datasets", params={"value": "diameter"}, content=csv_text()),
        viewer.post("/api/preview", content=csv_text()),
        viewer.post(f"{base}/invalid", json={"positions": [1], "reason": "r"}),
        viewer.post(f"{base}/restore", json={"positions": [1], "reason": "r"}),
        viewer.post(f"{base}/reports", json={"analysis": {"lsl": 9, "usl": 11}}),
        viewer.delete(base),
    ):
        assert r.status_code == 403 and err(r)["code"] == "forbidden"


def test_only_an_admin_manages_users_and_reads_the_audit_trail(app):
    for name in ("eng", "view"):
        c = logged_in_client(app, name)
        assert c.get("/api/users").status_code == 403
        assert c.post("/api/users", json={"username": "x", "password": PASSWORD}).status_code == 403
        assert c.get("/api/audit").status_code == 403
        assert c.get("/api/audit/verify").status_code == 403


def test_deleting_a_dataset_needs_to_be_its_owner_or_admin(app):
    mine = logged_in_client(app, "eng")
    ds = upload(mine).json()
    admin = logged_in_client(app, "admin")
    other = TestClient(app)
    admin.post("/api/users", json={"username": "eng2", "password": PASSWORD, "role": "engineer", "must_change": False})
    login(other, "eng2")
    assert other.delete(f"/api/datasets/{ds['id']}").status_code == 403
    assert mine.delete(f"/api/datasets/{ds['id']}").status_code == 200
    ds2 = upload(mine).json()
    assert admin.delete(f"/api/datasets/{ds2['id']}").status_code == 200
    assert mine.get(f"/api/datasets/{ds2['id']}").status_code == 404


# ------------------------------------------------------------------ passwords and accounts

def test_password_hash_is_salted_and_verifies():
    a, b = hash_password("a long enough secret", CHEAP), hash_password("a long enough secret", CHEAP)
    assert a != b and a.startswith("scrypt$") and "a long enough secret" not in a
    assert verify_password("a long enough secret", a) and not verify_password("A long enough secret", a)
    assert not verify_password("x", "garbage") and not verify_password("x", "scrypt$1$2$3$4$5")


@pytest.mark.parametrize("pw,code", [("short", "password_too_short"), ("x" * 300, "password_too_long"),
                                     ("aaaaaaaaaaaa", "password_too_simple")])
def test_password_policy(pw, code):
    with pytest.raises(PasswordPolicyError) as exc:
        check_policy(pw, "someone")
    assert exc.value.code == code


def test_password_may_not_be_the_user_name():
    with pytest.raises(PasswordPolicyError) as exc:
        check_policy("alice.engineer", "Alice.Engineer")
    assert exc.value.code == "password_is_username"


def test_admin_creates_a_user_who_must_change_the_password_first(app):
    admin = logged_in_client(app, "admin")
    r = admin.post("/api/users", json={"username": "new.user", "password": "temporary-pass-1", "role": "engineer", "display_name": "N. User"})
    assert r.status_code == 200 and r.json()["user"]["must_change"] is True
    assert err(admin.post("/api/users", json={"username": "NEW.user", "password": "temporary-pass-2"}))["code"] == "username_taken"
    assert err(admin.post("/api/users", json={"username": "a b", "password": "temporary-pass-2"}))["code"] == "username_invalid"
    assert err(admin.post("/api/users", json={"username": "okname", "password": "short"}))["code"] == "password_too_short"

    user = TestClient(app)
    login(user, "new.user", "temporary-pass-1")
    assert user.get("/api/auth/me").json()["user"]["must_change"] is True
    blocked = user.get("/api/datasets")
    assert blocked.status_code == 403 and err(blocked)["code"] == "password_change_required"
    assert err(user.post("/api/auth/password", json={"current": "wrong", "new": "another-good-pass"}))["code"] == "wrong_current_password"
    assert err(user.post("/api/auth/password", json={"current": "temporary-pass-1", "new": "temporary-pass-1"}))["code"] == "password_unchanged"
    assert user.post("/api/auth/password", json={"current": "temporary-pass-1", "new": "another-good-pass"}).status_code == 200
    assert user.get("/api/datasets").status_code == 200
    assert TestClient(app).post("/api/auth/login", json={"username": "new.user", "password": "temporary-pass-1"}).status_code == 401


def test_changing_the_password_ends_the_other_sessions_of_that_user(app):
    a, b = TestClient(app), TestClient(app)
    login(a)
    login(b)
    assert a.post("/api/auth/password", json={"current": PASSWORD, "new": "a brand new password"}).status_code == 200
    assert a.get("/api/auth/me").status_code == 200 and b.get("/api/auth/me").status_code == 401


def test_changing_role_or_disabling_ends_that_users_sessions_and_the_last_admin_stays(app):
    admin = logged_in_client(app, "admin")
    eng = logged_in_client(app, "eng")
    users = {u["username"]: u["id"] for u in admin.get("/api/users").json()["users"]}
    assert admin.patch(f"/api/users/{users['eng']}", json={"role": "viewer"}).json()["user"]["role"] == "viewer"
    assert eng.get("/api/auth/me").status_code == 401
    r = admin.patch(f"/api/users/{users['admin']}", json={"active": False})
    assert r.status_code == 409 and err(r)["code"] == "last_admin"
    assert err(admin.patch(f"/api/users/{users['admin']}", json={"role": "engineer"}))["code"] == "last_admin"
    assert admin.patch("/api/users/9999", json={"active": False}).status_code == 404


def test_admin_resets_a_password(app):
    admin = logged_in_client(app, "admin")
    uid = next(u["id"] for u in admin.get("/api/users").json()["users"] if u["username"] == "eng")
    eng = logged_in_client(app, "eng")
    assert admin.post(f"/api/users/{uid}/password", json={"password": "reset-by-admin-1"}).status_code == 200
    assert eng.get("/api/auth/me").status_code == 401
    other = TestClient(app)
    login(other, "eng", "reset-by-admin-1")
    assert other.get("/api/auth/me").json()["user"]["must_change"] is True


# ------------------------------------------------------------------ traceability

def test_marks_are_written_under_the_login_and_in_the_audit_trail(app):
    eng = logged_in_client(app, "eng")
    ds = upload(eng).json()
    eng.post(f"/api/datasets/{ds['id']}/invalid", json={"positions": [3, 4], "reason": "wrong part"})
    eng.post(f"/api/datasets/{ds['id']}/restore", json={"positions": [4], "reason": "it was fine"})
    log = eng.get(f"/api/datasets/{ds['id']}").json()["log"]
    assert [e["by"] for e in log] == ["Eva Engineer (eng)"] * 2

    entries = logged_in_client(app, "admin").get("/api/audit").json()["entries"]
    actions = [e["action"] for e in entries]
    assert {"login", "dataset_created", "dataset_marked_invalid", "dataset_restored"} <= set(actions)
    mark = next(e for e in entries if e["action"] == "dataset_marked_invalid")
    assert mark["username"] == "eng" and mark["target"] == ds["id"] and mark["detail"]["reason"] == "wrong part"
    assert mark["detail"]["positions"] == [3, 4]


def test_failed_logins_and_user_changes_are_in_the_audit_trail(app):
    TestClient(app).post("/api/auth/login", json={"username": "eng", "password": "nope nope nope"})
    admin = logged_in_client(app, "admin")
    admin.post("/api/users", json={"username": "someone", "password": "temporary-pass-1"})
    actions = [e["action"] for e in admin.get("/api/audit").json()["entries"]]
    assert "login_failed" in actions and actions.count("user_created") == 4  # three fixtures and one more
    assert "temporary-pass-1" not in json.dumps(admin.get("/api/audit").json())


def test_the_audit_chain_shows_a_change():
    app = make_app()
    admin = logged_in_client(app, "admin")
    upload(logged_in_client(app, "eng"))
    ok = admin.get("/api/audit/verify").json()
    assert ok["ok"] is True and ok["entries"] > 5 and len(ok["last_hash"]) == 64

    db = app.state.db
    victim = db.one("SELECT id FROM audit WHERE action = 'dataset_created'")["id"]
    db.execute("UPDATE audit SET username = 'somebody else' WHERE id = ?", (victim,))
    bad = admin.get("/api/audit/verify").json()
    assert bad["ok"] is False and bad["broken_at"] == victim

    db.execute("UPDATE audit SET username = 'eng' WHERE id = ?", (victim,))
    assert admin.get("/api/audit/verify").json()["ok"] is True
    first = db.one("SELECT id FROM audit ORDER BY id LIMIT 1")["id"]
    db.execute("DELETE FROM audit WHERE id = ?", (first,))  # removing an entry from the middle breaks the chain too
    assert admin.get("/api/audit/verify").json()["ok"] is False


def test_removing_the_newest_entries_is_only_seen_against_a_hash_kept_elsewhere():
    """Documented limit: the chain cannot show that its tail was cut. Keep the last hash outside the database."""
    app = make_app()
    admin = logged_in_client(app, "admin")
    upload(logged_in_client(app, "eng"))
    kept = admin.get("/api/audit/verify").json()
    app.state.db.execute("DELETE FROM audit WHERE id = (SELECT MAX(id) FROM audit)")
    now = admin.get("/api/audit/verify").json()
    assert now["ok"] is True and now["last_hash"] != kept["last_hash"] and now["entries"] == kept["entries"] - 1


def test_the_report_archive_names_the_login_that_made_it(app):
    eng = logged_in_client(app, "eng")
    ds = upload(eng).json()
    rid = eng.post(f"/api/datasets/{ds['id']}/reports", json={"analysis": {"lsl": 9.5, "usl": 10.5}}).json()["id"]
    archive = eng.get(f"/api/reports/{rid}/archive.json").json()
    assert archive["created_by"] == "Eva Engineer (eng)"
    assert eng.post("/api/archive/check", content=json.dumps(archive)).json()["integrity_ok"] is True


# ------------------------------------------------------------------ the database

def test_data_marks_and_reports_survive_a_restart(tmp_path):
    path = tmp_path / "spc.sqlite3"
    app = make_app(Database(path))
    eng = logged_in_client(app, "eng")
    ds = upload(eng).json()
    eng.post(f"/api/datasets/{ds['id']}/invalid", json={"positions": [3], "reason": "wrong part"})
    report = eng.post(f"/api/datasets/{ds['id']}/reports", json={"analysis": {"lsl": 9.5, "usl": 10.5}}).json()
    html_before = eng.get(report["urls"]["html"]).text
    app.state.db.close()

    app2 = make_app(Database(path))
    again = TestClient(app2)
    assert again.get("/api/datasets").status_code == 401  # the old cookie is not carried over by a new client
    login(again, "eng")
    got = again.get(f"/api/datasets/{ds['id']}").json()
    assert got["summary"]["n_total"] == 125 and got["summary"]["n_invalid"] == 1
    assert again.get(report["urls"]["html"]).text == html_before
    assert again.post("/api/archive/check", content=again.get(report["urls"]["archive"]).content).json()["reproduced"] is True
    assert [d["id"] for d in again.get("/api/datasets").json()["datasets"]] == [ds["id"]]
    assert [r["id"] for r in again.get("/api/reports").json()["reports"]] == [report["id"]]
    assert logged_in_client(app2, "admin").get("/api/audit/verify").json()["ok"] is True


def test_sessions_also_survive_a_restart(tmp_path):
    path = tmp_path / "spc.sqlite3"
    app = make_app(Database(path))
    client = logged_in_client(app, "eng")
    cookie = client.cookies.get("spc_session")
    app.state.db.close()
    again = TestClient(make_app(Database(path)))
    again.cookies.set("spc_session", cookie)
    assert again.get("/api/auth/me").status_code == 200


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_the_database_file_is_private(tmp_path):
    Database(tmp_path / "x.sqlite3")
    assert oct((tmp_path / "x.sqlite3").stat().st_mode & 0o777) == "0o600"


def test_schema_version_is_checked(tmp_path):
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError):
        Database(path)


def test_two_people_marking_the_same_value_at_once_cannot_both_succeed(tmp_path):
    app = make_app(Database(tmp_path / "c.sqlite3"))
    eng = logged_in_client(app, "eng")
    ds = upload(eng).json()
    admin = logged_in_client(app, "admin")
    results, barrier = [], threading.Barrier(2)

    def mark(client):
        barrier.wait()
        results.append(client.post(f"/api/datasets/{ds['id']}/invalid", json={"positions": [7], "reason": "r"}).status_code)

    threads = [threading.Thread(target=mark, args=(c,)) for c in (eng, admin)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [200, 409]
    assert len(eng.get(f"/api/datasets/{ds['id']}").json()["log"]) == 1


def test_a_failed_change_leaves_data_and_audit_trail_unchanged(app):
    eng = logged_in_client(app, "eng")
    ds = upload(eng).json()
    before = app.state.db.one("SELECT COUNT(*) AS n FROM audit")["n"]
    assert eng.post(f"/api/datasets/{ds['id']}/invalid", json={"positions": [9999], "reason": "r"}).status_code == 400
    assert app.state.db.one("SELECT COUNT(*) AS n FROM audit")["n"] == before
    assert app.state.db.one("SELECT revision FROM datasets")["revision"] == 1

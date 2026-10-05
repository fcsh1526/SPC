"""Shared helpers: an app with one user per role. Passwords are hashed with a cheap scrypt cost in tests."""

import pytest
from fastapi.testclient import TestClient

from spc.api import create_app
from spc.auth import AuthService
from spc.db import Database

PASSWORD = "correct horse battery"
CHEAP = {"n": 2**4, "r": 1, "p": 1}


def make_app(db=None, max_upload=200_000, clock=None, **kwargs):
    """Extra keyword arguments go to create_app (secure_cookies, notifiers)."""
    db = Database() if db is None else db
    auth = AuthService(db, cost=CHEAP, **({"clock": clock} if clock else {}))
    existing = {u.username for u in auth.list_users()}
    for name, role, display in (("admin", "admin", "Ada Admin"), ("eng", "engineer", "Eva Engineer"), ("view", "viewer", "")):
        if name not in existing:
            auth.create_user(name, PASSWORD, role, display)
    return create_app(db, max_upload=max_upload, auth=auth, **kwargs)


def login(client: TestClient, username="eng", password=PASSWORD) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r.json()


def logged_in_client(app, username="eng") -> TestClient:
    client = TestClient(app)
    login(client, username)
    return client


@pytest.fixture
def app():
    return make_app()

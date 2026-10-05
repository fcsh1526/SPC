"""Users, roles, sessions and login throttling.

Roles:  viewer   reads datasets, analyses, reports and the SPC monitors
        operator viewer + works at the line: enters measurements into SPC monitors and follows the out-of-control action plan
        engineer operator + imports data, marks values, makes reports, sets up monitors, deletes own datasets
        admin    engineer + manages users, reads the audit trail, deletes any dataset

Sessions: a random token in an HttpOnly cookie. The database keeps only its SHA-256, so a copy of the
database cannot be used to log in. A session ends after an idle time or a maximum age. Every state-
changing request must carry the session's CSRF token.
Throttling: after MAX_FAILURES wrong passwords for a name the name is locked for LOCK_SECONDS. The
count is kept per typed name whether or not the user exists, so the answer does not tell which names exist.
Every answer to a failed login is the same `invalid_credentials`, and an unknown name costs the same time.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from spc.auth.audit import Audit
from spc.auth.passwords import DEFAULT_COST, check_policy, hash_password, verify_password
from spc.db.database import Database
from spc.db.stores import now_iso

ROLES = ("viewer", "operator", "engineer", "admin")
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
IDLE_SECONDS = 2 * 60 * 60
MAX_AGE_SECONDS = 12 * 60 * 60
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{3,64}$")


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str = "", **params):
        super().__init__(message or code)
        self.status, self.code, self.message, self.params = status, code, message or code, params


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str
    active: bool
    must_change: bool

    @property
    def label(self) -> str:
        """The name written into marks and audit entries."""
        return f"{self.display_name} ({self.username})" if self.display_name else self.username

    def can(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)

    def to_json(self) -> dict:
        return {"id": self.id, "username": self.username, "display_name": self.display_name, "role": self.role,
                "active": self.active, "must_change": self.must_change}


@dataclass(frozen=True)
class SessionInfo:
    user: User
    csrf: str
    token_hash: str


def _user(row) -> User:
    return User(row["id"], row["username"], row["display_name"], row["role"], bool(row["active"]), bool(row["must_change"]))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


class AuthService:
    def __init__(self, db: Database, audit: Audit | None = None, clock: Callable[[], float] = time.time, cost: dict | None = None):
        self.db = db
        self.audit = audit or Audit(db)
        self.clock = clock
        self.cost = cost or DEFAULT_COST
        self._dummy = hash_password("not-a-real-password", self.cost)  # same work for names that do not exist

    # ------------------------------------------------------------------ users

    def user_count(self) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM users")["n"]

    def get_user(self, user_id: int) -> User:
        row = self.db.one("SELECT * FROM users WHERE id = ?", (user_id,))
        if row is None:
            raise AuthError(404, "user_not_found")
        return _user(row)

    def list_users(self) -> list[User]:
        return [_user(r) for r in self.db.all("SELECT * FROM users ORDER BY username")]

    def create_user(self, username: str, password: str, role: str, display_name: str = "", *,
                    must_change: bool = False, actor: User | None = None) -> User:
        username = username.strip()
        if not USERNAME_RE.match(username):
            raise AuthError(400, "username_invalid")
        if role not in ROLES:
            raise AuthError(400, "role_invalid")
        self._check_password(password, username)
        stamp = now_iso()
        with self.db.tx():
            if self.db.one("SELECT 1 FROM users WHERE username = ?", (username,)):
                raise AuthError(409, "username_taken")
            cur = self.db.execute(
                "INSERT INTO users (username, display_name, password_hash, role, active, must_change, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
                (username, display_name.strip()[:200], hash_password(password, self.cost), role, int(must_change), stamp, stamp),
            )
            user = self.get_user(cur.lastrowid)
            self._log("user_created", actor, user.username, {"role": role})
        return user

    def update_user(self, user_id: int, *, role: str | None = None, active: bool | None = None,
                    display_name: str | None = None, actor: User | None = None) -> User:
        with self.db.tx():
            user = self.get_user(user_id)
            new_role = user.role if role is None else role
            new_active = user.active if active is None else active
            if new_role not in ROLES:
                raise AuthError(400, "role_invalid")
            if user.role == "admin" and user.active and (new_role != "admin" or not new_active):
                others = self.db.one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1 AND id != ?", (user_id,))["n"]
                if others == 0:
                    raise AuthError(409, "last_admin")
            name = user.display_name if display_name is None else display_name.strip()[:200]
            self.db.execute("UPDATE users SET role = ?, active = ?, display_name = ?, updated_at = ? WHERE id = ?",
                            (new_role, int(new_active), name, now_iso(), user_id))
            if new_role != user.role or new_active != user.active:
                self.revoke_all(user_id)  # the new rights apply from the next login
            self._log("user_updated", actor, user.username,
                      {"role": new_role, "active": new_active, "display_name": name})
            return self.get_user(user_id)

    def reset_password(self, user_id: int, new_password: str, *, must_change: bool = True, actor: User | None = None) -> User:
        user = self.get_user(user_id)
        self._check_password(new_password, user.username)
        with self.db.tx():
            self.db.execute("UPDATE users SET password_hash = ?, must_change = ?, updated_at = ? WHERE id = ?",
                            (hash_password(new_password, self.cost), int(must_change), now_iso(), user_id))
            self.revoke_all(user_id)
            self.unlock(user.username)
            self._log("password_reset", actor, user.username, {})
        return self.get_user(user_id)

    def change_password(self, user: User, current: str, new: str, keep: str | None = None) -> User:
        row = self.db.one("SELECT password_hash FROM users WHERE id = ?", (user.id,))
        if row is None or not verify_password(current, row["password_hash"]):
            self._log("password_change_failed", user, user.username, {})
            raise AuthError(403, "wrong_current_password")
        if current == new:
            raise AuthError(400, "password_unchanged")
        self._check_password(new, user.username)
        with self.db.tx():
            self.db.execute("UPDATE users SET password_hash = ?, must_change = 0, updated_at = ? WHERE id = ?",
                            (hash_password(new, self.cost), now_iso(), user.id))
            self.revoke_all(user.id, keep=keep)
            self._log("password_changed", user, user.username, {})
        return self.get_user(user.id)

    def _check_password(self, password: str, username: str) -> None:
        from spc.auth.passwords import PasswordPolicyError

        try:
            check_policy(password, username)
        except PasswordPolicyError as exc:
            raise AuthError(400, exc.code, **exc.params) from None

    # ------------------------------------------------------------------ login

    def _retry_after(self, key: str) -> int:
        row = self.db.one("SELECT locked_until FROM login_attempts WHERE username = ?", (key,))
        return max(0, int((row["locked_until"] if row else 0) - self.clock() + 0.999))

    def unlock(self, username: str) -> None:
        self.db.execute("DELETE FROM login_attempts WHERE username = ?", (username.strip().lower(),))

    def authenticate(self, username: str, password: str) -> User:
        key = username.strip().lower()[:64]
        wait = self._retry_after(key)
        if wait:
            self._log("login_blocked", None, key, {})
            raise AuthError(429, "login_locked", retry_after=wait)
        row = self.db.one("SELECT * FROM users WHERE username = ?", (username.strip(),))
        ok = verify_password(password, row["password_hash"] if row else self._dummy)
        if not (ok and row is not None and row["active"]):
            self._fail(key)
            raise AuthError(401, "invalid_credentials")
        self.unlock(key)
        user = _user(row)
        self._log("login", user, user.username, {})
        return user

    def _fail(self, key: str) -> None:
        with self.db.tx():
            row = self.db.one("SELECT failures FROM login_attempts WHERE username = ?", (key,))
            failures = (row["failures"] if row else 0) + 1
            locked = self.clock() + LOCK_SECONDS if failures >= MAX_FAILURES else 0
            if locked:
                failures = 0
            self.db.execute(
                "INSERT INTO login_attempts (username, failures, locked_until) VALUES (?, ?, ?)"
                " ON CONFLICT(username) DO UPDATE SET failures = excluded.failures, locked_until = excluded.locked_until",
                (key, failures, locked),
            )
            self._log("login_failed", None, key, {"locked": bool(locked)})

    # ------------------------------------------------------------------ sessions

    def create_session(self, user: User) -> tuple[str, str]:
        """(token for the cookie, csrf token)"""
        token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.clock()
        with self.db.tx():
            self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            self.db.execute("INSERT INTO sessions (token_hash, user_id, csrf, created_at, last_seen, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                            (_hash_token(token), user.id, csrf, now, now, now + MAX_AGE_SECONDS))
        return token, csrf

    def session(self, token: str | None) -> SessionInfo | None:
        if not token:
            return None
        h, now = _hash_token(token), self.clock()
        row = self.db.one(
            "SELECT s.csrf, s.last_seen, s.expires_at, u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (h,))
        if row is None:
            return None
        if row["expires_at"] < now or row["last_seen"] + IDLE_SECONDS < now or not row["active"]:
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (h,))
            return None
        if now - row["last_seen"] > 60:
            self.db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, h))
        return SessionInfo(_user(row), row["csrf"], h)

    def revoke(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))

    def revoke_all(self, user_id: int, keep: str | None = None) -> None:
        """End all sessions of a user, except the one with this token hash."""
        self.db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, keep or ""))

    # ------------------------------------------------------------------ audit

    def _log(self, action: str, actor: User | None, target: str, detail: dict) -> None:
        self.audit.append(action, user_id=actor.id if actor else None, username=actor.username if actor else "", target=target, detail=detail)

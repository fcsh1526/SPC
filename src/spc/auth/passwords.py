"""Password hashing with scrypt (standard library). The cost parameters are stored inside the hash,
so they can be raised later and old hashes still verify."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

MIN_LENGTH = 10
MAX_LENGTH = 256
DEFAULT_COST = {"n": 2**15, "r": 8, "p": 3}


class PasswordPolicyError(ValueError):
    def __init__(self, code: str, **params):
        super().__init__(code)
        self.code, self.params = code, params


def check_policy(password: str, username: str = "") -> None:
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError("password_too_short", min=MIN_LENGTH)
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError("password_too_long", max=MAX_LENGTH)
    if username and password.lower() == username.lower():
        raise PasswordPolicyError("password_is_username")
    if len(set(password)) < 4:
        raise PasswordPolicyError("password_too_simple")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=256 * 1024 * 1024)


def hash_password(password: str, cost: dict | None = None) -> str:
    c = cost or DEFAULT_COST
    salt = os.urandom(16)
    digest = _derive(password, salt, c["n"], c["r"], c["p"])
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return f"scrypt${c['n']}${c['r']}${c['p']}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = _derive(password, base64.b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)

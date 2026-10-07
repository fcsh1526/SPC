"""Audit trail: an append-only list of who did what, linked by a hash chain.

Each entry holds the SHA-256 of the previous entry and of its own content. Changing or removing an
entry breaks the chain from that point, and `verify` finds the first broken entry. This shows a
change. It does not stop someone who can write to the database file and rewrites the whole chain.
For that, copy the last hash somewhere else (a ticket, a signed mail) from time to time.
"""

from __future__ import annotations

import hashlib
import json

from spc.db.database import Database
from spc.db.stores import now_iso

GENESIS = "0" * 64


def _digest(prev: str, entry: dict) -> str:
    body = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()


class Audit:
    def __init__(self, db: Database):
        self.db = db

    def append(self, action: str, *, user_id: int | None = None, username: str = "", target: str = "", detail: dict | None = None) -> None:
        entry = {
            "ts": now_iso(), "user_id": user_id, "username": username, "action": action,
            "target": target, "detail": json.dumps(detail or {}, sort_keys=True, ensure_ascii=False),
        }
        with self.db.tx():
            last = self.db.one("SELECT hash FROM audit ORDER BY id DESC LIMIT 1")
            prev = last["hash"] if last else GENESIS
            self.db.execute(
                "INSERT INTO audit (ts, user_id, username, action, target, detail, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (entry["ts"], user_id, username, action, target, entry["detail"], prev, _digest(prev, entry)),
            )

    def list(self, limit: int = 100, before: int | None = None) -> list[dict]:
        rows = self.db.all(
            "SELECT id, ts, username, action, target, detail, hash FROM audit WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
            (before, before, limit),
        )
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def verify(self) -> dict:
        """{'ok': bool, 'entries': n, 'last_hash': str, 'broken_at': id or None}"""
        prev, n = GENESIS, 0
        for r in self.db.all("SELECT * FROM audit ORDER BY id"):
            entry = {k: r[k] for k in ("ts", "user_id", "username", "action", "target", "detail")}
            if r["prev_hash"] != prev or r["hash"] != _digest(prev, entry):
                return {"ok": False, "entries": n, "last_hash": prev, "broken_at": r["id"]}
            prev, n = r["hash"], n + 1
        return {"ok": True, "entries": n, "last_hash": prev, "broken_at": None}

    def check_anchor(self, entries: int, last_hash: str) -> dict:
        """Is the chain still the one that had `entries` entries and the hash `last_hash` at the end, which somebody kept outside the database?
        This is what shows that the newest entries were cut: the chain alone cannot (the shorter chain is a chain too).
        {'ok': bool, 'reason': 'intact' | 'chain_broken' | 'shorter' | 'hash_differs', 'entries_now': n, 'broken_at': id or None}"""
        now = self.verify()
        if not now["ok"]:
            return {"ok": False, "reason": "chain_broken", "entries_now": now["entries"], "broken_at": now["broken_at"]}
        if entries == 0:
            return {"ok": last_hash == GENESIS, "reason": "intact" if last_hash == GENESIS else "hash_differs", "entries_now": now["entries"], "broken_at": None}
        row = self.db.one("SELECT hash FROM audit ORDER BY id LIMIT 1 OFFSET ?", (entries - 1,))
        if row is None:
            return {"ok": False, "reason": "shorter", "entries_now": now["entries"], "broken_at": None}
        same = row["hash"] == last_hash
        return {"ok": same, "reason": "intact" if same else "hash_differs", "entries_now": now["entries"], "broken_at": None}

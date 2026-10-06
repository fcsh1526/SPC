"""From a reading of the machine to a point of a monitor.

Every reading passes guards first. A reading that fails is counted with its reason and never reaches the chart: bad or uncertain quality
(OPC UA status code), a value that is not a finite number, no source timestamp, a timestamp that is not newer than the last one of the node
(a repeat after a reconnect, or an old value), too old (stale_after_s) or far in the future. Good readings of one node are collected until the
subgroup of the monitor is full; then one point is entered, as the person who enabled the link, with the tags source=opcua and link=name.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from spc.db.database import Database
from spc.db.stores import now_iso
from spc.monitor.model import MonitorError

FUTURE_SLACK = timedelta(minutes=5)
REASONS = ("bad_quality", "not_numeric", "no_timestamp", "old_or_repeated", "stale", "future", "not_active")


def _dump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class LinkStore:
    """The record of a link in the database, changed one writer at a time."""

    def __init__(self, db: Database):
        self.db = db
        self.lock = threading.RLock()

    def get(self, link_id: int) -> dict | None:
        r = self.db.one("SELECT * FROM equipment_links WHERE id = ?", (link_id,))
        return None if r is None else {"id": r["id"], "created_at": r["created_at"], "updated_at": r["updated_at"], **json.loads(r["data"])}

    def save(self, link_id: int, data: dict) -> None:
        payload = {k: v for k, v in data.items() if k not in ("id", "created_at", "updated_at")}
        self.db.execute("UPDATE equipment_links SET data = ?, name = ?, updated_at = ? WHERE id = ?", (_dump(payload), payload["name"], now_iso(), link_id))

    def all(self) -> list[dict]:
        return [{"id": r["id"], "created_at": r["created_at"], "updated_at": r["updated_at"], **json.loads(r["data"])}
                for r in self.db.all("SELECT * FROM equipment_links ORDER BY name COLLATE NOCASE")]


def empty_node_state() -> dict:
    return {"last_time": None, "last_value": None, "accepted": 0, "points": 0, "rejected": {}, "buffer": [], "last_error": None}


class Ingest:
    def __init__(self, store: LinkStore, monitors, auth):
        self.store, self.monitors, self.auth = store, monitors, auth

    def accept(self, link_id: int, node_id: str, value: Any, good: bool, source_time: datetime | None, now: datetime | None = None) -> dict:
        """Returns {"result": "buffered"|"point"|"rejected", "reason": ..., "point": ...}."""
        now = now or datetime.now(timezone.utc)
        with self.store.lock:
            link = self.store.get(link_id)
            if link is None or not link.get("enabled"):
                return {"result": "rejected", "reason": "not_active"}
            node = next((n for n in link["nodes"] if n["node_id"] == node_id), None)
            if node is None:
                return {"result": "rejected", "reason": "not_active"}
            st = link.setdefault("state", {}).setdefault("nodes", {}).setdefault(node_id, empty_node_state())

            def reject(reason: str, detail: str = "") -> dict:
                st["rejected"][reason] = st["rejected"].get(reason, 0) + 1
                st["last_error"] = {"at": _iso(now), "reason": reason, "detail": detail}
                self.store.save(link_id, link)
                return {"result": "rejected", "reason": reason, "detail": detail}

            if not good:
                return reject("bad_quality")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
                return reject("not_numeric", repr(value)[:60])
            if source_time is None:
                return reject("no_timestamp")
            if source_time.tzinfo is None:
                source_time = source_time.replace(tzinfo=timezone.utc)
            if source_time > now + FUTURE_SLACK:
                return reject("future", _iso(source_time))
            if link["stale_after_s"] and now - source_time > timedelta(seconds=link["stale_after_s"]):
                return reject("stale", _iso(source_time))
            if st["last_time"] and _iso(source_time) <= st["last_time"]:
                return reject("old_or_repeated", _iso(source_time))
            x = float(value) * node["scale"] + node["offset"]
            st["last_time"], st["last_value"] = _iso(source_time), x
            st["accepted"] += 1
            st["buffer"].append([x, _iso(source_time)])
            try:
                monitor = self.monitors.store.get(node["monitor_id"])
            except KeyError:
                st["buffer"] = []
                return reject("monitor_missing")
            n = monitor["n"]
            if len(st["buffer"]) < n:
                self.store.save(link_id, link)
                return {"result": "buffered", "have": len(st["buffer"]), "need": n}
            values = [v for v, _ in st["buffer"][:n]]
            taken = st["buffer"][n - 1][1]
            st["buffer"] = st["buffer"][n:]
            try:
                user = self.auth.get_user(link["enabled_by"])
                point = self.monitors.add_point(node["monitor_id"], values, "OPC UA", {"source": "opcua", "link": link["name"][:100]}, taken, user)
            except MonitorError as exc:  # the subgroup is dropped, not kept for later: it would be a different subgroup
                return reject(exc.code, str(exc))
            st["points"] += 1
            st["last_error"] = None
            self.store.save(link_id, link)
            return {"result": "point", "point": point["point"], "status": point["status"]}

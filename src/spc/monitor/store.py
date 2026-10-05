"""Database access for SPC monitors: monitors, limits revisions, points, incidents, events, acknowledgements."""

from __future__ import annotations

import json
from typing import Any

from spc.db.database import Database
from spc.db.stores import now_iso


class MonitorNotFound(KeyError):
    pass


class IncidentNotFound(KeyError):
    pass


class MonitorNameTaken(ValueError):
    pass


def _dump(x) -> str:
    return json.dumps(x, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def point_json(r) -> dict:
    return {
        "seq": r["seq"], "limits_rev": r["limits_rev"], "taken_at": r["taken_at"], "entered_at": r["entered_at"],
        "entered_by": r["entered_by"], "label": r["label"], "tags": json.loads(r["tags"]), "values": json.loads(r["vals"]),
        "loc": r["loc"], "var": r["var"], "valid": bool(r["valid"]),
        "invalid": None if r["valid"] else {"reason": r["invalid_reason"], "by": r["invalid_by"], "at": r["invalid_at"]},
        "alarms": json.loads(r["alarms"]), "warnings": json.loads(r["warnings"]), "incident_id": r["incident_id"],
    }


def incident_json(r) -> dict:
    return {"id": r["id"], "monitor_id": r["monitor_id"], "opened_at": r["opened_at"], "point_seq": r["point_seq"],
            "rules": json.loads(r["rules"]), "status": r["status"], "acked_by": r["acked_by"], "acked_at": r["acked_at"],
            "closed_at": r["closed_at"], "closed_by": r["closed_by"], "outcome": r["outcome"], "outcome_text": r["outcome_text"]}


class MonitorStore:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ monitors
    def _monitor(self, r) -> dict:
        cfg = json.loads(r["config"])
        return {"id": r["id"], "revision": r["revision"], "limits_rev": r["limits_rev"], "ocap_rev": r["ocap_rev"],
                "created_at": r["created_at"], "updated_at": r["updated_at"], **cfg, "active": bool(r["active"])}

    def create(self, config: dict, user_id: int) -> int:
        stamp = now_iso()
        with self.db.tx():
            if self.db.one("SELECT 1 FROM monitors WHERE name = ?", (config["name"],)):
                raise MonitorNameTaken(config["name"])
            return self.db.execute(
                "INSERT INTO monitors (name, config, active, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (config["name"], _dump(config), int(config["active"]), user_id, stamp, stamp)).lastrowid

    def update(self, monitor_id: int, config: dict) -> dict:
        with self.db.tx():
            old = self.get(monitor_id)
            clash = self.db.one("SELECT 1 FROM monitors WHERE name = ? AND id != ?", (config["name"], monitor_id))
            if clash:
                raise MonitorNameTaken(config["name"])
            ocap_changed = old["ocap"] != config["ocap"]
            self.db.execute(
                "UPDATE monitors SET name = ?, config = ?, active = ?, revision = revision + 1, ocap_rev = ocap_rev + ?, updated_at = ? WHERE id = ?",
                (config["name"], _dump(config), int(config["active"]), int(ocap_changed), now_iso(), monitor_id))
            return self.get(monitor_id)

    def get(self, monitor_id: int) -> dict:
        r = self.db.one("SELECT * FROM monitors WHERE id = ?", (monitor_id,))
        if r is None:
            raise MonitorNotFound(monitor_id)
        return self._monitor(r)

    def list(self) -> list[dict]:
        out = []
        for r in self.db.all("SELECT * FROM monitors ORDER BY name COLLATE NOCASE"):
            m = self._monitor(r)
            last = self.db.one("SELECT seq, taken_at, alarms FROM monitor_points WHERE monitor_id = ? ORDER BY seq DESC LIMIT 1", (r["id"],))
            m["last_point"] = None if last is None else {"seq": last["seq"], "taken_at": last["taken_at"], "alarm": json.loads(last["alarms"]) != []}
            m["open_incidents"] = self.db.one("SELECT COUNT(*) AS n FROM monitor_incidents WHERE monitor_id = ? AND status = 'open'", (r["id"],))["n"]
            m["unacknowledged"] = self.db.one(
                "SELECT COUNT(*) AS n FROM monitor_incidents WHERE monitor_id = ? AND status = 'open' AND acked_by = ''", (r["id"],))["n"]
            m["points"] = self.db.one("SELECT COUNT(*) AS n FROM monitor_points WHERE monitor_id = ?", (r["id"],))["n"]
            out.append(m)
        return out

    def delete(self, monitor_id: int) -> None:
        if self.db.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,)).rowcount == 0:
            raise MonitorNotFound(monitor_id)

    # ------------------------------------------------------------------ limits revisions
    def add_limits(self, monitor_id: int, source: dict, data: dict, reason: str, by: str) -> int:
        with self.db.tx():
            rev = self.db.one("SELECT limits_rev FROM monitors WHERE id = ?", (monitor_id,))["limits_rev"] + 1
            self.db.execute(
                "INSERT INTO monitor_limits (monitor_id, revision, created_at, created_by, reason, source, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (monitor_id, rev, now_iso(), by, reason, _dump(source), _dump(data)))
            self.db.execute("UPDATE monitors SET limits_rev = ?, updated_at = ? WHERE id = ?", (rev, now_iso(), monitor_id))
            return rev

    def limits(self, monitor_id: int, revision: int | None = None) -> dict | None:
        rev = revision if revision is not None else self.get(monitor_id)["limits_rev"]
        r = self.db.one("SELECT * FROM monitor_limits WHERE monitor_id = ? AND revision = ?", (monitor_id, rev))
        if r is None:
            return None
        return {"revision": r["revision"], "created_at": r["created_at"], "created_by": r["created_by"], "reason": r["reason"],
                "source": json.loads(r["source"]), **json.loads(r["data"])}

    def limits_history(self, monitor_id: int) -> list[dict]:
        return [self.limits(monitor_id, r["revision"]) for r in
                self.db.all("SELECT revision FROM monitor_limits WHERE monitor_id = ? ORDER BY revision DESC", (monitor_id,))]

    # ------------------------------------------------------------------ points
    def next_seq(self, monitor_id: int) -> int:
        return (self.db.one("SELECT COALESCE(MAX(seq), 0) AS m FROM monitor_points WHERE monitor_id = ?", (monitor_id,))["m"]) + 1

    def insert_point(self, monitor_id: int, seq: int, **f: Any) -> None:
        self.db.execute(
            "INSERT INTO monitor_points (monitor_id, seq, limits_rev, taken_at, entered_at, entered_by, label, tags, vals, loc, var, alarms, warnings, incident_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (monitor_id, seq, f["limits_rev"], f["taken_at"], now_iso(), f["entered_by"], f["label"], _dump(f["tags"]), _dump(f["values"]),
             f["loc"], f["var"], _dump(f["alarms"]), _dump(f["warnings"]), f.get("incident_id")))

    def points(self, monitor_id: int, limit: int = 100, before: int | None = None, since_seq: int | None = None) -> list[dict]:
        rows = self.db.all(
            "SELECT * FROM monitor_points WHERE monitor_id = ? AND (? IS NULL OR seq < ?) AND (? IS NULL OR seq >= ?) ORDER BY seq DESC LIMIT ?",
            (monitor_id, before, before, since_seq, since_seq, limit))
        return [point_json(r) for r in reversed(rows)]

    def point(self, monitor_id: int, seq: int) -> dict | None:
        r = self.db.one("SELECT * FROM monitor_points WHERE monitor_id = ? AND seq = ?", (monitor_id, seq))
        return None if r is None else point_json(r)

    def valid_history(self, monitor_id: int, limits_rev: int, limit: int):
        """(loc list, var list) of the latest valid points of a limits revision, oldest first."""
        rows = self.db.all(
            "SELECT loc, var FROM monitor_points WHERE monitor_id = ? AND limits_rev = ? AND valid = 1 ORDER BY seq DESC LIMIT ?",
            (monitor_id, limits_rev, limit))[::-1]
        return [r["loc"] for r in rows], [r["var"] for r in rows if r["var"] is not None]

    def valid_counts(self, monitor_id: int, limits_rev: int, limit: int):
        """(plotted value, sample size or None) of the latest valid points of a limits revision, oldest first (count charts)."""
        rows = self.db.all(
            "SELECT loc, vals FROM monitor_points WHERE monitor_id = ? AND limits_rev = ? AND valid = 1 ORDER BY seq DESC LIMIT ?",
            (monitor_id, limits_rev, limit))[::-1]
        return [(r["loc"], (json.loads(r["vals"]) + [None])[1]) for r in rows]

    def previous_value(self, monitor_id: int) -> float | None:
        r = self.db.one("SELECT loc FROM monitor_points WHERE monitor_id = ? AND valid = 1 ORDER BY seq DESC LIMIT 1", (monitor_id,))
        return None if r is None else r["loc"]

    def invalidate(self, monitor_id: int, seq: int, reason: str, by: str) -> None:
        self.db.execute(
            "UPDATE monitor_points SET valid = 0, invalid_reason = ?, invalid_by = ?, invalid_at = ? WHERE monitor_id = ? AND seq = ?",
            (reason, by, now_iso(), monitor_id, seq))

    def set_point_incident(self, monitor_id: int, seq: int, incident_id: int) -> None:
        self.db.execute("UPDATE monitor_points SET incident_id = ? WHERE monitor_id = ? AND seq = ?", (incident_id, monitor_id, seq))

    # ------------------------------------------------------------------ incidents and events
    def open_incident(self, monitor_id: int, seq: int, rules: list) -> int:
        return self.db.execute("INSERT INTO monitor_incidents (monitor_id, opened_at, point_seq, rules) VALUES (?, ?, ?, ?)",
                               (monitor_id, now_iso(), seq, _dump(rules))).lastrowid

    def incident(self, incident_id: int) -> dict:
        r = self.db.one("SELECT * FROM monitor_incidents WHERE id = ?", (incident_id,))
        if r is None:
            raise IncidentNotFound(incident_id)
        return incident_json(r)

    def open_incident_of(self, monitor_id: int) -> dict | None:
        r = self.db.one("SELECT * FROM monitor_incidents WHERE monitor_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1", (monitor_id,))
        return None if r is None else incident_json(r)

    def incidents(self, monitor_id: int | None = None, status: str | None = None, limit: int = 100) -> list[dict]:
        rows = self.db.all(
            "SELECT * FROM monitor_incidents WHERE (? IS NULL OR monitor_id = ?) AND (? IS NULL OR status = ?) ORDER BY id DESC LIMIT ?",
            (monitor_id, monitor_id, status, status, limit))
        return [incident_json(r) for r in rows]

    def add_event(self, monitor_id: int, incident_id: int | None, by: str, kind: str, step: str = "", text: str = "") -> dict:
        at = now_iso()
        eid = self.db.execute("INSERT INTO monitor_events (monitor_id, incident_id, at, by_label, kind, step, text) VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (monitor_id, incident_id, at, by, kind, step, text)).lastrowid
        return {"id": eid, "at": at, "by": by, "kind": kind, "step": step, "text": text, "incident_id": incident_id}

    def events(self, incident_id: int | None = None, monitor_id: int | None = None, limit: int = 200) -> list[dict]:
        rows = self.db.all(
            "SELECT * FROM monitor_events WHERE (? IS NULL OR incident_id = ?) AND (? IS NULL OR monitor_id = ?) ORDER BY id DESC LIMIT ?",
            (incident_id, incident_id, monitor_id, monitor_id, limit))
        return [{"id": r["id"], "at": r["at"], "by": r["by_label"], "kind": r["kind"], "step": r["step"], "text": r["text"],
                 "incident_id": r["incident_id"]} for r in reversed(rows)]

    def ack_incident(self, incident_id: int, by: str) -> None:
        self.db.execute("UPDATE monitor_incidents SET acked_by = ?, acked_at = ? WHERE id = ? AND acked_by = ''", (by, now_iso(), incident_id))

    def close_incident(self, incident_id: int, by: str, outcome: str, text: str) -> None:
        self.db.execute("UPDATE monitor_incidents SET status = 'closed', closed_at = ?, closed_by = ?, outcome = ?, outcome_text = ? WHERE id = ?",
                        (now_iso(), by, outcome, text, incident_id))

    # ------------------------------------------------------------------ acknowledgement of the action plan
    def has_ack(self, monitor_id: int, user_id: int, ocap_rev: int) -> bool:
        return self.db.one("SELECT 1 FROM monitor_acks WHERE monitor_id = ? AND user_id = ? AND ocap_rev = ?", (monitor_id, user_id, ocap_rev)) is not None

    def add_ack(self, monitor_id: int, user_id: int, ocap_rev: int) -> None:
        self.db.execute("INSERT OR IGNORE INTO monitor_acks (monitor_id, user_id, ocap_rev, at) VALUES (?, ?, ?, ?)",
                        (monitor_id, user_id, ocap_rev, now_iso()))

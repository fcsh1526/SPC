"""Equipment links: the record, the test read that proves the interface, enabling, and the view with the counters of the readings."""

from __future__ import annotations

import json
from typing import Any

from spc.auth.audit import Audit
from spc.db.database import Database
from spc.db.stores import now_iso
from spc.equipment import client as opc
from spc.equipment import model as M
from spc.equipment.ingest import Ingest, LinkStore
from spc.equipment.model import EquipmentError


def _label(user) -> str:
    return f"{user.display_name or user.username} ({user.username})" if getattr(user, "display_name", "") else user.username


def _dump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


class EquipmentService:
    def __init__(self, db: Database, audit: Audit, monitors, auth, runner=None, prober=None):
        self.db, self.audit, self.monitors = db, audit, monitors
        self.store = LinkStore(db)
        self.ingest = Ingest(self.store, monitors, auth)
        self.runner = runner if runner is not None else opc.Runner(self.store, self.ingest)
        self.prober = prober or opc.probe  # replaceable in tests

    # ------------------------------------------------------------------ record
    def get(self, link_id: int) -> dict:
        link = self.store.get(link_id)
        if link is None:
            raise EquipmentError("equipment_link_not_found", "equipment link not found", 404)
        return link

    def _check(self, record: dict) -> None:
        for i, n in enumerate(record["nodes"], start=1):
            try:
                m = self.monitors.store.get(n["monitor_id"])
            except KeyError:
                raise EquipmentError("monitor_not_found", f"node {i}: the monitor does not exist", 404) from None
            if m["kind"] in M.UNSUPPORTED_KINDS:
                raise EquipmentError("equipment_kind_unsupported", f"node {i}: a {m['kind']} monitor needs more than a list of numbers", 409, kind=m["kind"])

    def create(self, record_in: dict, user) -> dict:
        try:
            record = M.validate_link(record_in)
        except ValueError as exc:
            raise EquipmentError("invalid_input", str(exc)) from None
        self._check(record)
        data = {**record, "revision": 1, "enabled": False, "enabled_by": None, "last_test": None, "state": {"nodes": {}}}
        with self.store.lock, self.db.tx():
            if self.db.one("SELECT 1 FROM equipment_links WHERE name = ?", (record["name"],)):
                raise EquipmentError("equipment_name_taken", "a link with this name exists already", 409)
            stamp = now_iso()
            cur = self.db.execute("INSERT INTO equipment_links (name, data, created_at, updated_at, created_by) VALUES (?, ?, ?, ?, ?)",
                                  (record["name"], _dump(data), stamp, stamp, user.id))
            self.audit.append("equipment_created", user_id=user.id, username=user.username, target=record["name"],
                              detail={"id": cur.lastrowid, "endpoint": record["endpoint"], "nodes": len(record["nodes"])})
        return self.view(cur.lastrowid)

    def update(self, link_id: int, record_in: dict, user) -> dict:
        try:
            record = M.validate_link(record_in)
        except ValueError as exc:
            raise EquipmentError("invalid_input", str(exc)) from None
        self._check(record)
        with self.store.lock, self.db.tx():
            link = self.get(link_id)
            if self.db.one("SELECT 1 FROM equipment_links WHERE name = ? AND id != ?", (record["name"], link_id)):
                raise EquipmentError("equipment_name_taken", "a link with this name exists already", 409)
            changed = [k for k in M.CONFIG_KEYS if record[k] != link[k]]
            if changed:  # another configuration is another interface: it is tested and enabled again
                link.update(record, revision=link["revision"] + 1, enabled=False, enabled_by=None, last_test=None)
                link["state"] = {"nodes": {}}
                self.store.save(link_id, link)
            self.audit.append("equipment_updated", user_id=user.id, username=user.username, target=record["name"],
                              detail={"id": link_id, "revision": link["revision"], "changed": changed})
        self.runner.reconcile()
        return self.view(link_id)

    def delete(self, link_id: int, user) -> None:
        link = self.get(link_id)
        with self.store.lock, self.db.tx():
            self.db.execute("DELETE FROM equipment_links WHERE id = ?", (link_id,))
            self.audit.append("equipment_deleted", user_id=user.id, username=user.username, target=link["name"], detail={"id": link_id})
        self.runner.reconcile()

    # ------------------------------------------------------------------ test and enable
    def test(self, link_id: int, user) -> dict:
        link = self.get(link_id)
        result = self.prober(link)
        entry = {"at": now_iso(), "by": _label(user), "revision": link["revision"], "ok": bool(result["ok"]), "error": result.get("error"), "nodes": result["nodes"]}
        with self.store.lock, self.db.tx():
            link = self.get(link_id)
            if link["revision"] == entry["revision"]:
                link["last_test"] = entry
                self.store.save(link_id, link)
            self.audit.append("equipment_tested", user_id=user.id, username=user.username, target=link["name"],
                              detail={"id": link_id, "revision": entry["revision"], "ok": entry["ok"], "error": entry["error"],
                                      "nodes": [{"node_id": n["node_id"], "ok": n["ok"], "error": n["error"]} for n in entry["nodes"]]})
        return self.view(link_id)

    def enable(self, link_id: int, user) -> dict:
        with self.store.lock, self.db.tx():
            link = self.get(link_id)
            t = link["last_test"]
            if not t or t["revision"] != link["revision"] or not t["ok"]:
                raise EquipmentError("equipment_not_tested", "the interface is enabled after a test read of this configuration in which every node was read with good quality", 409)
            self._check(link)
            for n in link["nodes"]:  # the readings are entered in the name of this person: they must know the action plan like any operator
                m = self.monitors.store.get(n["monitor_id"])
                if m["require_ack"] and not self.monitors.store.has_ack(n["monitor_id"], user.id, m["ocap_rev"]):
                    raise EquipmentError("ocap_not_acknowledged", "confirm that you know the action plan of this monitor first", 403, monitor=m["name"])
            link.update(enabled=True, enabled_by=user.id)
            self.store.save(link_id, link)
            self.audit.append("equipment_enabled", user_id=user.id, username=user.username, target=link["name"], detail={"id": link_id, "revision": link["revision"]})
        self.runner.reconcile()
        return self.view(link_id)

    def disable(self, link_id: int, user) -> dict:
        with self.store.lock, self.db.tx():
            link = self.get(link_id)
            link.update(enabled=False)
            self.store.save(link_id, link)
            self.audit.append("equipment_disabled", user_id=user.id, username=user.username, target=link["name"], detail={"id": link_id})
        self.runner.reconcile()
        return self.view(link_id)

    # ------------------------------------------------------------------ views
    def view(self, link_id: int) -> dict:
        link = self.get(link_id)
        run = self.runner.status.get(link_id)
        if not link["enabled"]:
            runtime = {"state": "disabled"}
        elif not self.runner.running:
            runtime = {"state": "runner_stopped"}
        else:
            runtime = run or {"state": "connecting"}
        nodes = link["state"].get("nodes", {})
        counters = {n["node_id"]: {k: nodes.get(n["node_id"], {}).get(k) for k in ("last_time", "last_value", "accepted", "points", "rejected", "last_error")}
                    | {"buffered": len(nodes.get(n["node_id"], {}).get("buffer", []))} for n in link["nodes"]}
        out = {k: v for k, v in link.items() if k != "state"}
        return {"link": out, "runtime": runtime, "counters": counters}

    def list(self) -> list[dict]:
        out = []
        for l in self.store.all():
            v = self.view(l["id"])
            out.append({"id": l["id"], "name": l["name"], "endpoint": l["endpoint"], "nodes": len(l["nodes"]), "enabled": l["enabled"], "revision": l["revision"],
                        "tested": bool(l["last_test"] and l["last_test"]["ok"]), "runtime": v["runtime"]["state"],
                        "accepted": sum(c["accepted"] or 0 for c in v["counters"].values()), "points": sum(c["points"] or 0 for c in v["counters"].values()),
                        "rejected": sum(sum((c["rejected"] or {}).values()) for c in v["counters"].values())})
        return out

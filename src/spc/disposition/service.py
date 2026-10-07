"""Lots and their disposition: the quality conformance gate (draft 5.4, control loop 2; 6.8.1 "disposition of the product"; 10.2.3 sorting).

A lot is a quantity of parts made in a stretch of a monitored process. The draft wants conforming product passed and nonconforming product rejected, and says that
action on the output (sorting, scrap, rework) is an interim measure until the process is corrected and the correction verified. The program does not decide: it
puts the evidence of the monitor in front of the person and refuses a plain release that the evidence does not support.

Evidence of a lot linked to a monitor and a range of its points (seq_from..seq_to), read live while the lot is open and fixed at the decision:
  blocking   an incident of a point in the range, or the open incident of the monitor from before the end of the range, is open;
             an incident of the range was closed as `escalated` (root cause analysis open: product conformance is to be ensured by sorting);
             the measurement system of the monitor is blocked by the MSA gate; the range holds no valid point
  remarks    points marked invalid in the range, a conditional MSA gate, alarms answered by a closed incident (recovered or invalid sample)
Decisions: release (needs clean evidence), concession (release with blocking evidence: an engineer, a reason and the reference of the customer's approval),
sort (every part inspected; good + rejected = inspected; the rejected ones go to scrap or rework), rework, scrap. A release without a linked monitor needs an engineer
and a reason. The people: an operator may record lots, hold them and decide when the evidence is clean (6.8.1); an engineer decides anything else, and reopens.
Every step is in the history of the lot and in the audit chain.
"""

from __future__ import annotations

import json
import math
from typing import Any

from spc.auth.audit import Audit
from spc.db.database import Database
from spc.db.stores import now_iso
from spc.monitor.store import MonitorNotFound

DECISIONS = ("release", "concession", "sort", "rework", "scrap")
STATUSES = ("open", "held", "decided")
CLOSED_OK = ("recovered", "invalid_sample")
MAX_QUANTITY = 100_000_000


class LotProblem(ValueError):
    """A request that breaks the rules. `code` is the stable message code of the API."""

    def __init__(self, code: str, message: str = "", status: int = 400, **params):
        super().__init__(message or code)
        self.code, self.status, self.params = code, status, params


class LotNotFound(KeyError):
    """No lot with this id."""


class LotNumberTaken(ValueError):
    """Another lot has this number."""


def _label(user) -> str:
    return f"{user.display_name or user.username} ({user.username})" if getattr(user, "display_name", "") else user.username


def _dump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def _text(d: dict, key: str, limit: int, required: bool = False) -> str:
    v = d.get(key, "")
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise ValueError(f"{key} must be text")
    v = v.strip()
    if required and not v:
        raise ValueError(f"{key} is required")
    if len(v) > limit:
        raise ValueError(f"{key} is longer than {limit} characters")
    return v


def _whole(v, name: str, low: int, high: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
        raise ValueError(f"{name} must be a whole number from {low} to {high}")
    return v


def validate_record(data: dict) -> dict:
    allowed = {"lot_no", "product", "characteristic", "quantity", "monitor_id", "seq_from", "seq_to", "note", "parent_lot_id"}
    if set(data) - allowed:
        raise ValueError(f"unknown field(s): {sorted(set(data) - allowed)}")
    out: dict[str, Any] = {"lot_no": _text(data, "lot_no", 100, True), "product": _text(data, "product", 200), "characteristic": _text(data, "characteristic", 200),
                           "quantity": _whole(data.get("quantity"), "quantity", 1, MAX_QUANTITY), "note": _text(data, "note", 2000),
                           "monitor_id": None, "seq_from": None, "seq_to": None, "parent_lot_id": None}
    if data.get("monitor_id") is not None:
        out["monitor_id"] = _whole(data["monitor_id"], "monitor_id", 1, 2**31)
        out["seq_from"] = _whole(data.get("seq_from"), "seq_from", 1, 2**31)
        out["seq_to"] = _whole(data.get("seq_to"), "seq_to", 1, 2**31)
        if out["seq_from"] > out["seq_to"]:
            raise ValueError("seq_from must not be above seq_to")
    elif data.get("seq_from") is not None or data.get("seq_to") is not None:
        raise ValueError("a range of points needs a monitor")
    if data.get("parent_lot_id") is not None:
        out["parent_lot_id"] = _whole(data["parent_lot_id"], "parent_lot_id", 1, 2**31)
    return out


class LotService:
    def __init__(self, db: Database, audit: Audit, monitors, msa):
        self.db, self.audit, self.monitors, self.msa = db, audit, monitors, msa

    def _log(self, action: str, user, target: str, detail: dict) -> None:
        self.audit.append(action, user_id=user.id, username=user.username, target=target, detail=detail)

    # ------------------------------------------------------------------ storage
    @staticmethod
    def _row(r) -> dict:
        return {"id": r["id"], "status": r["status"], "created_at": r["created_at"], "updated_at": r["updated_at"], **json.loads(r["data"])}

    def get(self, lot_id: int) -> dict:
        r = self.db.one("SELECT * FROM lots WHERE id = ?", (lot_id,))
        if r is None:
            raise LotNotFound(lot_id)
        return self._row(r)

    def _save(self, lot: dict) -> None:
        data = {k: v for k, v in lot.items() if k not in ("id", "status", "created_at", "updated_at")}
        self.db.execute("UPDATE lots SET status = ?, data = ?, updated_at = ? WHERE id = ?", (lot["status"], _dump(data), now_iso(), lot["id"]))

    def list(self, status: str | None = None) -> list[dict]:
        rows = self.db.all("SELECT * FROM lots WHERE (? IS NULL OR status = ?) ORDER BY id DESC LIMIT 1000", (status, status))
        return [{k: v for k, v in self._row(r).items() if k not in ("history", "evidence")} for r in rows]

    # ------------------------------------------------------------------ evidence
    def evidence(self, lot: dict) -> dict | None:
        """What the monitor says about the range of the lot now. None when the lot is not linked to a monitor."""
        mid = lot.get("monitor_id")
        if mid is None:
            return None
        store = self.monitors.store
        monitor = store.get(mid)
        lo, hi = lot["seq_from"], lot["seq_to"]
        points = [p for p in store.points(mid, limit=1_000_000, since_seq=lo) if p["seq"] <= hi]
        valid = [p for p in points if p["valid"]]
        blocking: list[dict] = []
        remarks: list[dict] = []
        if not valid:
            blocking.append({"code": "no_points"})
        invalid = [p["seq"] for p in points if not p["valid"]]
        if invalid:
            remarks.append({"code": "invalid_points", "count": len(invalid), "seqs": invalid[:20]})
        incidents: dict[int, dict] = {}
        for p in valid:
            if p["incident_id"] is not None and p["incident_id"] not in incidents:
                incidents[p["incident_id"]] = store.incident(p["incident_id"])
        open_inc = store.open_incident_of(mid)
        if open_inc is not None and open_inc["point_seq"] <= hi:
            incidents.setdefault(open_inc["id"], open_inc)
        alarm_points = [p["seq"] for p in valid if p["alarms"]]
        for inc in incidents.values():
            if inc["status"] == "open":
                blocking.append({"code": "open_incident", "incident": inc["id"], "point": inc["point_seq"]})
            elif inc["outcome"] == "escalated":
                blocking.append({"code": "escalated_incident", "incident": inc["id"], "point": inc["point_seq"]})
            elif inc["outcome"] in CLOSED_OK:
                remarks.append({"code": "alarm_answered", "incident": inc["id"], "outcome": inc["outcome"], "point": inc["point_seq"]})
        msa_id = (monitor.get("specs") or {}).get("msa_id")
        gate_status = None
        if msa_id:
            try:
                gate_status = self.msa.gate(msa_id)["status"]
            except KeyError:
                gate_status = None
            if gate_status == "block":
                blocking.append({"code": "gate_blocked", "msa_id": msa_id})
            elif gate_status == "conditional":
                remarks.append({"code": "gate_conditional", "msa_id": msa_id})
        return {"monitor": monitor["name"], "monitor_id": mid, "seq_from": lo, "seq_to": hi, "points": len(points), "valid_points": len(valid), "alarm_points": alarm_points,
                "incidents": [{"id": i["id"], "status": i["status"], "outcome": i["outcome"], "point": i["point_seq"]} for i in sorted(incidents.values(), key=lambda i: i["id"])],
                "gate": gate_status, "blocking": blocking, "remarks": remarks, "clean": not blocking, "taken_at": now_iso()}

    def view(self, lot_id: int) -> dict:
        lot = self.get(lot_id)
        live = lot["status"] != "decided"
        return {**lot, "evidence": self.evidence(lot) if live else lot.get("evidence")}

    # ------------------------------------------------------------------ steps
    def create(self, record_in: dict, user) -> dict:
        try:
            record = validate_record(record_in)
        except ValueError as exc:
            raise LotProblem("invalid_input", str(exc)) from None
        if record["monitor_id"] is not None:
            try:
                self.monitors.store.get(record["monitor_id"])
            except MonitorNotFound:
                raise LotProblem("invalid_input", "no such monitor", 400) from None
        if record["parent_lot_id"] is not None:
            try:
                self.get(record["parent_lot_id"])
            except LotNotFound:
                raise LotProblem("invalid_input", "no such parent lot") from None
        if self.db.one("SELECT 1 FROM lots WHERE lot_no = ?", (record["lot_no"],)):
            raise LotNumberTaken(record["lot_no"])
        history = [{"at": now_iso(), "by": _label(user), "action": "created", "detail": {}}]
        stamp = now_iso()
        with self.db.tx():
            cur = self.db.execute("INSERT INTO lots (lot_no, status, data, created_at, updated_at, created_by) VALUES (?, 'open', ?, ?, ?, ?)",
                                  (record["lot_no"], _dump({**record, "decision": None, "evidence": None, "history": history}), stamp, stamp, user.id))
            self._log("lot_created", user, record["lot_no"], {"id": cur.lastrowid, "quantity": record["quantity"], "monitor_id": record["monitor_id"],
                                                             "seq_from": record["seq_from"], "seq_to": record["seq_to"]})
        return self.view(cur.lastrowid)

    def hold(self, lot_id: int, reason: str, user) -> dict:
        lot = self.get(lot_id)
        if lot["status"] == "decided":
            raise LotProblem("lot_decided", "the lot has a decision already: reopen it first", 409)
        if lot["status"] == "held":
            raise LotProblem("lot_held", "the lot is on hold already", 409)
        if not reason or len(reason.strip()) < 3:
            raise LotProblem("reason_required", "a reason is required to put a lot on hold")
        lot["status"] = "held"
        lot["history"].append({"at": now_iso(), "by": _label(user), "action": "held", "detail": {"reason": reason.strip()}})
        with self.db.tx():
            self._save(lot)
            self._log("lot_held", user, lot["lot_no"], {"id": lot_id, "reason": reason.strip()})
        return self.view(lot_id)

    def decide(self, lot_id: int, body: dict, user) -> dict:
        lot = self.get(lot_id)
        if lot["status"] == "decided":
            raise LotProblem("lot_decided", "the lot has a decision already: reopen it first", 409)
        kind = body.get("decision")
        if kind not in DECISIONS:
            raise LotProblem("invalid_input", f"decision must be one of {DECISIONS}")
        reason = (body.get("reason") or "").strip()
        if len(reason) > 2000:
            raise LotProblem("invalid_input", "the reason is longer than 2000 characters")
        engineer = user.can("engineer")
        ev = self.evidence(lot)
        detail: dict[str, Any] = {"kind": kind, "reason": reason}
        if kind == "release":
            if ev is None:
                if not engineer:
                    raise LotProblem("release_needs_evidence", "a lot without a monitor can only be released by an engineer", 403)
                if len(reason) < 3:
                    raise LotProblem("reason_required", "a reason is required to release a lot that has no monitor")
            elif not ev["clean"]:
                raise LotProblem("release_blocked", "the evidence does not support a release: sort the lot, or an engineer grants a concession", 409, reasons=ev["blocking"])
        elif kind == "concession":
            if not engineer:
                raise LotProblem("concession_needs_engineer", "only an engineer can grant a concession", 403)
            ref = (body.get("customer_ref") or "").strip()
            if len(reason) < 3 or not ref:
                raise LotProblem("concession_needs_approval", "a concession needs a reason and the reference of the approval of the customer")
            if len(ref) > 300:
                raise LotProblem("invalid_input", "the reference is longer than 300 characters")
            detail["customer_ref"] = ref
        elif kind == "sort":
            s = body.get("sorted")
            if not isinstance(s, dict) or set(s) - {"inspected", "good", "rejected", "rejected_to"}:
                raise LotProblem("invalid_input", "sorted holds inspected, good, rejected and rejected_to")
            try:
                inspected = _whole(s.get("inspected"), "inspected", 0, MAX_QUANTITY)
                good, rejected = _whole(s.get("good"), "good", 0, MAX_QUANTITY), _whole(s.get("rejected"), "rejected", 0, MAX_QUANTITY)
            except ValueError as exc:
                raise LotProblem("invalid_input", str(exc)) from None
            if inspected != lot["quantity"]:
                raise LotProblem("sort_incomplete", "sorting inspects every part of the lot", 400, inspected=inspected, quantity=lot["quantity"])
            if good + rejected != inspected:
                raise LotProblem("sort_counts", "good and rejected must add up to the parts inspected", 400, inspected=inspected, good=good, rejected=rejected)
            to = s.get("rejected_to")
            if rejected and to not in ("scrap", "rework"):
                raise LotProblem("sort_rejected_to", "the rejected parts go to scrap or rework")
            detail["sorted"] = {"inspected": inspected, "good": good, "rejected": rejected, "rejected_to": to if rejected else None}
        else:  # rework, scrap
            if len(reason) < 3:
                raise LotProblem("reason_required", f"a reason is required to {kind} a lot")
        lot["status"] = "decided"
        lot["decision"] = {**detail, "by": _label(user), "at": now_iso(), "role": user.role}
        lot["evidence"] = ev
        lot["history"].append({"at": lot["decision"]["at"], "by": _label(user), "action": "decided", "detail": detail})
        with self.db.tx():
            self._save(lot)
            self._log("lot_decided", user, lot["lot_no"], {"id": lot_id, **{k: v for k, v in detail.items() if k != "reason"}, "reason": reason,
                                                          "evidence_clean": None if ev is None else ev["clean"]})
        return self.view(lot_id)

    def reopen(self, lot_id: int, reason: str, user) -> dict:
        lot = self.get(lot_id)
        if lot["status"] != "decided":
            raise LotProblem("lot_not_decided", "only a lot with a decision can be reopened", 409)
        if not reason or len(reason.strip()) < 3:
            raise LotProblem("reason_required", "a reason is required to reopen a lot")
        lot["history"].append({"at": now_iso(), "by": _label(user), "action": "reopened", "detail": {"reason": reason.strip(), "was": lot["decision"]["kind"]}})
        lot["status"], lot["decision"], lot["evidence"] = "open", None, None
        with self.db.tx():
            self._save(lot)
            self._log("lot_reopened", user, lot["lot_no"], {"id": lot_id, "reason": reason.strip()})
        return self.view(lot_id)

    def delete(self, lot_id: int, user) -> None:
        lot = self.get(lot_id)
        with self.db.tx():
            self.db.execute("DELETE FROM lots WHERE id = ?", (lot_id,))
            self._log("lot_deleted", user, lot["lot_no"], {"id": lot_id, "status": lot["status"]})

"""Improvement cycles: control loop 3, the post-process improvement cycle (draft 5.4, figure 5-4).

The draft gives the method (PDCA) and the people (quality, process engineering and planning), not the rules:
  P  analysis of the indicators and a plan for the improvement, with a defined KPI     -> the monitor, the KPI (Pk or P of the ongoing report) with its target, the plan; the
                                                                                         baseline is read from the ongoing report of the monitor at that moment
  D  implement the improvement                                                          -> what was done; the sequence number of the newest point is kept
  C  verify the impact of the improvement with conformance to the KPI                   -> the ongoing report is calculated again over the points entered after the implementation
                                                                                         (at least 25 valid ones); effective = the index reaches the target and the process is stable
  A  if the KPI is met, establish the improvement as the new standard; otherwise rework the plan  -> standardise (a note of what the new standard is), or rework (a new plan)
The steps follow one another; every step is in the history of the improvement and in the audit chain. The rules (the 25 points, effective = target reached and stable) are ours.
"""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

from spc.auth.audit import Audit
from spc.db.database import Database
from spc.db.stores import now_iso
from spc.monitor.model import MonitorError
from spc.monitor.store import MonitorNotFound

MIN_VERIFY_POINTS = 25
KPIS = ("pk", "p")
STATUSES = ("planned", "implemented", "verified", "standardised")
STABLE = ("statistical_control", "in_control")


class ImprovementProblem(ValueError):
    def __init__(self, code: str, message: str = "", status: int = 400, **params):
        super().__init__(message or code)
        self.code, self.status, self.params = code, status, params


class ImprovementNotFound(KeyError):
    """No improvement cycle with this id."""


def _label(user) -> str:
    return f"{user.display_name or user.username} ({user.username})" if getattr(user, "display_name", "") else user.username


def _dump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def _text(v, name: str, limit: int, minimum: int = 0) -> str:
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise ValueError(f"{name} must be text")
    v = v.strip()
    if len(v) < minimum:
        raise ValueError(f"{name} is required" if minimum else f"{name} is empty")
    if len(v) > limit:
        raise ValueError(f"{name} is longer than {limit} characters")
    return v


def validate_record(data: dict) -> dict:
    allowed = {"title", "problem", "monitor_id", "kpi", "target", "plan", "due"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError(f"unknown field(s): {sorted(set(data) - allowed) if isinstance(data, dict) else 'the record must be an object'}")
    monitor_id = data.get("monitor_id")
    if isinstance(monitor_id, bool) or not isinstance(monitor_id, int) or monitor_id < 1:
        raise ValueError("monitor_id must be the id of the monitor whose indicators the improvement is about")
    kpi = data.get("kpi", "pk")
    if kpi not in KPIS:
        raise ValueError(f"kpi must be one of {KPIS}")
    target = data.get("target")
    if isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target) or not 0.1 <= target <= 10:
        raise ValueError("target must be a number from 0.1 to 10 (the index that the improvement has to reach)")
    due = data.get("due")
    if due not in (None, ""):
        try:
            date.fromisoformat(due)
        except (TypeError, ValueError):
            raise ValueError("due must be a date YYYY-MM-DD") from None
    return {"title": _text(data.get("title"), "title", 200, 1), "problem": _text(data.get("problem"), "problem", 2000), "monitor_id": monitor_id, "kpi": kpi, "target": float(target),
            "plan": _text(data.get("plan"), "plan", 2000, 3), "due": due or None}


class ImprovementService:
    def __init__(self, db: Database, audit: Audit, monitors):
        self.db, self.audit, self.monitors = db, audit, monitors

    def _log(self, action: str, user, target: str, detail: dict) -> None:
        self.audit.append(action, user_id=user.id, username=user.username, target=target, detail=detail)

    @staticmethod
    def _row(r) -> dict:
        return {"id": r["id"], "status": r["status"], "created_at": r["created_at"], "updated_at": r["updated_at"], **json.loads(r["data"])}

    def get(self, imp_id: int) -> dict:
        r = self.db.one("SELECT * FROM improvements WHERE id = ?", (imp_id,))
        if r is None:
            raise ImprovementNotFound(imp_id)
        return self._row(r)

    def _save(self, imp: dict) -> None:
        data = {k: v for k, v in imp.items() if k not in ("id", "status", "created_at", "updated_at")}
        self.db.execute("UPDATE improvements SET status = ?, data = ?, updated_at = ? WHERE id = ?", (imp["status"], _dump(data), now_iso(), imp["id"]))

    def list(self, status: str | None = None) -> list[dict]:
        rows = self.db.all("SELECT * FROM improvements WHERE (? IS NULL OR status = ?) ORDER BY id DESC LIMIT 1000", (status, status))
        return [{k: v for k, v in self._row(r).items() if k != "history"} for r in rows]

    def view(self, imp_id: int) -> dict:
        imp = self.get(imp_id)
        try:
            imp["monitor_name"] = self.monitors.store.get(imp["monitor_id"])["name"]
        except MonitorNotFound:
            imp["monitor_name"] = None
        return imp

    # ------------------------------------------------------------------ the indicators of the monitor
    def _snapshot(self, monitor_id: int, window: int, kpi: str) -> dict:
        """The ongoing report over the last `window` valid points, reduced to what the improvement needs."""
        try:
            rep = self.monitors.ongoing(monitor_id, window, 1)
        except MonitorError as exc:
            raise ImprovementProblem(exc.code, str(exc), exc.status, **getattr(exc, "params", {})) from None
        except ValueError as exc:
            raise ImprovementProblem("invalid_input", str(exc)) from None
        res = rep["result"]
        if not res.get("indices"):
            raise ImprovementProblem("kpi_not_available", "the monitor has no specification limits: there is no capability or performance index to follow", 409)
        stable = res["stability"]["class"] in STABLE
        return {"kpi": kpi, "name": res["names"][kpi], "value": res["indices"].get(kpi), "stability": res["stability"]["class"], "stable": bool(stable),
                "quadrant": rep["quadrant"]["number"], "points": rep["window"]["points"], "from_seq": rep["window"]["from_seq"], "to_seq": rep["window"]["to_seq"], "at": now_iso()}

    def _last_seq(self, monitor_id: int) -> int:
        pts = [p for p in self.monitors.store.points(monitor_id, limit=1)]
        return pts[-1]["seq"] if pts else 0

    # ------------------------------------------------------------------ the steps
    def create(self, record_in: dict, user) -> dict:
        try:
            rec = validate_record(record_in)
        except ValueError as exc:
            raise ImprovementProblem("invalid_input", str(exc)) from None
        try:
            monitor = self.monitors.store.get(rec["monitor_id"])
        except MonitorNotFound:
            raise ImprovementProblem("invalid_input", "no such monitor") from None
        baseline, note = None, None
        try:
            baseline = self._snapshot(rec["monitor_id"], 125, rec["kpi"])
        except ImprovementProblem as exc:  # the baseline is wanted but not needed to plan: say why it is missing
            note = exc.code
        stamp = now_iso()
        history = [{"at": stamp, "by": _label(user), "step": "plan", "detail": {"plan": rec["plan"], "kpi": rec["kpi"], "target": rec["target"]}}]
        with self.db.tx():
            cur = self.db.execute("INSERT INTO improvements (status, data, created_at, updated_at, created_by) VALUES ('planned', ?, ?, ?, ?)",
                                  (_dump({**rec, "baseline": baseline, "baseline_missing": note, "implementation": None, "verification": None, "standard": None, "history": history}), stamp, stamp, user.id))
            self._log("improvement_planned", user, monitor["name"], {"id": cur.lastrowid, "title": rec["title"], "kpi": rec["kpi"], "target": rec["target"],
                                                                      "baseline": None if baseline is None else baseline["value"]})
        return self.view(cur.lastrowid)

    def implement(self, imp_id: int, description: str, user) -> dict:
        imp = self.get(imp_id)
        if imp["status"] != "planned":
            raise ImprovementProblem("wrong_step", "only a planned improvement is implemented", 409, current=imp["status"])
        text = (description or "").strip()
        if len(text) < 3 or len(text) > 2000:
            raise ImprovementProblem("invalid_input", "say what was done (3 to 2000 characters)")
        seq = self._last_seq(imp["monitor_id"])
        imp["implementation"] = {"at": now_iso(), "by": _label(user), "description": text, "after_seq": seq}
        imp["status"] = "implemented"
        imp["history"].append({"at": imp["implementation"]["at"], "by": _label(user), "step": "do", "detail": {"description": text, "after_seq": seq}})
        with self.db.tx():
            self._save(imp)
            self._log("improvement_implemented", user, imp["title"], {"id": imp_id, "after_seq": seq})
        return self.view(imp_id)

    def verify(self, imp_id: int, user) -> dict:
        imp = self.get(imp_id)
        if imp["status"] not in ("implemented", "verified"):
            raise ImprovementProblem("wrong_step", "only an implemented improvement is verified", 409, current=imp["status"])
        after = imp["implementation"]["after_seq"]
        pts = [p for p in self.monitors.store.points(imp["monitor_id"], limit=100000, since_seq=after + 1) if p["valid"]]
        if len(pts) < MIN_VERIFY_POINTS:
            raise ImprovementProblem("verification_too_early", f"the verification needs at least {MIN_VERIFY_POINTS} valid points after the implementation", 409, have=len(pts), need=MIN_VERIFY_POINTS)
        snap = self._snapshot(imp["monitor_id"], len(pts), imp["kpi"])
        value = snap["value"]
        effective = bool(value is not None and value >= imp["target"] and snap["stable"])
        imp["verification"] = {**snap, "by": _label(user), "target": imp["target"], "effective": effective, "after_seq": after}
        imp["status"] = "verified"
        imp["history"].append({"at": snap["at"], "by": _label(user), "step": "check", "detail": {"value": value, "target": imp["target"], "stable": snap["stable"], "effective": effective, "points": snap["points"]}})
        with self.db.tx():
            self._save(imp)
            self._log("improvement_verified", user, imp["title"], {"id": imp_id, "value": value, "target": imp["target"], "stable": snap["stable"], "effective": effective})
        return self.view(imp_id)

    def standardise(self, imp_id: int, note: str, user) -> dict:
        imp = self.get(imp_id)
        if imp["status"] != "verified" or not imp["verification"]["effective"]:
            raise ImprovementProblem("not_effective", "only an improvement whose verification met the KPI is made the new standard", 409, current=imp["status"])
        text = (note or "").strip()
        if len(text) < 3 or len(text) > 2000:
            raise ImprovementProblem("invalid_input", "say what the new standard is (for example the revision of the control plan or of the limits), 3 to 2000 characters")
        imp["standard"] = {"at": now_iso(), "by": _label(user), "note": text}
        imp["status"] = "standardised"
        imp["history"].append({"at": imp["standard"]["at"], "by": _label(user), "step": "act", "detail": {"outcome": "standardised", "note": text}})
        with self.db.tx():
            self._save(imp)
            self._log("improvement_standardised", user, imp["title"], {"id": imp_id, "note": text})
        return self.view(imp_id)

    def rework(self, imp_id: int, plan: str, user) -> dict:
        imp = self.get(imp_id)
        if imp["status"] != "verified" or imp["verification"]["effective"]:
            raise ImprovementProblem("not_ineffective", "only an improvement whose verification missed the KPI is reworked", 409, current=imp["status"])
        text = (plan or "").strip()
        if len(text) < 3 or len(text) > 2000:
            raise ImprovementProblem("invalid_input", "write the new plan (3 to 2000 characters)")
        imp["history"].append({"at": now_iso(), "by": _label(user), "step": "act", "detail": {"outcome": "rework", "plan": text}})
        imp["plan"], imp["implementation"], imp["verification"], imp["status"] = text, None, None, "planned"
        with self.db.tx():
            self._save(imp)
            self._log("improvement_reworked", user, imp["title"], {"id": imp_id})
        return self.view(imp_id)

    def delete(self, imp_id: int, user) -> None:
        imp = self.get(imp_id)
        with self.db.tx():
            self.db.execute("DELETE FROM improvements WHERE id = ?", (imp_id,))
            self._log("improvement_deleted", user, imp["title"], {"id": imp_id, "status": imp["status"]})

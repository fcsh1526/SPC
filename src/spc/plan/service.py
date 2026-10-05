"""SPC roles and competencies of people, and control plans. Every change is written together with its audit entry."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable

from spc.auth.audit import Audit
from spc.db.database import Database
from spc.db.stores import now_iso
from spc.plan import model as M
from spc.plan import roles as R
from spc.plan.model import PlanError


class PlanNotFound(KeyError):
    """No control plan with this id."""


class PlanNameTaken(ValueError):
    """Another control plan has this name."""


def _label(user) -> str:
    return f"{user.display_name or user.username} ({user.username})" if getattr(user, "display_name", "") else user.username


def _dump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


class PeopleService:
    """Who holds which of the seven SPC roles (draft 6.8.1) and what competence they have shown (table 6-1)."""

    def __init__(self, db: Database, audit: Audit, auth, today: Callable[[], date] | None = None):
        self.db, self.audit, self.auth = db, audit, auth
        self.today = today or date.today

    def _data(self, user_id: int) -> dict:
        r = self.db.one("SELECT data FROM spc_people WHERE user_id = ?", (user_id,))
        return json.loads(r["data"]) if r else {"roles": [], "competences": {}, "history": []}

    def _save(self, user_id: int, data: dict) -> None:
        self.db.execute("INSERT INTO spc_people (user_id, data, updated_at) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                        (user_id, _dump(data), now_iso()))

    def get(self, user_id: int) -> dict:
        user = self.auth.get_user(user_id)
        d = self._data(user_id)
        return {"user": user.to_json(), "roles": d["roles"], "competences": d["competences"], "history": d["history"][-50:],
                "qualification": R.qualification(d["roles"], d["competences"])}

    def list(self) -> list[dict]:
        out = []
        for u in self.auth.list_users():
            d = self._data(u.id)
            q = R.qualification(d["roles"], d["competences"])
            out.append({"user": u.to_json(), "roles": d["roles"], "qualification": {r: v["qualified"] for r, v in q.items()}})
        return out

    def set_roles(self, user_id: int, roles: list[str], actor) -> dict:
        user = self.auth.get_user(user_id)
        if not isinstance(roles, list) or any(r not in R.ROLES for r in roles) or len(set(roles)) != len(roles):
            raise PlanError("invalid_input", f"roles must be a list of different roles from {R.ROLES}")
        d = self._data(user_id)
        with self.db.tx():
            self._save(user_id, {**d, "roles": [r for r in R.ROLES if r in roles]})
            self.audit.append("spc_roles_set", user_id=actor.id, username=actor.username, target=user.username, detail={"user": user_id, "roles": roles})
        return self.get(user_id)

    def record_competence(self, user_id: int, competence: str, level: int, day: str, note: str, actor) -> dict:
        user = self.auth.get_user(user_id)
        if competence not in R.COMPETENCES:
            raise PlanError("invalid_input", f"competence must be one of {R.COMPETENCES}")
        if isinstance(level, bool) or level not in R.LEVELS:
            raise PlanError("invalid_input", "level must be 0 (none), 1 (basic) or 2 (expert)")
        try:
            when = date.fromisoformat(day)
        except (TypeError, ValueError):
            raise PlanError("invalid_input", "date must be a day as YYYY-MM-DD") from None
        if when > self.today():
            raise PlanError("invalid_input", "the date of a qualification cannot be in the future")
        if len(note) > 1000:
            raise PlanError("invalid_input", "the note is longer than 1000 characters")
        if level > 0 and len(note.strip()) < 3:
            raise PlanError("note_required", "say how the competence was shown (training, test, experience)")
        d = self._data(user_id)
        entry = {"level": level, "date": when.isoformat(), "note": note.strip(), "by": _label(actor), "at": now_iso()}
        d["competences"] = {**d["competences"], competence: entry}
        d["history"] = [*d["history"], {"competence": competence, **entry}][-200:]
        with self.db.tx():
            self._save(user_id, d)
            self.audit.append("spc_competence", user_id=actor.id, username=actor.username, target=user.username,
                              detail={"user": user_id, "competence": competence, "level": level, "date": entry["date"], "note": entry["note"]})
        return self.get(user_id)

    def staffing(self) -> dict[str, dict]:
        """For each role: how many active people hold it, and how many of them are qualified by table 6-1."""
        out = {r: {"assigned": 0, "qualified": 0} for r in R.ROLES}
        for u in self.auth.list_users():
            if not u.active:
                continue
            d = self._data(u.id)
            for role, q in R.qualification(d["roles"], d["competences"]).items():
                out[role]["assigned"] += 1
                out[role]["qualified"] += q["qualified"]
        return out

    def holds(self, user_id: int, role: str) -> bool:
        return role in self._data(user_id)["roles"]


class PlanService:
    def __init__(self, db: Database, audit: Audit, people: PeopleService, monitors=None, msa=None):
        self.db, self.audit, self.people, self.monitors, self.msa = db, audit, people, monitors, msa

    def _log(self, action: str, user, target: str, detail: dict) -> None:
        self.audit.append(action, user_id=user.id, username=user.username, target=target, detail=detail)

    # ------------------------------------------------------------------ storage
    @staticmethod
    def _row(r) -> dict:
        return {"id": r["id"], "created_at": r["created_at"], "updated_at": r["updated_at"], **json.loads(r["data"])}

    def get(self, plan_id: int) -> dict:
        r = self.db.one("SELECT * FROM control_plans WHERE id = ?", (plan_id,))
        if r is None:
            raise PlanNotFound(plan_id)
        return self._row(r)

    def _save(self, plan_id: int, data: dict) -> None:
        self.db.execute("UPDATE control_plans SET data = ?, name = ?, updated_at = ? WHERE id = ?", (_dump(data), data["name"], now_iso(), plan_id))

    @staticmethod
    def _payload(plan: dict) -> dict:
        return {k: v for k, v in plan.items() if k not in ("id", "created_at", "updated_at")}

    # ------------------------------------------------------------------ evaluation
    def _context(self, plan: dict) -> M.Context:
        monitors, gates = {}, {}
        for line in plan["lines"]:
            mid, sid = line.get("monitor_id"), line.get("msa_id")
            if mid and mid not in monitors and self.monitors is not None:
                try:
                    monitors[mid] = self.monitors.store.get(mid)
                except KeyError:
                    pass
            if sid and sid not in gates and self.msa is not None:
                try:
                    v = self.msa.view(sid)
                    gates[sid] = {"gate": v["gate"], "name": v["system"]["name"], "tolerance": v["system"]["tolerance"]}
                except KeyError:
                    pass
        return M.Context(monitors, gates, self.people.staffing())

    def evaluate(self, plan: dict) -> dict:
        return M.evaluate(plan, self._context(plan))

    def view(self, plan_id: int) -> dict:
        plan = self.get(plan_id)
        return {"plan": plan, "evaluation": self.evaluate(plan), "approvers": list(R.APPROVERS)}

    def list(self) -> list[dict]:
        out = []
        for r in self.db.all("SELECT * FROM control_plans ORDER BY name COLLATE NOCASE"):
            p = self._row(r)
            ev = self.evaluate(p)
            out.append({"id": p["id"], "name": p["name"], "part": p["part"], "process": p["process"], "phase": p["phase"], "status": p["status"],
                        "revision": p["content_revision"], "lines": len(p["lines"]), "ready": ev["ready"], "approvals": sorted(p["approvals"]),
                        "blockers": len(ev["blockers"]), "updated_at": p["updated_at"]})
        return out

    # ------------------------------------------------------------------ changes
    def create(self, record_in: dict, user) -> dict:
        try:
            record = M.validate_record(record_in)
        except ValueError as exc:
            raise PlanError("invalid_input", str(exc)) from None
        self._check_links(record)
        data = {**record, "status": "draft", "content_revision": 1, "approvals": {}, "released": [], "released_revision": None}
        with self.db.tx():
            if self.db.one("SELECT 1 FROM control_plans WHERE name = ?", (record["name"],)):
                raise PlanNameTaken(record["name"])
            stamp = now_iso()
            cur = self.db.execute("INSERT INTO control_plans (name, data, created_at, updated_at, created_by) VALUES (?, ?, ?, ?, ?)",
                                  (record["name"], _dump(data), stamp, stamp, user.id))
            self._log("plan_created", user, record["name"], {"id": cur.lastrowid, "lines": len(record["lines"])})
        return self.view(cur.lastrowid)

    def _check_links(self, record: dict) -> None:
        for i, line in enumerate(record["lines"], start=1):
            if line["monitor_id"] and self.monitors is not None:
                try:
                    self.monitors.store.get(line["monitor_id"])
                except KeyError:
                    raise PlanError("monitor_not_found", f"line {i}: the monitor does not exist", 404) from None
            if line["msa_id"] and self.msa is not None:
                try:
                    self.msa.get(line["msa_id"])
                except KeyError:
                    raise PlanError("msa_system_not_found", f"line {i}: the measurement system does not exist", 404) from None

    def update(self, plan_id: int, record_in: dict, user) -> dict:
        plan = self.get(plan_id)
        try:
            record = M.validate_record(record_in)
        except ValueError as exc:
            raise PlanError("invalid_input", str(exc)) from None
        self._check_links(record)
        changed = {k: record[k] for k in record if record[k] != plan[k]}
        data = {**self._payload(plan), **record}
        if changed:  # a change makes it a draft again and the approvals no longer cover it
            data.update(status="draft", approvals={}, content_revision=plan["content_revision"] + 1)
        with self.db.tx():
            if self.db.one("SELECT id FROM control_plans WHERE name = ? AND id != ?", (record["name"], plan_id)):
                raise PlanNameTaken(record["name"])
            self._save(plan_id, data)
            self._log("plan_updated", user, record["name"], {"id": plan_id, "revision": data["content_revision"], "changed": sorted(changed),
                                                              "was_released": plan["status"] == "released"})
        return self.view(plan_id)

    def approve(self, plan_id: int, role: str, note: str, user) -> dict:
        plan = self.get(plan_id)
        if role not in R.APPROVERS:
            raise PlanError("invalid_input", f"role must be one of {R.APPROVERS}")
        if not self.people.holds(user.id, role):
            raise PlanError("role_not_held", "you do not hold this SPC role: an administrator or engineer assigns it first", 403, role=role)
        if plan["status"] != "draft":
            raise PlanError("plan_not_draft", "only a draft is approved: a released plan is approved already", 409)
        if not plan["lines"]:
            raise PlanError("plan_empty", "there is nothing to approve: the plan has no lines", 409)
        if len(note) > 2000:
            raise PlanError("invalid_input", "the note is longer than 2000 characters")
        approvals = {**plan["approvals"], role: {"by": _label(user), "user_id": user.id, "at": now_iso(), "note": note.strip(), "revision": plan["content_revision"]}}
        with self.db.tx():
            self._save(plan_id, {**self._payload(plan), "approvals": approvals})
            self._log("plan_approved", user, plan["name"], {"id": plan_id, "role": role, "revision": plan["content_revision"], "note": note.strip()})
        return self.view(plan_id)

    def release(self, plan_id: int, reason: str, user) -> dict:
        plan = self.get(plan_id)
        if plan["status"] == "released":
            raise PlanError("plan_released", "the plan is released already", 409)
        if not reason or not reason.strip():
            raise PlanError("reason_required", "a statement is required to release the plan")
        ev = self.evaluate(plan)
        if not ev["ready"]:
            raise PlanError("plan_not_ready", "the plan is not complete or not approved by all roles", 409, blockers=ev["blockers"])
        entry = {"revision": plan["content_revision"], "at": now_iso(), "by": _label(user), "reason": reason.strip(), "remarks": ev["remarks"],
                 "approvals": plan["approvals"], "snapshot": {k: plan[k] for k in ("name", "part", "process", "phase", "description", "lines")}}
        with self.db.tx():
            self._save(plan_id, {**self._payload(plan), "status": "released", "released_revision": plan["content_revision"], "released": [*plan["released"], entry]})
            self._log("plan_released", user, plan["name"], {"id": plan_id, "revision": plan["content_revision"], "reason": reason.strip(), "remarks": ev["remarks"]})
        return self.view(plan_id)

    def withdraw(self, plan_id: int, reason: str, user) -> dict:
        plan = self.get(plan_id)
        if plan["status"] != "released":
            raise PlanError("plan_not_released", "the plan is a draft", 409)
        if not reason or not reason.strip():
            raise PlanError("reason_required", "a reason is required to withdraw the release")
        with self.db.tx():
            self._save(plan_id, {**self._payload(plan), "status": "draft", "approvals": {}, "content_revision": plan["content_revision"] + 1})
            self._log("plan_withdrawn", user, plan["name"], {"id": plan_id, "reason": reason.strip()})
        return self.view(plan_id)

    def delete(self, plan_id: int, user) -> None:
        plan = self.get(plan_id)
        with self.db.tx():
            self.db.execute("DELETE FROM control_plans WHERE id = ?", (plan_id,))
            self._log("plan_deleted", user, plan["name"], {"id": plan_id})

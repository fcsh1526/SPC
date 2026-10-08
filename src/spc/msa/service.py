"""Measurement systems: the record, the evidence (studies), waivers, and the gate. Every change is audited in the same transaction."""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any, Callable

from spc.auth.audit import Audit
from spc.core import msa_more
from spc.core import iso22514_7 as iso_core
from spc.msa import iso
from spc.core import msa
from spc.core import msa_aiag, msa_attribute
from spc.db.database import Database
from spc.db.stores import now_iso
from spc.msa import gate


class MsaProblem(ValueError):
    """A request that breaks the rules. `code` is the stable message code of the API."""

    def __init__(self, code: str, message: str = "", status: int = 400, **params):
        super().__init__(message or code)
        self.code, self.status, self.params = code, status, params


class SystemNotFound(KeyError):
    """No measurement system with this id."""


class SystemNameTaken(ValueError):
    """Another measurement system has this name."""


def _label(user) -> str:
    return f"{user.display_name or user.username} ({user.username})" if getattr(user, "display_name", "") else user.username


def _dump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def _positive(v, name):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not v > 0:
        raise ValueError(f"{name} must be a positive number")
    return float(v)


def validate_record(data: dict) -> dict:
    allowed = {"name", "kind", "description", "characteristic", "unit", "resolution", "tolerance", "policy"}
    if set(data) - allowed:
        raise ValueError(f"unknown setting(s): {sorted(set(data) - allowed)}")
    def text(key, limit, required=False):
        v = data.get(key, "")
        v = "" if v is None else v
        if not isinstance(v, str) or len(v) > limit or (required and not v.strip()):
            raise ValueError(f"{key} must be a text of at most {limit} characters" + (" and must not be empty" if required else ""))
        return v.strip()
    kind = data.get("kind") or "variable"
    if kind not in gate.SYSTEM_KINDS:
        raise ValueError(f"kind must be one of {gate.SYSTEM_KINDS}")
    return {"name": text("name", 100, True), "kind": kind, "description": text("description", 1000), "characteristic": text("characteristic", 200), "unit": text("unit", 40),
            "resolution": _positive(data.get("resolution"), "resolution"), "tolerance": _positive(data.get("tolerance"), "tolerance"),
            "policy": gate.validate_policy(data.get("policy"))}


def _check_date(value) -> str:
    try:
        d = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("date must be a day as YYYY-MM-DD") from None
    return d.isoformat()


class MsaService:
    def __init__(self, db: Database, audit: Audit, today: Callable[[], date] | None = None):
        self.db, self.audit = db, audit
        self.today = today or date.today

    def _log(self, action: str, user, target: str, detail: dict) -> None:
        self.audit.append(action, user_id=user.id, username=user.username, target=target, detail=detail)

    # ------------------------------------------------------------------ storage
    @staticmethod
    def _row(r) -> dict:
        return {"id": r["id"], "revision": r["revision"], "created_at": r["created_at"], "updated_at": r["updated_at"], **json.loads(r["data"])}

    def get(self, system_id: int) -> dict:
        r = self.db.one("SELECT * FROM msa_systems WHERE id = ?", (system_id,))
        if r is None:
            raise SystemNotFound(system_id)
        return self._row(r)

    def _save(self, system_id: int, data: dict) -> None:
        self.db.execute("UPDATE msa_systems SET data = ?, name = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                        (_dump(data), data["name"], now_iso(), system_id))

    @staticmethod
    def _payload(system: dict) -> dict:
        return {k: v for k, v in system.items() if k not in ("id", "revision", "created_at", "updated_at")}

    # ------------------------------------------------------------------ evidence: the results follow the tolerance and the policy
    def _compute(self, system: dict, study: dict) -> dict:
        """The result and the verdict of one study from its input, the tolerance and the policy of the system now."""
        pol, tol, inp = system["policy"], system.get("tolerance"), study["input"]
        if study["kind"] == "type1":
            if not tol:
                raise msa.MsaError("a type 1 study needs the tolerance of the measurement system")
            r = msa.type1(inp["values"], inp["reference"], tol, pol["cg_min"])
        elif study["kind"] == "grr":
            r = msa.grr(inp["data"], tol, pol["grr_pass"], pol["grr_conditional"], pol["ndc_min"])
        elif study["kind"] == "grr_nested":
            r = msa_more.grr_nested(inp["data"], tol, pol["grr_pass"], pol["grr_conditional"], pol["ndc_min"])
        elif study["kind"] == "linearity":
            r = msa_more.linearity(inp["reference"], inp["values"], inp.get("process_variation"))
        elif study["kind"] == "budget":
            r = msa_more.budget(inp["components"], tol, pol["k"], pol["budget_pass"], pol["budget_conditional"], inp.get("lsl"), inp.get("usl"))
        elif study["kind"] == "iso_study":
            r = iso.evaluate_iso(inp, tol, system.get("resolution"), pol)
        elif study["kind"] in gate.ISO_ATTRIBUTE_KINDS:
            r = iso.evaluate_attribute_iso(study["kind"], inp, pol)
        elif study["kind"] == "attribute":
            r = msa_attribute.evaluate(inp["ratings"], inp["reference"], gate.attribute_policy(pol))
        elif study["kind"] == "bias":
            r = msa_aiag.bias_independent(inp["values"], inp["reference"], inp.get("process_sd"), tol, 0.05, pol["grr_pass"], pol["grr_conditional"])
        elif study["kind"] == "bias_chart":
            r = msa_aiag.bias_control_chart(inp["subgroups"], inp["reference"], inp.get("process_sd"), tol, 0.05, pol["grr_pass"], pol["grr_conditional"])
        elif study["kind"] == "grr_range":
            r = msa_aiag.range_method(inp["a"], inp["b"], inp.get("process_sd"), tol, pol["grr_pass"], pol["grr_conditional"])
        elif study["kind"] == "signal_detection":
            r = msa_aiag.signal_detection(inp["reference"], msa_aiag.codes_from_ratings(inp["results"]), inp["lower"], inp["upper"], inp.get("process_sd"), pol["grr_pass"], pol["grr_conditional"])
        elif study["kind"] == "analytic":
            r = msa_aiag.analytic_method(inp["reference"], inp["accepts"], inp["limit"], inp.get("side", "lower"), tolerance=tol)
        else:
            r = msa.stability(inp["values"])
        return {**study, "result": r, "verdict": r["verdict"]}

    def with_results(self, system: dict) -> dict:
        studies = []
        for s in system["studies"]:
            try:
                studies.append(self._compute(system, s))
            except msa.MsaError as exc:  # a study whose data no longer fit (for example a tolerance removed): shown, not counted
                studies.append({**s, "result": None, "verdict": "unusable", "error": str(exc), "voided": s.get("voided") or {"reason": "unusable"}})
        return {**system, "studies": studies}

    def linearity_monitor(self, system_id: int, references: list, readings: list, epsilon: float = 0.05) -> dict:
        """ISO 22514-7, 11.2: the readings of standards measured after the study, transformed with the regression function of the newest linearity study of the system."""
        system = self.with_results(self.get(system_id))
        items = [x for x in system["studies"] if x["kind"] == "iso_study" and not x.get("voided") and x.get("result")
                 and (x["result"]["analyses"].get("linearity") or {}).get("method") == "anova"]
        if not items:
            raise MsaProblem("no_linearity_study", "the system has no ISO 22514-7 study with a linearity analysis of variance", 409)
        study = max(items, key=lambda x: (x["date"], x["id"]))
        lin = study["result"]["analyses"]["linearity"]
        try:
            out = iso_core.linearity_monitor(lin["beta0"], lin["beta1"], lin["residual_sd"], lin["n"] - 2, references, readings, epsilon)
        except msa.MsaError as exc:
            raise MsaProblem("invalid_input", str(exc)) from None
        return {**out, "study": study["id"], "beta0": lin["beta0"], "beta1": lin["beta1"]}

    def gate(self, system_id: int) -> dict:
        return gate.evaluate(self.with_results(self.get(system_id)), self.today())

    def view(self, system_id: int) -> dict:
        system = self.with_results(self.get(system_id))
        return {"system": system, "gate": gate.evaluate(system, self.today())}

    def list(self) -> list[dict]:
        out = []
        for r in self.db.all("SELECT * FROM msa_systems ORDER BY name COLLATE NOCASE"):
            s = self.with_results(self._row(r))
            g = gate.evaluate(s, self.today())
            out.append({"id": s["id"], "name": s["name"], "kind": s.get("kind", "variable"), "characteristic": s["characteristic"], "unit": s["unit"], "status": g["status"],
                        "blocking": g["blocking"], "remarks": g["remarks"], "waived": g["waived"], "U": (g["uncertainty"] or {}).get("U")})
        return out

    # ------------------------------------------------------------------ changes
    def create(self, record_in: dict, user) -> dict:
        try:
            record = validate_record(record_in)
        except ValueError as exc:
            raise MsaProblem("invalid_input", str(exc)) from None
        data = {**record, "studies": [], "waivers": {}, "next_study": 1}
        with self.db.tx():
            if self.db.one("SELECT 1 FROM msa_systems WHERE name = ?", (record["name"],)):
                raise SystemNameTaken(record["name"])
            stamp = now_iso()
            cur = self.db.execute("INSERT INTO msa_systems (name, revision, data, created_at, updated_at, created_by) VALUES (?, 1, ?, ?, ?, ?)",
                                  (record["name"], _dump(data), stamp, stamp, user.id))
            self._log("msa_system_created", user, record["name"], {"id": cur.lastrowid})
        return self.view(cur.lastrowid)

    def update(self, system_id: int, record_in: dict, user) -> dict:
        system = self.get(system_id)
        try:
            record = validate_record({"kind": system.get("kind", "variable"), **record_in})
        except ValueError as exc:
            raise MsaProblem("invalid_input", str(exc)) from None
        if record["kind"] != system.get("kind", "variable") and system["studies"]:
            raise MsaProblem("invalid_input", "the kind of a system cannot change once it has studies")
        with self.db.tx():
            if self.db.one("SELECT id FROM msa_systems WHERE name = ? AND id != ?", (record["name"], system_id)):
                raise SystemNameTaken(record["name"])
            self._save(system_id, {**self._payload(system), **record})
            self._log("msa_system_updated", user, record["name"], {"id": system_id, "tolerance": record["tolerance"], "resolution": record["resolution"],
                                                                  "policy": record["policy"]})
        return self.view(system_id)

    def add_study(self, system_id: int, kind: str, day: str, note: str, input_: dict, user) -> dict:
        system = self.get(system_id)
        if kind not in gate.STUDY_KINDS_OF[system.get("kind", "variable")]:
            raise MsaProblem("invalid_input", f"a system of the kind {system.get('kind', 'variable')!r} takes studies of the kinds {gate.STUDY_KINDS_OF[system.get('kind', 'variable')]}")
        try:
            day = _check_date(day)
        except ValueError as exc:
            raise MsaProblem("invalid_input", str(exc)) from None
        if date.fromisoformat(day) > self.today():
            raise MsaProblem("invalid_input", "the date of a study cannot be in the future")
        if len(note) > 2000:
            raise MsaProblem("invalid_input", "the note is longer than 2000 characters")
        if not isinstance(input_, dict):
            raise MsaProblem("invalid_input", "input must be an object")
        need = {"type1": {"values", "reference"}, "grr": {"data"}, "grr_nested": {"data"}, "stability": {"values"}, "attribute": {"ratings", "reference"},
                "linearity": {"values", "reference"}, "budget": {"components"}, "iso_study": set(), "bowker": {"results"},
                "uncertainty_range": {"reference", "results", "lower", "upper"}, "attribute_review": {"reference", "results", "lower", "upper"},
                "bias": {"values", "reference"}, "bias_chart": {"subgroups", "reference"}, "grr_range": {"a", "b"}, "signal_detection": {"reference", "results", "lower", "upper"},
                "analytic": {"reference", "accepts", "limit"}}[kind]
        optional = {"linearity": {"process_variation"}, "budget": {"lsl", "usl"}, "iso_study": set(iso.ISO_KEYS), "bowker": {"alpha"}, "attribute_review": {"q_mp"},
                "bias": {"process_sd"}, "bias_chart": {"process_sd"}, "grr_range": {"process_sd"}, "signal_detection": {"process_sd"}, "analytic": {"side"}}.get(kind, set())
        if not need <= set(input_) <= need | optional:
            raise MsaProblem("invalid_input", f"the input of a {kind} study holds {sorted(need)}" + (f" and may hold {sorted(optional)}" if optional else ""))
        study = {"id": system["next_study"], "kind": kind, "date": day, "by": _label(user), "at": now_iso(), "note": note.strip(), "input": input_}
        try:
            computed = self._compute(system, study)
        except msa.MsaError as exc:
            raise MsaProblem("study_not_evaluable", str(exc)) from None
        except (TypeError, KeyError, ValueError) as exc:
            raise MsaProblem("invalid_input", f"the data cannot be read: {exc}") from None
        data = self._payload(system)
        data["studies"] = [*system["studies"], study]
        data["next_study"] = system["next_study"] + 1
        with self.db.tx():
            self._save(system_id, data)
            self._log("msa_study_added", user, system["name"], {"id": system_id, "study": study["id"], "kind": kind, "date": day, "verdict": computed["verdict"]})
        return self.view(system_id)

    def void_study(self, system_id: int, study_id: int, reason: str, user) -> dict:
        system = self.get(system_id)
        if not reason or len(reason.strip()) < 3:
            raise MsaProblem("reason_required", "a reason is required to void a study")
        studies = []
        found = False
        for s in system["studies"]:
            if s["id"] == study_id:
                found = True
                if s.get("voided"):
                    raise MsaProblem("already_voided", "the study is void already", 409)
                s = {**s, "voided": {"by": _label(user), "at": now_iso(), "reason": reason.strip()}}
            studies.append(s)
        if not found:
            raise MsaProblem("msa_study_not_found", "no such study", 404)
        with self.db.tx():
            self._save(system_id, {**self._payload(system), "studies": studies})
            self._log("msa_study_voided", user, system["name"], {"id": system_id, "study": study_id, "reason": reason.strip()})
        return self.view(system_id)

    def set_waiver(self, system_id: int, check: str, reason: str, user) -> dict:
        system = self.get(system_id)
        if check not in gate.CHECKS:
            raise MsaProblem("invalid_input", f"check must be one of {gate.CHECKS}")
        if not reason or len(reason.strip()) < 3:
            raise MsaProblem("reason_required", "a reason is required: say what was agreed and with whom")
        waivers = {**system["waivers"], check: {"by": _label(user), "at": now_iso(), "reason": reason.strip()}}
        with self.db.tx():
            self._save(system_id, {**self._payload(system), "waivers": waivers})
            self._log("msa_waiver_set", user, system["name"], {"id": system_id, "check": check, "reason": reason.strip()})
        return self.view(system_id)

    def remove_waiver(self, system_id: int, check: str, user) -> dict:
        system = self.get(system_id)
        if check not in system["waivers"]:
            raise MsaProblem("waiver_not_found", "no such waiver", 404)
        waivers = {k: v for k, v in system["waivers"].items() if k != check}
        with self.db.tx():
            self._save(system_id, {**self._payload(system), "waivers": waivers})
            self._log("msa_waiver_removed", user, system["name"], {"id": system_id, "check": check})
        return self.view(system_id)

    def delete(self, system_id: int, user) -> None:
        system = self.get(system_id)
        with self.db.tx():
            self.db.execute("DELETE FROM msa_systems WHERE id = ?", (system_id,))
            self._log("msa_system_deleted", user, system["name"], {"id": system_id})

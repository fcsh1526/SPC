"""The control plan (draft 6.7): a central, multidisciplinary document with all process steps, product characteristics and process
parameters that must be controlled; the measuring systems, measurement and inspection methods; the sample sizes and frequencies; and
the reaction to a violation of control or tolerance limits. It is the basis of the inspection plans and the foundation of every SPC
application (6.7), so a line can point to the measurement system (MSA gate) and to the SPC monitor that carries it out.

A plan is *released* only when its lines are complete, the measurement systems that it names are not blocked, there is room between
the tolerance and the measurement uncertainty, and the roles that the draft asks to coordinate (6.8.1: product developer, process planner,
inspection planner, with the owner of the SPC process) have approved it. A change after the release makes it a draft again.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from spc.plan import roles as R

PHASES = ("pre_launch", "safe_launch", "production")  # draft 6.7: initial acceptance and release of equipment, pre-launch, safe launch
KINDS = ("product_characteristic", "process_parameter")
CLASSES = ("critical", "major", "minor", "others")
CONTROLS = ("spc_chart", "full_inspection", "sampling_inspection", "other")
MEASURED = ("spc_chart", "full_inspection", "sampling_inspection")  # these need a measurement system
MAX_LINES = 300
TEXT = 2000


class PlanError(ValueError):
    """A request that breaks the rules. `code` is the stable message code of the API."""

    def __init__(self, code: str, message: str = "", status: int = 400, **params):
        super().__init__(message or code)
        self.code, self.status, self.params = code, status, params


def _text(d: Mapping, key: str, limit: int, where: str = "") -> str:
    v = d.get(key, "")
    v = "" if v is None else v
    if not isinstance(v, str) or len(v) > limit:
        raise ValueError(f"{where}{key} must be a text of at most {limit} characters")
    return v.strip()


def _num(v, name: str, allow_none=True, positive=False):
    if v is None:
        if allow_none:
            return None
        raise ValueError(f"{name} is required")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (positive and not v > 0):
        raise ValueError(f"{name} must be a{' positive' if positive else ''} number")
    return float(v)


def _id(v, name: str):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ValueError(f"{name} must be an id")
    return v


def validate_line(raw: Mapping[str, Any], n: int) -> dict:
    where = f"line {n}: "
    allowed = {"step", "kind", "characteristic", "unit", "target", "lsl", "usl", "class", "msa_id", "method", "sample_size", "frequency", "control",
               "monitor_id", "reaction", "responsible", "note", "instruction_ref"}
    if set(raw) - allowed:
        raise ValueError(f"{where}unknown setting(s): {sorted(set(raw) - allowed)}")
    kind = raw.get("kind", "product_characteristic")
    if kind not in KINDS:
        raise ValueError(f"{where}kind must be one of {KINDS}")
    cls = raw.get("class")
    if cls not in (None, *CLASSES):
        raise ValueError(f"{where}class must be one of {CLASSES}")
    control = raw.get("control", "spc_chart")
    if control not in CONTROLS:
        raise ValueError(f"{where}control must be one of {CONTROLS}")
    lsl, usl, target = _num(raw.get("lsl"), f"{where}lsl"), _num(raw.get("usl"), f"{where}usl"), _num(raw.get("target"), f"{where}target")
    if lsl is not None and usl is not None and not lsl < usl:
        raise ValueError(f"{where}lsl must be below usl")
    size = raw.get("sample_size")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 100000):
        raise ValueError(f"{where}sample_size must be a whole number from 1")
    resp = raw.get("responsible", [])
    if not isinstance(resp, list) or len(resp) > len(R.ROLES) or any(r not in R.ROLES for r in resp) or len(set(resp)) != len(resp):
        raise ValueError(f"{where}responsible must be a list of the roles {R.ROLES}")
    return {"step": _text(raw, "step", 200, where), "kind": kind, "characteristic": _text(raw, "characteristic", 200, where), "unit": _text(raw, "unit", 40, where),
            "target": target, "lsl": lsl, "usl": usl, "class": cls, "msa_id": _id(raw.get("msa_id"), f"{where}msa_id"), "method": _text(raw, "method", TEXT, where),
            "sample_size": size, "frequency": _text(raw, "frequency", 200, where), "control": control, "monitor_id": _id(raw.get("monitor_id"), f"{where}monitor_id"),
            "reaction": _text(raw, "reaction", TEXT, where), "responsible": list(resp), "note": _text(raw, "note", TEXT, where),
            "instruction_ref": _text(raw, "instruction_ref", 200, where)}


def validate_record(data: Mapping[str, Any]) -> dict:
    allowed = {"name", "part", "process", "phase", "description", "lines"}
    if set(data) - allowed:
        raise ValueError(f"unknown setting(s): {sorted(set(data) - allowed)}")
    name = _text(data, "name", 100)
    if not name:
        raise ValueError("name must not be empty")
    phase = data.get("phase", "production")
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    lines = data.get("lines", [])
    if not isinstance(lines, list) or len(lines) > MAX_LINES:
        raise ValueError(f"lines must be a list of at most {MAX_LINES} entries")
    out_lines = []
    for i, raw in enumerate(lines, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"line {i} must be an object")
        out_lines.append(validate_line(raw, i))
    return {"name": name, "part": _text(data, "part", 200), "process": _text(data, "process", 200), "phase": phase,
            "description": _text(data, "description", TEXT), "lines": out_lines}


# ---------------------------------------------------------------------------------------------------- checks

def check_line(line: Mapping, ctx: "Context") -> list[dict]:
    """The checks of one line. result: pass, warn (remark), fail (blocks the release)."""
    out: list[dict] = []

    def add(key, result, **detail):
        out.append({"key": key, "result": result, **detail})

    missing = [f for f, ok in (("characteristic", bool(line["characteristic"])), ("step", bool(line["step"])),
                               ("responsible", bool(line["responsible"]))) if not ok]
    if line["control"] != "full_inspection":
        missing += [f for f, ok in (("sample_size", line["sample_size"] is not None), ("frequency", bool(line["frequency"]))) if not ok]
    if line["control"] in MEASURED:
        missing += [f for f, ok in (("method", bool(line["method"])),) if not ok]
    add("complete", "fail" if missing else "pass", missing=missing)
    # a product characteristic has a specification limit; a process parameter may only have a set point
    if line["kind"] == "product_characteristic" and line["lsl"] is None and line["usl"] is None:
        add("specification", "fail")
    elif line["lsl"] is None and line["usl"] is None and line["target"] is None:
        add("specification", "warn")
    else:
        add("specification", "pass")
    # the control method
    if line["control"] == "spc_chart":
        m = ctx.monitors.get(line["monitor_id"]) if line["monitor_id"] else None
        if line["monitor_id"] is None:
            add("monitor", "fail", reason="no_monitor")
        elif m is None:
            add("monitor", "fail", reason="monitor_missing")
        elif not m["active"]:
            add("monitor", "warn", reason="monitor_inactive", name=m["name"])
        else:
            add("monitor", "pass", name=m["name"], kind=m["kind"])
        reaction_ok = bool(line["reaction"]) or bool(m and (m["ocap"]["default"]["operator_action"] or m["ocap"]["rules"]))
        add("reaction", "pass" if reaction_ok else "fail", from_monitor=bool(m and not line["reaction"] and reaction_ok))
    else:
        add("reaction", "pass" if line["reaction"] else "fail", from_monitor=False)
    # the measurement system and the room between tolerance and uncertainty
    if line["control"] in MEASURED:
        if line["msa_id"] is None:
            add("msa", "fail", reason="no_system")
        else:
            g = ctx.gates.get(line["msa_id"])
            if g is None:
                add("msa", "fail", reason="system_missing")
            else:
                add("msa", {"pass": "pass", "conditional": "warn", "block": "fail"}[g["gate"]["status"]], name=g["name"], status=g["gate"]["status"],
                    blocking=g["gate"]["blocking"])
                u = g["gate"].get("uncertainty")
                if u and line["lsl"] is not None and line["usl"] is not None:
                    tol = line["usl"] - line["lsl"]
                    gb = u["guard_band"]
                    room = tol - 2.0 * gb
                    detail = {"U": u["U"], "guard_band": gb, "tolerance": tol, "share": u["U"] / tol, "accept_low": line["lsl"] + gb, "accept_high": line["usl"] - gb}
                    add("uncertainty", "pass" if room > 0 else "fail", **detail)
                    if g["tolerance"] and abs(g["tolerance"] - tol) > 1e-9 * max(1.0, abs(tol)):
                        add("system_tolerance", "warn", system=g["tolerance"], line=tol)
    elif line["msa_id"] is not None:
        add("msa", "warn", reason="not_needed")
    return out


class Context:
    """What the checks need to know about the rest of the system: monitors, the gates of measurement systems and who holds which role."""

    def __init__(self, monitors: Mapping[int, dict], gates: Mapping[int, dict], staffing: Mapping[str, dict]):
        self.monitors, self.gates, self.staffing = monitors, gates, staffing


def evaluate(plan: Mapping, ctx: Context) -> dict[str, Any]:
    lines = {}
    blockers: list[str] = []
    remarks: list[str] = []
    for i, line in enumerate(plan["lines"], start=1):
        checks = check_line(line, ctx)
        lines[str(i)] = checks
        blockers += [f"{i}:{c['key']}" for c in checks if c["result"] == "fail"]
        remarks += [f"{i}:{c['key']}" for c in checks if c["result"] == "warn"]
    approvals = plan.get("approvals", {})
    missing_approvals = [r for r in R.APPROVERS if r not in approvals]
    used = sorted({r for line in plan["lines"] for r in line["responsible"]})
    staffing = {r: ctx.staffing.get(r, {"assigned": 0, "qualified": 0}) for r in used}
    unstaffed = [r for r, s in staffing.items() if s["qualified"] == 0]
    plan_checks = [{"key": "has_lines", "result": "pass" if plan["lines"] else "fail"},
                   {"key": "approvals", "result": "pass" if not missing_approvals else "fail", "missing": missing_approvals},
                   {"key": "staffing", "result": "pass" if not unstaffed else "warn", "unstaffed": unstaffed, "roles": staffing}]
    plan_blockers = [c["key"] for c in plan_checks if c["result"] == "fail"]
    return {"plan": plan_checks, "lines": lines, "line_blockers": blockers, "remarks": remarks + [c["key"] for c in plan_checks if c["result"] == "warn"],
            "blockers": blockers + plan_blockers, "ready": not blockers and not plan_blockers}

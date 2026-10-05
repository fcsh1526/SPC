"""The checklist of a machine performance study (draft 8.1 to 8.3) and its evaluation.

Every item belongs to a section of the draft. A *manual* item is confirmed by a person (status ok, not_applicable or deviation, with
a note where the draft asks for an agreement). An *auto* item is evaluated from the study record and the linked data set; the person
can still record a deviation (with a reason) when an agreement says otherwise. A study can be closed only when no item blocks.

Item results (`effective`): ok, warn (passes with a remark), fail, open (blocks), not_applicable, deviation (recorded, passes, flagged),
not_done (an optional item that was not done).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

STATUSES = ("open", "ok", "not_applicable", "deviation")
MIN_PARTS = 50  # draft 8.2.1: "usually 50"
MIN_DRESSING_CYCLES = 1.5  # draft 8.2.1: "at least 1.5 dressing cycles"
PRE_CENTER_SHARE = 0.125  # draft 8.2.5: within +-12.5 % of the tolerance centre
PRE_NATURAL_SHARE = 0.625  # draft 8.2.5: within 62.5 % of the tolerance (one-sided, natural limit)
PRE_RANGE_SHARE = 0.25  # draft 8.2.5: range smaller than 25 % of the tolerance


@dataclass(frozen=True)
class Item:
    key: str
    section: str
    auto: bool = False
    optional: bool = False


ITEMS: tuple[Item, ...] = (
    Item("human", "8.1"), Item("material_constant", "8.1"), Item("method", "8.1"), Item("milieu", "8.1"),
    Item("machine_parameters", "8.1"), Item("changes_documented", "8.1"), Item("deviations_agreed", "8.1"),
    Item("sample_size", "8.2.1", auto=True), Item("tool_wear_cycles", "8.2.1", auto=True),
    Item("material_homogeneous", "8.2.2"),
    Item("msa_evidence", "8.2.3", auto=True),
    Item("warmed_up", "8.2.4"), Item("tools_conditioned", "8.2.4"), Item("uninterrupted", "8.2.4"),
    Item("adjusted_to_center", "8.2.4", auto=True),
    Item("preproduction_1", "8.2.5", auto=True, optional=True), Item("preproduction_5", "8.2.5", auto=True, optional=True),
    Item("stations", "8.2.6", auto=True),
    Item("traceability", "8.3.1", auto=True),
    Item("parts_retained", "8.3.2"),
    Item("data_numeric", "8.3.3", auto=True),
    Item("qualitative_stability", "8.3.4.1"),
    Item("distribution", "8.3.4.2", auto=True),
)
KEYS = tuple(i.key for i in ITEMS)
BY_KEY = {i.key: i for i in ITEMS}


class StudyError(ValueError):
    """A study record or a change that breaks the rules. `code` is the stable message code of the API."""

    def __init__(self, code: str, message: str = "", status: int = 400, **params):
        super().__init__(message or code)
        self.code, self.status, self.params = code, status, params


def _num(v, name: str, lo=None, hi=None, allow_none=True):
    if v is None:
        if allow_none:
            return None
        raise ValueError(f"{name} is required")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValueError(f"{name} must be a number" + (f" from {lo}" if lo is not None else "") + (f" to {hi}" if hi is not None else ""))
    return float(v)


def _text(d: Mapping, key: str, limit: int, required: bool = False) -> str:
    v = d.get(key, "")
    if v is None:
        v = ""
    if not isinstance(v, str) or len(v) > limit or (required and not v.strip()):
        raise ValueError(f"{key} must be a text of at most {limit} characters" + (" and must not be empty" if required else ""))
    return v.strip()


def validate_record(data: Mapping[str, Any]) -> dict:
    """Check and normalise the descriptive part of a study (everything except the items and the closing)."""
    allowed = {"name", "machine", "characteristic", "station", "unit", "dataset_id", "specs", "sample", "preproduction", "measurement_system_id"}
    if set(data) - allowed:
        raise ValueError(f"unknown setting(s): {sorted(set(data) - allowed)}")
    out: dict[str, Any] = {"name": _text(data, "name", 100, True), "machine": _text(data, "machine", 200), "characteristic": _text(data, "characteristic", 200, True),
                           "station": _text(data, "station", 100), "unit": _text(data, "unit", 40)}
    ds = data.get("dataset_id")
    if ds is not None and (not isinstance(ds, str) or not 0 < len(ds) <= 64):
        raise ValueError("dataset_id must be the id of a stored data set")
    out["dataset_id"] = ds
    msa_id = data.get("measurement_system_id")
    if msa_id is not None and (isinstance(msa_id, bool) or not isinstance(msa_id, int) or msa_id < 1):
        raise ValueError("measurement_system_id must be the id of a measurement system")
    out["measurement_system_id"] = msa_id
    specs = dict(data.get("specs") or {})
    if set(specs) - {"lsl", "usl", "natural"}:
        raise ValueError("specs: unknown setting(s)")
    lsl, usl = _num(specs.get("lsl"), "specs.lsl"), _num(specs.get("usl"), "specs.usl")
    if lsl is not None and usl is not None and not lsl < usl:
        raise ValueError("specs: lsl must be below usl")
    natural = specs.get("natural")
    if natural not in (None, "lsl", "usl"):
        raise ValueError("specs.natural must be lsl or usl: the limit that is a natural limit (such as 0 for a flatness)")
    if natural and (lsl is None or usl is None):
        raise ValueError("a one-sided characteristic with a natural limit needs the natural limit and the specification limit")
    out["specs"] = {"lsl": lsl, "usl": usl, "natural": natural}
    sample = dict(data.get("sample") or {})
    if set(sample) - {"reduced_approved_by", "reduced_reason", "tool_wear_high", "dressing_cycles"}:
        raise ValueError("sample: unknown setting(s)")
    out["sample"] = {"reduced_approved_by": _text(sample, "reduced_approved_by", 200), "reduced_reason": _text(sample, "reduced_reason", 1000),
                     "tool_wear_high": bool(sample.get("tool_wear_high", False)), "dressing_cycles": _num(sample.get("dressing_cycles"), "sample.dressing_cycles", 0, 1000)}
    pre = dict(data.get("preproduction") or {})
    if set(pre) - {"one", "five"}:
        raise ValueError("preproduction: unknown setting(s)")
    one = pre.get("one")
    five = pre.get("five")
    if one is not None:
        one = _num(one, "preproduction.one")
    if five is not None:
        if not isinstance(five, list) or len(five) != 5:
            raise ValueError("preproduction.five needs 5 values")
        five = [_num(v, "preproduction.five", allow_none=False) for v in five]
    out["preproduction"] = {"one": one, "five": five}
    return out


def _region(specs: Mapping) -> tuple[float, float, float] | None:
    """(lower, upper, tolerance) of the pre-run area of the draft's figure 8-1, or None when the limits are not complete."""
    lsl, usl, nat = specs.get("lsl"), specs.get("usl"), specs.get("natural")
    if lsl is None or usl is None:
        return None
    tol = usl - lsl
    if nat == "lsl":
        return lsl, lsl + PRE_NATURAL_SHARE * tol, tol
    if nat == "usl":
        return usl - PRE_NATURAL_SHARE * tol, usl, tol
    c = (lsl + usl) / 2.0
    return c - PRE_CENTER_SHARE * tol, c + PRE_CENTER_SHARE * tol, tol


def facts_from_dataset(ds, normality: dict | None = None) -> dict:
    """What the checklist needs to know about a linked data set."""
    vals = ds.values[ds.valid_mask]
    return {"n_total": int(ds.n_total), "n_valid": int(ds.n_valid), "n_invalid": int(ds.n_invalid), "has_timestamps": ds.timestamp is not None,
            "mean": float(vals.mean()) if vals.size else None, "n_restarts": sum(1 for e in ds.log if e.action.startswith("restart")),
            "normality": normality}


def auto_results(record: Mapping, facts: Mapping | None, dataset_missing: bool = False, msa: Mapping | None = None) -> dict[str, dict]:
    """Result of every auto item: {'result': pass|warn|fail|unknown|not_done|not_needed, ...details}."""
    out: dict[str, dict] = {}
    # 8.2.3 proof of the measurement process: the MSA gate of the linked measurement system
    if msa is None:
        out["msa_evidence"] = {"result": "unknown", "reason": "no_system"}
    elif msa["state"] == "missing":
        out["msa_evidence"] = {"result": "unknown", "reason": "system_missing"}
    else:
        g = msa["gate"]
        out["msa_evidence"] = {"result": {"pass": "pass", "conditional": "warn", "block": "fail"}[g["status"]], "system": msa["name"], "status": g["status"],
                               "blocking": g["blocking"], "remarks": g["remarks"], "waived": g["waived"]}
    specs, sample = record["specs"], record["sample"]
    region = _region(specs)
    # 8.2.1 number of parts
    if facts is None:
        out["sample_size"] = {"result": "unknown", "reason": "dataset_missing" if dataset_missing else "no_dataset"}
    else:
        n = facts["n_valid"]
        if n >= MIN_PARTS:
            out["sample_size"] = {"result": "pass", "n": n, "needed": MIN_PARTS}
        elif sample["reduced_approved_by"] and sample["reduced_reason"]:
            out["sample_size"] = {"result": "warn", "n": n, "needed": MIN_PARTS, "reason": "reduced_approved"}
        else:
            out["sample_size"] = {"result": "fail", "n": n, "needed": MIN_PARTS, "reason": "reduced_not_approved"}
    # 8.2.1 tool wear: at least 1.5 dressing cycles
    if not sample["tool_wear_high"]:
        out["tool_wear_cycles"] = {"result": "not_needed"}
    else:
        c = sample["dressing_cycles"]
        out["tool_wear_cycles"] = {"result": "fail" if c is None or c < MIN_DRESSING_CYCLES else "pass", "cycles": c, "needed": MIN_DRESSING_CYCLES}
    # 8.2.4 adjusted close to the middle of the tolerance (two-sided only)
    if specs["natural"] or specs["lsl"] is None or specs["usl"] is None:
        out["adjusted_to_center"] = {"result": "not_needed"}
    elif facts is None or facts["mean"] is None:
        out["adjusted_to_center"] = {"result": "unknown", "reason": "no_dataset"}
    else:
        tol = specs["usl"] - specs["lsl"]
        off = abs(facts["mean"] - (specs["lsl"] + specs["usl"]) / 2.0) / tol
        out["adjusted_to_center"] = {"result": "pass" if off <= PRE_CENTER_SHARE else "warn", "offset_share": off, "limit": PRE_CENTER_SHARE}
    # 8.2.5 pre-production run, optional
    pre = record["preproduction"]
    for key, vals in (("preproduction_1", None if pre["one"] is None else [pre["one"]]), ("preproduction_5", pre["five"])):
        if vals is None:
            out[key] = {"result": "not_done"}
        elif region is None:
            out[key] = {"result": "unknown", "reason": "specs_incomplete"}
        else:
            lo, hi, tol = region
            location = sum(vals) / len(vals)
            in_area = lo <= location <= hi
            ok_range = True if len(vals) == 1 else (max(vals) - min(vals)) < PRE_RANGE_SHARE * tol
            out[key] = {"result": "pass" if in_area and ok_range else "fail", "location": location, "area": [lo, hi],
                        "range": None if len(vals) == 1 else max(vals) - min(vals), "range_limit": PRE_RANGE_SHARE * tol, "location_ok": in_area, "range_ok": ok_range}
    # 8.2.6 each station, clamping device or cavity is its own machine
    out["stations"] = {"result": "pass" if record["station"] else "warn", "station": record["station"]}
    # 8.3.1 data timeline, 8.3.3 numeric data
    if facts is None:
        out["traceability"] = {"result": "unknown", "reason": "dataset_missing" if dataset_missing else "no_dataset"}
        out["data_numeric"] = dict(out["traceability"])
        out["distribution"] = dict(out["traceability"])
    else:
        out["traceability"] = {"result": "pass" if facts["has_timestamps"] else "warn", "has_timestamps": facts["has_timestamps"]}
        out["data_numeric"] = {"result": "pass" if facts["n_invalid"] == 0 else "warn", "n_invalid": facts["n_invalid"], "n_total": facts["n_total"]}
        nm = facts.get("normality")
        out["distribution"] = ({"result": "pass", "test": nm["test"], "p_value": nm["p_value"]} if nm and nm.get("p_value") is not None
                               else {"result": "unknown", "reason": "no_test"})
    return out


def evaluate(record: Mapping, items: Mapping[str, Mapping], facts: Mapping | None, dataset_missing: bool = False, msa: Mapping | None = None) -> dict:
    """The state of every item and the readiness of the study."""
    auto = auto_results(record, facts, dataset_missing, msa)
    rows, blockers = [], []
    for item in ITEMS:
        rec = items.get(item.key) or {}
        status = rec.get("status", "open")
        a = auto.get(item.key)
        if item.auto and status in ("deviation", "not_applicable"):
            effective = status
        elif item.auto:
            effective = {"pass": "ok", "warn": "warn", "fail": "fail", "unknown": "open", "not_done": "not_done", "not_needed": "not_applicable"}[a["result"]]
        else:
            effective = status
        if item.optional and effective in ("open", "not_done"):
            effective = "not_done"
        blocking = effective in ("open", "fail")
        if blocking:
            blockers.append(item.key)
        rows.append({"key": item.key, "section": item.section, "auto": item.auto, "optional": item.optional, "status": status,
                     "note": rec.get("note", ""), "by": rec.get("by"), "at": rec.get("at"), "result": a, "effective": effective, "blocking": blocking})
    flagged = [r["key"] for r in rows if r["effective"] in ("warn", "deviation")]
    return {"items": rows, "ready": not blockers, "blockers": blockers, "flagged": flagged,
            "counts": {s: sum(1 for r in rows if r["effective"] == s) for s in ("ok", "warn", "fail", "open", "not_applicable", "deviation", "not_done")}}

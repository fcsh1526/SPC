"""The MSA gate: may data from this measurement system be used for a capability study, a monitor or a report?

The draft assumes a capable and stable measurement process (1, 6.3) and asks for proof in 8.2.3. The gate turns that into checks:

  resolution  the resolution is at most 5 % of the tolerance (policy)
  grr         the latest gauge R&R (crossed, or nested for destructive measurement) is capable (conditional counts as a remark)
  type1       a type 1 study, when there is one, is capable (optional)
  validity    the latest gauge R&R is not older than the validity time (12 months)
  linearity   a linearity and bias study, when there is one, shows the line "bias = 0" inside the confidence band (optional)
  budget      an uncertainty budget, when there is one, gives 2U/T within the limits of the policy (optional)
  stability   repeated measurements of a reference show no signal and are not older than the check interval (when required)

A system of the kind `attribute` (go / no-go) has the checks `attribute` (the latest attribute agreement study is capable, `spc.core.msa_attribute`) and
`validity` (that study is not older than the validity time); the checks of a variable system do not apply to it, and the other way round.

Result of a check: pass, warn (conditional), fail, missing (no evidence), not_done (optional), not_needed, or waived (recorded with
a reason by an engineer: it passes and stays flagged). The gate is `block` when a check failed or is missing without a waiver,
`conditional` when only remarks are left, `pass` otherwise. A blocked gate refuses; a conditional one is allowed and flagged.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Mapping

from spc.core import msa
from spc.core import msa_attribute

POLICY_DEFAULTS: dict[str, Any] = {
    "validity_months": 12, "stability_months": 6, "resolution_share_max": 0.05, "grr_pass": 10.0, "grr_conditional": 30.0, "ndc_min": 5.0,
    "cg_min": 1.33, "require_stability": True, "k": 2.0, "guard_band_risk": 0.05, "u_cal": 0.0, "budget_pass": 15.0, "budget_conditional": 30.0,
    **{f"attr_{k}": v for k, v in msa_attribute.POLICY_DEFAULTS.items()},
}
CHECKS = ("resolution", "grr", "type1", "validity", "stability", "attribute", "linearity", "budget")
STUDY_KINDS = ("type1", "grr", "grr_nested", "stability", "linearity", "budget", "attribute")
SYSTEM_KINDS = ("variable", "attribute")
STUDY_KINDS_OF = {"variable": ("type1", "grr", "grr_nested", "stability", "linearity", "budget"), "attribute": ("attribute",)}
GRR_KINDS = ("grr", "grr_nested")  # the crossed study, and the nested one for destructive measurement: either proves repeatability and reproducibility


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    m += 1
    last = [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def latest(system: Mapping, kind: str) -> dict | None:
    items = [s for s in system["studies"] if s["kind"] == kind and not s.get("voided")]
    return max(items, key=lambda s: (s["date"], s["id"])) if items else None


def latest_grr(system: Mapping) -> dict | None:
    items = [s for s in system["studies"] if s["kind"] in GRR_KINDS and not s.get("voided") and s.get("result")]
    return max(items, key=lambda s: (s["date"], s["id"])) if items else None


def validate_policy(data: Mapping | None) -> dict:
    p = {**POLICY_DEFAULTS, **dict(data or {})}
    if set(p) - set(POLICY_DEFAULTS):
        raise ValueError(f"policy: unknown setting(s) {sorted(set(p) - set(POLICY_DEFAULTS))}")
    def num(key, lo, hi, whole=False):
        v = p[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi or (whole and v != int(v)):
            raise ValueError(f"policy.{key} must be a {'whole ' if whole else ''}number from {lo} to {hi}")
        p[key] = int(v) if whole else float(v)
    num("validity_months", 1, 60, True); num("stability_months", 1, 60, True); num("resolution_share_max", 0.001, 0.5)
    num("grr_pass", 1, 50); num("grr_conditional", 1, 100); num("ndc_min", 1, 20); num("cg_min", 0.5, 5); num("k", 1, 4)
    num("guard_band_risk", 0.001, 0.499); num("u_cal", 0, 1e9); num("budget_pass", 1, 100); num("budget_conditional", 1, 200)
    if not p["budget_pass"] < p["budget_conditional"]:
        raise ValueError("policy: the budget limit for capable must be below the limit for conditional")
    if not p["grr_pass"] < p["grr_conditional"]:
        raise ValueError("policy: the limit for capable must be below the limit for conditional")
    p["require_stability"] = bool(p["require_stability"])
    attr = msa_attribute.validate_policy({k[5:]: v for k, v in p.items() if k.startswith("attr_")})
    p.update({f"attr_{k}": v for k, v in attr.items()})
    return p


def attribute_policy(policy: Mapping) -> dict:
    return {k[5:]: v for k, v in policy.items() if k.startswith("attr_")}


def uncertainty(system: Mapping) -> dict | None:
    g = latest_grr(system)
    if g is None:
        return None
    pol = system["policy"]
    t1 = latest(system, "type1")
    return {**msa.expanded_uncertainty(g["result"]["sigma"]["grr"], t1["result"]["bias"] if t1 else 0.0, system.get("resolution") or 0.0, pol["u_cal"], pol["k"],
                                       pol["guard_band_risk"]), "from_study": g["id"], "bias_from_study": t1["id"] if t1 else None}


def evaluate(system: Mapping, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    pol, tol, res = system["policy"], system.get("tolerance"), system.get("resolution")
    checks: dict[str, dict] = {}
    if system.get("kind", "variable") == "attribute":
        return _evaluate_attribute(system, today)
    # resolution
    if not tol or not res:
        checks["resolution"] = {"result": "missing", "reason": "no_tolerance_or_resolution"}
    else:
        share = res / tol
        checks["resolution"] = {"result": "pass" if share <= pol["resolution_share_max"] else "fail", "share": share, "limit": pol["resolution_share_max"]}
    # gauge R&R and its age
    g = latest_grr(system)
    if g is None:
        checks["grr"] = {"result": "missing", "reason": "no_study"}
        checks["validity"] = {"result": "missing", "reason": "no_study"}
    else:
        checks["grr"] = {"result": {"pass": "pass", "conditional": "warn", "fail": "fail"}[g["verdict"]], "study": g["id"], "pct": g["result"]["pct_tol"] if
                         g["result"]["pct_tol"] is not None else g["result"]["pct_tv"], "basis": g["result"]["basis"], "ndc": g["result"]["ndc"]}
        until = add_months(date.fromisoformat(g["date"]), pol["validity_months"])
        checks["validity"] = {"result": "pass" if until >= today else "fail", "until": until.isoformat(), "study": g["id"]}
    # type 1
    t = latest(system, "type1")
    checks["type1"] = ({"result": "not_done"} if t is None else
                       {"result": "pass" if t["verdict"] == "pass" else "fail", "study": t["id"], "cg": t["result"]["cg"], "cgk": t["result"]["cgk"]})
    # stability
    if not pol["require_stability"]:
        checks["stability"] = {"result": "not_needed"}
    else:
        s = latest(system, "stability")
        if s is None:
            checks["stability"] = {"result": "missing", "reason": "no_study"}
        else:
            until = add_months(date.fromisoformat(s["date"]), pol["stability_months"])
            ok = s["verdict"] == "pass" and until >= today
            checks["stability"] = {"result": "pass" if ok else "fail", "study": s["id"], "signals": s["result"]["n_signals"], "until": until.isoformat(),
                                   "reason": None if ok else ("signals" if s["verdict"] != "pass" else "expired")}
    lin = latest(system, "linearity")
    checks["linearity"] = ({"result": "not_done"} if lin is None else
                           {"result": "pass" if lin["verdict"] == "pass" else "fail", "study": lin["id"], "pct_linearity": lin["result"]["pct_linearity"],
                            "bias": lin["result"]["average_bias"]})
    bud = latest(system, "budget")
    checks["budget"] = ({"result": "not_done"} if bud is None else
                        {"result": {"pass": "pass", "conditional": "warn", "fail": "fail"}[bud["verdict"]], "study": bud["id"], "q": bud["result"]["q_ms"], "U": bud["result"]["U"]})
    checks["attribute"] = {"result": "not_needed", "reason": "other_kind"}
    return _finish(system, checks, today)


def _evaluate_attribute(system: Mapping, today: date) -> dict[str, Any]:
    pol = system["policy"]
    other = {"result": "not_needed", "reason": "other_kind"}
    checks: dict[str, dict] = {k: dict(other) for k in ("resolution", "grr", "type1", "stability", "linearity", "budget")}
    a = latest(system, "attribute")
    if a is None:
        checks["attribute"] = {"result": "missing", "reason": "no_study"}
        checks["validity"] = {"result": "missing", "reason": "no_study"}
    else:
        r = a["result"]
        checks["attribute"] = {"result": {"pass": "pass", "conditional": "warn", "fail": "fail"}[a["verdict"]], "study": a["id"], "checks": r["checks"], "worst": r["worst"]}
        until = add_months(date.fromisoformat(a["date"]), pol["validity_months"])
        checks["validity"] = {"result": "pass" if until >= today else "fail", "until": until.isoformat(), "study": a["id"]}
    return _finish(system, checks, today)


def _finish(system: Mapping, checks: dict[str, dict], today: date) -> dict[str, Any]:
    waivers = system.get("waivers", {})
    for key, c in checks.items():
        c["waiver"] = waivers.get(key) if c["result"] in ("warn", "fail", "missing") else None
        c["effective"] = "waived" if c["waiver"] else c["result"]
    blocking = [k for k, c in checks.items() if c["effective"] in ("fail", "missing")]
    remarks = [k for k, c in checks.items() if c["effective"] == "warn"]
    waived = [k for k, c in checks.items() if c["effective"] == "waived"]
    status = "block" if blocking else "conditional" if remarks else "pass"
    return {"status": status, "checks": checks, "blocking": blocking, "remarks": remarks, "waived": waived, "uncertainty": uncertainty(system),
            "today": today.isoformat()}

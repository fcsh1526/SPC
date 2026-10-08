"""The MSA gate: may data from this measurement system be used for a capability study, a monitor or a report?

The draft assumes a capable and stable measurement process (1, 6.3) and asks for proof in 8.2.3. The gate turns that into checks:

  resolution  the resolution is at most 5 % of the tolerance (policy)
  grr         the latest gauge R&R (crossed, or nested for destructive measurement) is capable (conditional counts as a remark)
  type1       a type 1 study, when there is one, is capable (optional)
  validity    the latest gauge R&R is not older than the validity time (12 months)
  iso22514_7  an ISO 22514-7 study, when there is one: Q_MS and Q_MP within the limits of the policy (15 % and 30 %) and C_MS, C_MP above 1.33 (optional);
              a study with a process analysis (repeatability and reproducibility) also proves the `grr` check, as a gauge R&R does
  linearity   a linearity and bias study, when there is one, shows the line "bias = 0" inside the confidence band (optional)
  budget      an uncertainty budget, when there is one, gives 2U/T within the limits of the policy (optional)
  bias        the newest bias study (AIAG MSA, independent sample or control chart method), when there is one: the bias is statistically zero and the repeatability is not large (optional)
  stability   repeated measurements of a reference show no signal and are not older than the check interval (when required)

A system of the kind `attribute` (go / no-go) has the checks `attribute` (the latest attribute agreement study is capable, `spc.core.msa_attribute`) and
`validity` (that study is not older than the validity time); the checks of a variable system do not apply to it, and the other way round. The signal detection approach and the analytic
method of AIAG MSA are checked as `attribute_aiag` (optional). The range method of the gauge R&R is a quick check, not the proof of repeatability and reproducibility: it is kept with the
studies and does not count in the gate. So is the Gage R study (appendix D): a preliminary repeatability of one part and one operator. The pooled standard deviation study (chapter IV H) is a gauge
R&R: it proves repeatability and reproducibility like the crossed and the nested one.

Result of a check: pass, warn (conditional), fail, missing (no evidence), not_done (optional), not_needed, or waived (recorded with
a reason by an engineer: it passes and stays flagged). The gate is `block` when a check failed or is missing without a waiver,
`conditional` when only remarks are left, `pass` otherwise. A blocked gate refuses; a conditional one is allowed and flagged.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Mapping

from scipy.stats import norm

from spc.core import msa
from spc.core import msa_attribute

POLICY_DEFAULTS: dict[str, Any] = {
    "validity_months": 12, "stability_months": 6, "resolution_share_max": 0.05, "grr_pass": 10.0, "grr_conditional": 30.0, "ndc_min": 5.0,
    "cg_min": 1.33, "require_stability": True, "k": 2.0, "guard_band_risk": 0.05, "u_cal": 0.0, "budget_pass": 15.0, "budget_conditional": 30.0,
    "iso_q_ms_max": 15.0, "iso_q_mp_max": 30.0, "iso_c_min": 1.33,
    **{f"attr_{k}": v for k, v in msa_attribute.POLICY_DEFAULTS.items()},
}
CHECKS = ("resolution", "grr", "type1", "validity", "stability", "attribute", "linearity", "budget", "iso22514_7", "attribute_iso", "bias", "attribute_aiag")
STUDY_KINDS = ("type1", "grr", "grr_nested", "stability", "linearity", "budget", "iso_study", "attribute", "bowker", "uncertainty_range", "attribute_review",
               "bias", "bias_chart", "grr_range", "signal_detection", "analytic", "pooled_grr", "gage_r")
BIAS_KINDS = ("bias", "bias_chart")
AIAG_ATTRIBUTE_KINDS = ("signal_detection", "analytic")
SYSTEM_KINDS = ("variable", "attribute")
STUDY_KINDS_OF = {"variable": ("type1", "grr", "grr_nested", "stability", "linearity", "budget", "iso_study", "bias", "bias_chart", "grr_range", "pooled_grr", "gage_r"),
                  "attribute": ("attribute", "bowker", "uncertainty_range", "attribute_review", "signal_detection", "analytic")}
ISO_ATTRIBUTE_KINDS = ("bowker", "uncertainty_range", "attribute_review")
GRADE = {"pass": "pass", "conditional": "warn", "fail": "fail"}
GRR_KINDS = ("grr", "grr_nested", "pooled_grr")  # the crossed study, the nested one for destructive measurement and the pooled standard deviation one (parts that cannot be measured in random order): each proves repeatability and reproducibility


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


def latest_evidence(system: Mapping) -> dict | None:
    """The newest study that proves repeatability and reproducibility: a gauge R&R (crossed or nested) or an ISO 22514-7 study with a process analysis."""
    items = [s for s in system["studies"] if not s.get("voided") and s.get("result") and (s["kind"] in GRR_KINDS or (s["kind"] == "iso_study" and s["result"]["combined"]["q_mp"] is not None))]
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
    num("iso_q_ms_max", 1, 100); num("iso_q_mp_max", 1, 100); num("iso_c_min", 0.5, 5)
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
    g = latest_evidence(system)
    pol = system["policy"]
    if g is None:
        iso_only = [x for x in system["studies"] if x["kind"] == "iso_study" and not x.get("voided") and x.get("result")]
        g = max(iso_only, key=lambda x: (x["date"], x["id"])) if iso_only else None
    if g is None:
        return None
    if g["kind"] == "iso_study":  # ISO 22514-7: the uncertainty of the measurement process (of the measuring system when there is no process study), k = 2 or Student's t
        c = g["result"]["combined"]
        u = c["u_mp"] if c["u_mp"] is not None else c["u_ms"]
        z = float(norm.ppf(1.0 - pol["guard_band_risk"]))
        return {"u": u, "k": c["k"], "U": c["k"] * u, "z": z, "guard_band": z * u, "guard_risk": pol["guard_band_risk"], "parts": c["components"], "from_study": g["id"],
                "bias_from_study": None, "source": "iso22514_7"}
    t1 = latest(system, "type1")
    return {**msa.expanded_uncertainty(g["result"]["sigma"]["grr"], t1["result"]["bias"] if t1 else 0.0, system.get("resolution") or 0.0, pol["u_cal"], pol["k"],
                                       pol["guard_band_risk"]), "from_study": g["id"], "bias_from_study": t1["id"] if t1 else None, "source": "grr"}


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
    g = latest_evidence(system)
    if g is None:
        checks["grr"] = {"result": "missing", "reason": "no_study"}
        checks["validity"] = {"result": "missing", "reason": "no_study"}
    else:
        if g["kind"] == "iso_study":
            c = g["result"]["combined"]
            checks["grr"] = {"result": GRADE[g["verdict"]], "study": g["id"], "iso": True, "pct": c["q_mp"], "basis": "iso_q_mp", "ndc": None, "c": c["c_mp"]}
        else:
            checks["grr"] = {"result": GRADE[g["verdict"]], "study": g["id"], "pct": g["result"]["pct_tol"] if
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
    biases = [x for x in (latest(system, k) for k in BIAS_KINDS) if x is not None]
    if not biases:
        checks["bias"] = {"result": "not_done"}
    else:
        b = max(biases, key=lambda x: (x["date"], x["id"]))
        checks["bias"] = {"result": GRADE[b["verdict"]], "study": b["id"], "kind": b["kind"], "bias": b["result"]["bias"], "t": b["result"]["t"], "ev_pct": b["result"]["ev_pct"],
                          "stable": b["result"].get("stable")}
    iso_s = latest(system, "iso_study")
    checks["iso22514_7"] = ({"result": "not_done"} if iso_s is None else
                            {"result": GRADE[iso_s["verdict"]], "study": iso_s["id"], "q_ms": iso_s["result"]["combined"]["q_ms"], "q_mp": iso_s["result"]["combined"]["q_mp"],
                             "c_ms": iso_s["result"]["combined"]["c_ms"], "c_mp": iso_s["result"]["combined"]["c_mp"]})
    checks["attribute"] = {"result": "not_needed", "reason": "other_kind"}
    checks["attribute_iso"] = {"result": "not_needed", "reason": "other_kind"}
    checks["attribute_aiag"] = {"result": "not_needed", "reason": "other_kind"}
    return _finish(system, checks, today)


def _evaluate_attribute(system: Mapping, today: date) -> dict[str, Any]:
    pol = system["policy"]
    other = {"result": "not_needed", "reason": "other_kind"}
    checks: dict[str, dict] = {k: dict(other) for k in ("resolution", "grr", "type1", "stability", "linearity", "budget", "iso22514_7", "bias")}
    a = latest(system, "attribute")
    rng = latest(system, "uncertainty_range")
    if a is None and rng is None:
        checks["attribute"] = {"result": "missing", "reason": "no_study"}
        checks["validity"] = {"result": "missing", "reason": "no_study"}
    else:
        grades = []
        if a is not None:
            r = a["result"]
            grades.append((GRADE[a["verdict"]], {"study": a["id"], "checks": r["checks"], "worst": r["worst"]}, a))
        if rng is not None:  # ISO 22514-7, 12.3: the uncertainty range stands for the proof as well
            grades.append((GRADE[rng["verdict"]], {"study": rng["id"], "iso": True, "q_attr": rng["result"]["q_attr"]}, rng))
        order = {"fail": 0, "warn": 1, "pass": 2}
        worst = min(grades, key=lambda g: order[g[0]])
        checks["attribute"] = {"result": worst[0], **worst[1]}
        newest = max((g[2] for g in grades), key=lambda x: (x["date"], x["id"]))
        until = add_months(date.fromisoformat(newest["date"]), pol["validity_months"])
        checks["validity"] = {"result": "pass" if until >= today else "fail", "until": until.isoformat(), "study": newest["id"]}
    iso_items = [latest(system, k) for k in ISO_ATTRIBUTE_KINDS]
    iso_items = [x for x in iso_items if x is not None]
    if not iso_items:
        checks["attribute_iso"] = {"result": "not_done"}
    else:
        order = {"fail": 0, "warn": 1, "pass": 2}
        res = min((GRADE[x["verdict"]] for x in iso_items), key=lambda g: order[g])
        checks["attribute_iso"] = {"result": res, "studies": {x["kind"]: x["id"] for x in iso_items}}
    aiag_items = [x for x in (latest(system, k) for k in AIAG_ATTRIBUTE_KINDS) if x is not None]
    if not aiag_items:
        checks["attribute_aiag"] = {"result": "not_done"}
    else:
        order = {"fail": 0, "warn": 1, "pass": 2}
        res = min((GRADE[x["verdict"]] for x in aiag_items), key=lambda g: order[g])
        checks["attribute_aiag"] = {"result": res, "studies": {x["kind"]: x["id"] for x in aiag_items}}
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

"""The studies after ISO 22514-7:2012 as studies of a measurement system: the evaluation of the input of one study (spc.core.iso22514_7 does the calculations).

`iso_study` (variable system) takes the parts of the flowchart of figure 1 that the person has data for:
    lower, upper       the specification limits (the tolerance of the system is used when they are left out)
    calibration        {"expanded": U_CAL, "k": k_CAL} or {"standard": u_CAL}
    mpe                maximum permissible error(s): the MPE route of 5.3 (then no calibration, linearity or bias)
    resolution         RE (the resolution of the system when left out)
    repeatability      {"reference": x_m, "values": [...>= 30]} or {"references": [...], "values": [[...], ...]} (N >= 2, N K >= 30)
    linearity          {"references": [...], "values": [[...], ...], "method": "anova" | "deviation", "at": x}
                       anova: lack of fit and pure error give u_LIN and u_EVR, and the readings are corrected with the regression function (u_BI = 0, as in A.3);
                       deviation: the bias line of 7.1.3.4 (needs `repeatability` for u_EVR and u_BI)
    linearity_a        a known deviation or MPE: u_LIN = a / sqrt 3 (instance 2 of Table 4)
    process            {"data": [operator][part][repeat], "kind": "operator" | "system"}: u_EVO, u_AV (u_GV for measuring systems), u_IA
    process_gv         the same for several measuring systems of one operator, when `process` has the operators
    other              {"stab": u, "obj": a_OBJ, "temperature": {...}, "rest": u, "ms_rest": u, "gv": u, "interactions": [...]}
    observed_cp        an observed capability index of the production process: the real one follows from Q_MP (clause 10)
    alpha              level of the tests (5 %)
The attribute studies: `bowker` (12.2), `uncertainty_range` (12.3), `attribute_review` (12.4).
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from spc.core import iso22514_7 as iso
from spc.core.msa import MsaError

ISO_KEYS = {"lower", "upper", "calibration", "mpe", "resolution", "repeatability", "linearity", "linearity_a", "process", "process_gv", "other", "observed_cp", "alpha"}
OTHER_KEYS = {"stab", "obj", "temperature", "rest", "ms_rest", "gv", "interactions"}
TEMPERATURE_KEYS = {"delta_t", "alpha", "length", "mean_temperature", "u_alpha"}
DESIGN_WARNINGS = {"few_workpieces", "few_measurements", "few_repetitions"}


def _block(inp: Mapping, key: str, allowed: set[str]) -> dict | None:
    b = inp.get(key)
    if b is None:
        return None
    if not isinstance(b, dict) or set(b) - allowed:
        raise MsaError(f"{key}: allowed keys are {sorted(allowed)}")
    return b


def policy_limits(policy: Mapping) -> dict:
    return {"q_ms_max": policy.get("iso_q_ms_max", iso.Q_MS_MAX), "q_mp_max": policy.get("iso_q_mp_max", iso.Q_MP_MAX), "c_min": policy.get("iso_c_min", iso.C_MIN)}


def evaluate_iso(inp: Mapping, tolerance: float | None, resolution: float | None, policy: Mapping) -> dict[str, Any]:
    if not isinstance(inp, Mapping) or set(inp) - ISO_KEYS:
        raise MsaError(f"allowed keys are {sorted(ISO_KEYS)}")
    alpha = inp.get("alpha", 0.05)
    lo, hi = inp.get("lower"), inp.get("upper")
    if (lo is None) != (hi is None):
        raise MsaError("give both specification limits, or none (then the tolerance of the system is used)")
    if lo is not None:
        if not iso._num(lo, "the lower limit") < iso._num(hi, "the upper limit"):
            raise MsaError("the lower limit must be below the upper limit")
        interval = float(hi) - float(lo)
    elif tolerance:
        interval = float(tolerance)
    else:
        raise MsaError("the specification interval U - L is needed: give the limits, or the tolerance of the measurement system")
    comps: dict[str, float] = {}
    analyses: dict[str, Any] = {}
    warnings: list[dict] = []
    # calibration
    cal = _block(inp, "calibration", {"expanded", "k", "standard"})
    mpe = inp.get("mpe")
    if mpe is not None and (not isinstance(mpe, list) or not mpe or len(mpe) > 10):
        raise MsaError("mpe is a list of 1 to 10 maximum permissible errors")
    if cal:
        comps["cal"] = iso.u_calibration(cal.get("expanded"), cal.get("k"), cal.get("standard"))
    elif not mpe:
        warnings.append({"code": "no_calibration_uncertainty"})
    # resolution
    re_ = inp.get("resolution", resolution)
    if re_:
        comps["re"] = iso.u_resolution(re_)
        analyses["resolution"] = iso.resolution_check(re_, tolerance=interval)
        if not analyses["resolution"]["conformity"]["ok"]:
            warnings.append({"code": "resolution_too_coarse", "limit": analyses["resolution"]["conformity"]["limit"]})
    else:
        warnings.append({"code": "no_resolution"})
    # repeatability and linearity
    rep = _block(inp, "repeatability", {"reference", "references", "values"})
    lin = _block(inp, "linearity", {"references", "values", "method", "at"})
    if mpe and lin:
        raise MsaError("the MPE route and a linearity study exclude each other")
    if rep:
        if "references" in rep:
            r = iso.repeatability_standards(rep["references"], rep.get("values"))
            if r["variances_differ"]:
                warnings.append({"code": "variances_differ"})
        else:
            r = iso.repeatability_standard(rep.get("values", []), rep.get("reference"))
        analyses["repeatability"] = r
        comps["evr"], comps["bi"] = r["u_evr"], r["u_bi"]
    if lin:
        method = lin.get("method", "anova")
        if method not in ("anova", "deviation"):
            raise MsaError("the linearity method is anova or deviation")
        if method == "anova":
            if rep:
                raise MsaError("the analysis of variance of the linearity study gives u_EVR itself: leave out `repeatability`, or use the method `deviation`")
            a = iso.linearity(lin.get("references"), lin.get("values"), alpha)
            analyses["linearity"] = {"method": "anova", **a}
            comps["lin"], comps["evr"], comps["bi"] = a["u_lin"], a["u_evr"], 0.0  # the readings are corrected with the regression function
            if a["lack_of_fit"]:
                warnings.append({"code": "lack_of_fit", "f": a["f"], "f_crit": a["f_crit"]})
            if not a["sd_constant"]:
                warnings.append({"code": "variances_differ"})
        else:
            if not rep:
                raise MsaError("the method `deviation` needs a `repeatability` study for u_EVR and u_BI")
            d = iso.linearity_deviation(lin.get("references"), lin.get("values"), lin.get("at"))
            analyses["linearity"] = {"method": "deviation", **d}
            comps["lin"] = d["u_lin"]
    elif inp.get("linearity_a") is not None:
        a_known = iso._num(inp["linearity_a"], "linearity_a", nonnegative=True)
        comps["lin"] = a_known / iso.SQRT3
        analyses["linearity"] = {"method": "known", "a": a_known, "u_lin": comps["lin"]}
    elif not mpe:
        warnings.append({"code": "linearity_not_studied"})
    if not rep and not lin and not mpe and not inp.get("process"):
        raise MsaError("give a repeatability study, a linearity study, an MPE or a process study")
    if mpe and not rep:
        raise MsaError("the MPE route still needs a repeatability study for u_EVR")
    # the process
    proc = _block(inp, "process", {"data", "kind"})
    interactions: list[float] = []
    n_process = None
    gauges, operators = 1, 1
    if proc:
        kind = proc.get("kind", "operator")
        if kind not in ("operator", "system"):
            raise MsaError("process.kind is operator or system")
        p = iso.reproducibility(proc.get("data"), alpha)
        analyses["reproducibility"] = {"kind": kind, **p}
        comps["evo"] = p["u_evo"]
        if p["u_av"] is not None:
            comps["av" if kind == "operator" else "gv"] = p["u_av"]
        if p["u_ia"]:
            interactions.append(p["u_ia"])
        warnings += [{"code": code} for code in p["notes"]]
        n_process = p["n"]
        operators, gauges = (p["operators"], 1) if kind == "operator" else (1, p["operators"])
        repeats, workpieces = p["repeats"], p["parts"]
    gvb = _block(inp, "process_gv", {"data"})
    if gvb:
        g = iso.reproducibility(gvb.get("data"), alpha)
        analyses["reproducibility_gv"] = g
        if g["u_av"] is not None:
            comps["gv"] = g["u_av"]
        if g["u_ia"]:
            interactions.append(g["u_ia"])
        gauges = g["operators"]
        warnings += [{"code": code} for code in g["notes"] if code not in DESIGN_WARNINGS or not proc]
    other = _block(inp, "other", OTHER_KEYS)
    if other:
        for key in ("stab", "rest", "ms_rest", "gv"):
            if other.get(key) is not None:
                comps[key] = iso._num(other[key], f"other.{key}", nonnegative=True)
        if other.get("obj") is not None:
            comps["obj"] = iso.u_object(other["obj"])
        t = other.get("temperature")
        if t is not None:
            if not isinstance(t, dict) or set(t) - TEMPERATURE_KEYS or not {"delta_t", "alpha", "length"} <= set(t):
                raise MsaError(f"temperature needs delta_t, alpha and length and may have mean_temperature and u_alpha")
            tt = iso.u_temperature(t["delta_t"], t["alpha"], t["length"], t.get("mean_temperature", iso.REFERENCE_TEMPERATURE), t.get("u_alpha", 0.0))
            analyses["temperature"] = tt
            comps["t"] = tt["u_t"]
        extra = other.get("interactions") or []
        if not isinstance(extra, list) or len(extra) > 20:
            raise MsaError("other.interactions is a list of up to 20 standard uncertainties")
        interactions += [iso._num(v, "an interaction", nonnegative=True) for v in extra]
    # the coverage factor (8.2)
    k = 2.0
    cov = {"k": 2.0, "student": False, "nu": None}
    if proc and n_process is not None and n_process < iso.MIN_STANDARD_VALUES:
        cov = iso.coverage_factor(workpieces, operators, gauges, repeats, alpha, n_process)
        k = cov["k"]
    lim = policy_limits(policy)
    combined = iso.combine({key: comps.get(key, 0.0) for key in (*iso.SYSTEM_KEYS, *iso.PROCESS_KEYS) if key != "ms_rest" or "ms_rest" in comps}, interval, k, interactions, mpe, **lim)
    out: dict[str, Any] = {"interval": interval, "components": comps, "coverage": cov, "analyses": analyses, "combined": combined, "warnings": warnings}
    cp = inp.get("observed_cp")
    if cp is not None:
        q = combined["q_mp"] if combined["q_mp"] is not None else combined["q_ms"]
        out["real_capability"] = {"observed": iso._num(cp, "observed_cp", positive=True), "q": q, "basis": "q_mp" if combined["q_mp"] is not None else "q_ms", "real": iso.real_capability(cp, q / 100.0)}
    if combined["verdict"] == "fail":
        out["verdict"] = "fail"
    elif any(w["code"] in ({"lack_of_fit", "resolution_too_coarse", "variances_differ"} | DESIGN_WARNINGS) for w in warnings):
        out["verdict"] = "conditional"
    else:
        out["verdict"] = "pass"
    return out


# ------------------------------------------------------------------ attribute studies (clause 12)

def _results(b: Mapping, key: str = "results") -> dict:
    r = b.get(key)
    if not isinstance(r, dict) or not 1 <= len(r) <= 10:
        raise MsaError("results are the decisions of each operator: {operator: [trial][part]} with 1 for good and 0 for bad")
    return r


def evaluate_attribute_iso(kind: str, inp: Mapping, policy: Mapping) -> dict[str, Any]:
    lim = policy_limits(policy)
    if kind == "bowker":
        if not isinstance(inp, Mapping) or set(inp) - {"results", "alpha"}:
            raise MsaError("a bowker study holds results and may hold alpha")
        r = iso.bowker_study(_results(inp), inp.get("alpha", 0.05))
        r["verdict"] = "pass" if r["symmetric"] and not r["notes"] else "conditional"
        return r
    if kind == "uncertainty_range":
        if not isinstance(inp, Mapping) or set(inp) != {"reference", "results", "lower", "upper"}:
            raise MsaError("an uncertainty range study holds reference, results, lower and upper")
        r = iso.uncertainty_range(inp["reference"], _results(inp), inp["lower"], inp["upper"])
        r["limit_rule"], r["limit_mp"] = iso.Q_ATTR_RULE, lim["q_mp_max"]
        r["verdict"] = "pass" if r["q_attr"] <= iso.Q_ATTR_RULE else "conditional" if r["q_attr"] <= lim["q_mp_max"] else "fail"
        return r
    if kind == "attribute_review":
        if not isinstance(inp, Mapping) or not {"reference", "results", "lower", "upper"} <= set(inp) or set(inp) - {"reference", "results", "lower", "upper", "q_mp"}:
            raise MsaError("a review holds reference, results, lower and upper, and may hold q_mp (percent)")
        tol = float(inp["upper"]) - float(inp["lower"])
        u_max = iso.u_mp_max(inp["q_mp"] / 100.0, tol) if inp.get("q_mp") else None
        res = inp["results"]
        r = iso.attribute_review(inp["reference"], res, inp["lower"], inp["upper"], u_max)
        r["u_mp_max"] = u_max
        r["verdict"] = "pass" if r["accepted"] else "fail"
        return r
    raise MsaError(f"unknown attribute study {kind!r}")

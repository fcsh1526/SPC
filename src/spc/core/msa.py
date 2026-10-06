"""Measurement system analysis: what the capability study needs as a precondition (draft 1.x, 6.3, 8.2.3).

The draft assumes "the measurement process is capable, stable and does not significantly contribute to overall variation" and
asks for proof "in accordance with AIAG MSA and VDA 5" (the report element names ISO 22514-3 and 22514-7 as references for a supporting study). It gives no criteria of its own. The usual ones are used here
and are parameters of the policy of a measurement system:

  Type 1 study (VDA 5; the formulas are the usual ones of that guideline, not checked against ISO 22514-7, whose Cg/Cgk counterpart is C_MS), repeated measurements of one reference standard:
      Cg = 0.2 T / (6 s),   Cgk = (0.1 T - |mean - reference|) / (3 s),   both >= 1.33.
  Crossed gauge R&R by analysis of variance (AIAG MSA, 4th edition), p parts x o operators x r trials:
      sigma_EV (repeatability), sigma_AV (reproducibility), sigma_INT (part x operator), sigma_GRR, sigma_PV,
      %GRR of the tolerance = 6 sigma_GRR / T,  %GRR of the total variation,  ndc = 1.41 sigma_PV / sigma_GRR.
      The interaction is pooled with the error when its p value is above 0.25 (AIAG).
      Of the tolerance: <= 10 % capable, <= 30 % conditional, above 30 % not capable. ndc >= 5 for "capable".
  Stability: repeated measurements of a reference over time on an individuals chart without a signal.
  Expanded uncertainty (simplified; ISO 14253-1 / VDA 5 combine the same kinds of parts):
      U = k sqrt(sigma_GRR^2 + (bias/sqrt 3)^2 + (RE/sqrt 12)^2 + u_cal^2), guard band g = z(1-risk) U / k.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np
from scipy import stats

CG_MIN = 1.33
MIN_TYPE1 = 25
POOL_ALPHA = 0.25  # AIAG: the part x operator interaction is pooled with the error above this p value


class MsaError(ValueError):
    """The data of a study cannot be evaluated."""


def type1(values: Sequence[float], reference: float, tolerance: float, cg_min: float = CG_MIN) -> dict[str, Any]:
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or not np.all(np.isfinite(x)):
        raise MsaError("the measurements must be a list of numbers")
    if x.size < MIN_TYPE1:
        raise MsaError(f"a type 1 study needs at least {MIN_TYPE1} measurements of the reference (50 are usual)")
    if not tolerance > 0 or not math.isfinite(reference):
        raise MsaError("the tolerance must be positive and the reference a number")
    s = float(x.std(ddof=1))
    if not s > 0:
        raise MsaError("the measurements do not vary: the resolution is too coarse to evaluate the study")
    mean = float(x.mean())
    bias = mean - reference
    cg = 0.2 * tolerance / (6.0 * s)
    cgk = (0.1 * tolerance - abs(bias)) / (3.0 * s)
    t = bias / (s / math.sqrt(x.size))
    return {"n": int(x.size), "mean": mean, "sd": s, "reference": float(reference), "bias": float(bias), "bias_p": float(2 * stats.t.sf(abs(t), x.size - 1)),
            "cg": float(cg), "cgk": float(cgk), "cg_min": cg_min, "verdict": "pass" if cg >= cg_min and cgk >= cg_min else "fail"}


def grr(data, tolerance: float | None, pass_pct: float = 10.0, conditional_pct: float = 30.0, ndc_min: float = 5.0) -> dict[str, Any]:
    """Crossed gauge R&R by analysis of variance. `data[part][operator][trial]`, balanced."""
    try:
        a = np.asarray(data, dtype=float)
    except (ValueError, TypeError):
        raise MsaError("the data must be parts x operators x trials, the same number of values everywhere") from None
    if a.ndim != 3 or not np.all(np.isfinite(a)):
        raise MsaError("the data must be parts x operators x trials of numbers")
    p, o, r = a.shape
    if p < 5 or o < 2 or r < 2:
        raise MsaError("a gauge R&R study needs at least 5 parts, 2 operators and 2 trials (10 parts, 3 operators, 3 trials are usual)")
    grand = a.mean()
    ss_t = float(((a - grand) ** 2).sum())
    part_m, op_m, cell_m = a.mean(axis=(1, 2)), a.mean(axis=(0, 2)), a.mean(axis=2)
    ss_p = float(o * r * ((part_m - grand) ** 2).sum())
    ss_o = float(p * r * ((op_m - grand) ** 2).sum())
    ss_po = float(r * ((cell_m - part_m[:, None] - op_m[None, :] + grand) ** 2).sum())
    ss_e = ss_t - ss_p - ss_o - ss_po
    df_p, df_o, df_po, df_e = p - 1, o - 1, (p - 1) * (o - 1), p * o * (r - 1)
    ms_p, ms_o, ms_po, ms_e = ss_p / df_p, ss_o / df_o, ss_po / df_po, max(ss_e, 0.0) / df_e
    if not ms_e > 0:
        raise MsaError("the repeated measurements are identical: the resolution is too coarse to evaluate the study")
    f_po = ms_po / ms_e
    p_po = float(stats.f.sf(f_po, df_po, df_e))
    pooled = p_po > POOL_ALPHA
    ms_err = (ss_po + max(ss_e, 0.0)) / (df_po + df_e) if pooled else ms_e
    ev2 = ms_err
    av2 = max(0.0, (ms_o - (ms_err if pooled else ms_po)) / (p * r))
    int2 = 0.0 if pooled else max(0.0, (ms_po - ms_e) / r)
    pv2 = max(0.0, (ms_p - (ms_err if pooled else ms_po)) / (o * r))
    grr2 = ev2 + av2 + int2
    tv2 = grr2 + pv2
    if not grr2 > 0:
        raise MsaError("no measurement variation could be estimated")
    s_grr, s_pv, s_tv = math.sqrt(grr2), math.sqrt(pv2), math.sqrt(tv2)
    pct_tv = 100.0 * s_grr / s_tv
    pct_tol = None if not tolerance else 100.0 * 6.0 * s_grr / tolerance
    ndc = 1.41 * s_pv / s_grr
    basis = pct_tol if pct_tol is not None else pct_tv
    verdict = "pass" if basis <= pass_pct else "conditional" if basis <= conditional_pct else "fail"
    if ndc < 2.0:
        verdict = "fail"
    elif ndc < ndc_min and verdict == "pass":
        verdict = "conditional"
    notes = []
    if p < 10 or o < 3 or r < 2:
        notes.append("small_design")
    if pct_tol is None:
        notes.append("no_tolerance")
    return {"parts": p, "operators": o, "trials": r, "pooled_interaction": pooled, "interaction_p": p_po,
            "ms": {"parts": ms_p, "operators": ms_o, "interaction": ms_po, "error": ms_e},
            "sigma": {"ev": math.sqrt(ev2), "av": math.sqrt(av2), "interaction": math.sqrt(int2), "grr": s_grr, "pv": s_pv, "tv": s_tv},
            "pct_tv": pct_tv, "pct_tol": pct_tol, "ndc": float(ndc), "basis": "tolerance" if pct_tol is not None else "total_variation",
            "limits": {"pass": pass_pct, "conditional": conditional_pct, "ndc": ndc_min}, "verdict": verdict, "notes": notes}


def stability(values: Sequence[float]) -> dict[str, Any]:
    """Repeated measurements of a reference over time: an individuals chart with the usual signals (limit, run of 7, trend of 7)."""
    from spc.core.charts.variable import imr
    from spc.core.rules import RuleSet, evaluate

    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or not np.all(np.isfinite(x)):
        raise MsaError("the measurements must be a list of numbers")
    if x.size < 20:
        raise MsaError("a stability check needs at least 20 measurements of the reference over time")
    if not float(x.std(ddof=1)) > 0:
        raise MsaError("the measurements do not vary")
    chart = imr(x, 0.0027)
    loc = chart.location
    res = evaluate(x, loc.center, loc.lcl, loc.ucl, RuleSet(beyond_limits=True, run_length=7, trend_length=7))
    return {"n": int(x.size), "mean": float(x.mean()), "sd": float(x.std(ddof=1)), "lcl": float(loc.lcl), "ucl": float(loc.ucl),
            "signals": [{"index": int(v.index), "rule": v.rule} for v in res.violations][:20], "n_signals": len(res.violations),
            "verdict": "pass" if not res.violations else "fail"}


def expanded_uncertainty(sigma_grr: float, bias: float = 0.0, resolution: float = 0.0, u_cal: float = 0.0, k: float = 2.0,
                         guard_risk: float = 0.05) -> dict[str, float]:
    u = math.sqrt(sigma_grr ** 2 + (bias / math.sqrt(3.0)) ** 2 + (resolution / math.sqrt(12.0)) ** 2 + u_cal ** 2)
    z = float(stats.norm.ppf(1.0 - guard_risk))
    return {"u": u, "k": k, "U": k * u, "z": z, "guard_band": z * u, "guard_risk": guard_risk,
            "parts": {"grr": sigma_grr, "bias": abs(bias) / math.sqrt(3.0), "resolution": resolution / math.sqrt(12.0), "calibration": u_cal}}

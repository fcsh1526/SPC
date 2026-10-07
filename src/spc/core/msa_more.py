"""More studies of a variable measurement system: linearity and bias, nested (destructive) gauge R&R, and an uncertainty budget.

Linearity and bias (AIAG MSA, 4th edition, chapter III; the formulas are the usual ones of that guideline, not checked against the manual).
g reference parts of known value x_i, each measured m times, y_ij. The bias of a reading is b_ij = y_ij - x_i.
  repeatability   s_r = sqrt(sum (y_ij - mean_i)^2 / (g m - g)),  bias of part i = mean_i - x_i,  t_i = bias_i / (s_r / sqrt m),  df = g m - g
  average bias    t = mean(b) / (s_r / sqrt(g m)),  df = g m - g
  linearity       b_ij = a + c x_i by least squares over all g m readings; linearity = |c| x process variation; % linearity = 100 |c|;
                  the fitted bias line has the band  a + c x +- t(1 - alpha/2, g m - 2) s sqrt(1/(g m) + (x - mean x)^2 / S_xx);
                  the system passes when the line "bias = 0" lies inside the band over the whole range of the reference values (AIAG).

Nested gauge R&R (destructive measurement: a part cannot be measured by a second operator). Data[operator][part][trial]; the parts differ between operators.
  x = mu + operator + part(operator) + error   (random effects; method of moments, `spc.core.nested`)
  repeatability = error, reproducibility = operator, part-to-part = part(operator), GRR = repeatability + reproducibility (variances);
  %GRR and ndc as in the crossed study, with the same limits.

Uncertainty budget (ISO 14253-1 / GUM). Components with a standard uncertainty (or a half width and a distribution), a sensitivity coefficient and degrees of freedom:
  u_c = sqrt(sum (c_i u_i)^2), nu_eff = u_c^4 / sum ((c_i u_i)^4 / nu_i) (Welch-Satterthwaite), U = k u_c (k = 2 as ISO 14253-1),
  ISO 14253-1 decision rule: the acceptance zone is the specification zone shrunk by U at each limit, the rejection zone lies beyond the limits by U.
  Q = 2 U / T (VDA 5 style ratio); limits are the policy of the measurement system (15 % and 30 % by default, not checked against VDA 5).
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
from scipy import stats

from spc.core import nested
from spc.core.msa import MsaError

DIVISORS = {"standard": 1.0, "normal_k2": 2.0, "normal_k3": 3.0, "rectangular": math.sqrt(3.0), "triangular": math.sqrt(6.0), "u_shaped": math.sqrt(2.0)}
MAX_COMPONENTS = 30
BAND_GRID = 200


def _f(x):
    return None if x is None or not math.isfinite(float(x)) else float(x)


# ------------------------------------------------------------------ linearity and bias

def linearity(reference: Sequence[float], values, process_variation: float | None = None, alpha: float = 0.05) -> dict[str, Any]:
    try:
        x = np.asarray(reference, dtype=float)
        y = np.asarray(values, dtype=float)
    except (TypeError, ValueError):
        raise MsaError("the reference values and the measurements must be numbers, the same number of readings for every part") from None
    if x.ndim != 1 or y.ndim != 2 or y.shape[0] != x.size or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise MsaError("give one reference value per part and, for each part, the list of its readings (the same number for every part)")
    g, m = y.shape
    if g < 5 or m < 2:
        raise MsaError("a linearity study needs at least 5 parts with 2 readings each (5 parts with 12 readings each are usual)")
    if np.unique(x).size != g:
        raise MsaError("the reference values must differ from part to part")
    if not 0 < alpha < 0.5:
        raise MsaError("alpha must be between 0 and 0.5")
    if process_variation is not None and not (math.isfinite(process_variation) and process_variation > 0):
        raise MsaError("the process variation must be positive")
    part_mean = y.mean(axis=1)
    ss_within = float(((y - part_mean[:, None]) ** 2).sum())
    df_r = g * m - g
    s_r = math.sqrt(ss_within / df_r)
    if not s_r > 0:
        raise MsaError("the repeated readings are identical: the resolution is too coarse to evaluate the study")
    bias_part = part_mean - x
    t_part = bias_part / (s_r / math.sqrt(m))
    tq = float(stats.t.isf(alpha / 2, df_r))
    parts = [{"reference": float(x[i]), "mean": float(part_mean[i]), "bias": float(bias_part[i]), "t": float(t_part[i]), "p_value": float(2 * stats.t.sf(abs(t_part[i]), df_r)),
              "ci": [float(bias_part[i] - tq * s_r / math.sqrt(m)), float(bias_part[i] + tq * s_r / math.sqrt(m))]} for i in range(g)]
    for p in parts:
        p["significant"] = bool(p["p_value"] < alpha)
    b_all = (y - x[:, None]).ravel()
    xs = np.repeat(x, m)
    avg = float(b_all.mean())
    t_avg = avg / (s_r / math.sqrt(g * m))
    n = g * m
    sxx = float(((xs - xs.mean()) ** 2).sum())
    slope = float(((xs - xs.mean()) * (b_all - avg)).sum() / sxx)
    icpt = avg - slope * float(xs.mean())
    resid = b_all - (icpt + slope * xs)
    dfe = n - 2
    s = math.sqrt(float(resid @ resid) / dfe)
    tq2 = float(stats.t.isf(alpha / 2, dfe))
    se_slope = s / math.sqrt(sxx)
    se_icpt = s * math.sqrt(1.0 / n + float(xs.mean()) ** 2 / sxx)
    p_slope = float(2 * stats.t.sf(abs(slope / se_slope), dfe)) if se_slope > 0 else 1.0
    p_icpt = float(2 * stats.t.sf(abs(icpt / se_icpt), dfe)) if se_icpt > 0 else 1.0
    grid = np.linspace(float(x.min()), float(x.max()), BAND_GRID)
    half = tq2 * s * np.sqrt(1.0 / n + (grid - xs.mean()) ** 2 / sxx)
    fit = icpt + slope * grid
    inside = bool(np.all((fit - half <= 0.0) & (0.0 <= fit + half)))
    band = [{"x": float(grid[i]), "fit": float(fit[i]), "lower": float(fit[i] - half[i]), "upper": float(fit[i] + half[i])} for i in range(0, BAND_GRID, 20)] + \
           [{"x": float(grid[-1]), "fit": float(fit[-1]), "lower": float(fit[-1] - half[-1]), "upper": float(fit[-1] + half[-1])}]
    out: dict[str, Any] = {
        "parts": g, "readings": m, "alpha": alpha, "repeatability": float(s_r), "df_repeatability": int(df_r), "part_results": parts,
        "average_bias": avg, "average_bias_t": float(t_avg), "average_bias_p": float(2 * stats.t.sf(abs(t_avg), df_r)),
        "slope": slope, "slope_se": float(se_slope), "slope_p": p_slope, "slope_ci": [slope - tq2 * se_slope, slope + tq2 * se_slope],
        "intercept": float(icpt), "intercept_se": float(se_icpt), "intercept_p": p_icpt, "intercept_ci": [float(icpt - tq2 * se_icpt), float(icpt + tq2 * se_icpt)],
        "r2": float(1.0 - float(resid @ resid) / float(((b_all - avg) ** 2).sum())) if float(((b_all - avg) ** 2).sum()) > 0 else 0.0,
        "s_fit": s, "df_fit": int(dfe), "band": band, "zero_in_band": inside, "bias_significant_parts": [p["reference"] for p in parts if p["significant"]],
        "linearity": None, "pct_linearity": float(100.0 * abs(slope)), "pct_bias": None, "process_variation": process_variation,
        "verdict": "pass" if inside else "fail",
    }
    if process_variation:
        out["linearity"] = float(abs(slope) * process_variation)
        out["pct_bias"] = float(100.0 * abs(avg) / process_variation)
    return out


# ------------------------------------------------------------------ nested gauge R&R

def grr_nested(data, tolerance: float | None, pass_pct: float = 10.0, conditional_pct: float = 30.0, ndc_min: float = 5.0) -> dict[str, Any]:
    try:
        a = np.asarray(data, dtype=float)
    except (TypeError, ValueError):
        raise MsaError("the data must be operators x parts x trials, the same number of values everywhere") from None
    if a.ndim != 3 or not np.all(np.isfinite(a)):
        raise MsaError("the data must be operators x parts (of each operator) x trials of numbers")
    o, p, r = a.shape
    if o < 2 or p < 2 or r < 2 or o * p < 5:
        raise MsaError("a nested gauge R&R study needs at least 2 operators with 2 parts each (5 parts in all) and 2 trials per part")
    ops = [f"op{i + 1}" for i in range(o) for _ in range(p * r)]
    parts = [f"p{j + 1}" for _ in range(o) for j in range(p) for _ in range(r)]
    try:
        res = nested.analyse(a.ravel(), {"operator": ops, "part": parts})
    except ValueError as exc:
        raise MsaError(str(exc)) from None
    t_op, t_part = res["table"]
    err = res["error"]
    ev2, av2, pv2 = err["variance"], t_op["variance"], t_part["variance"]
    if not ev2 > 0:
        raise MsaError("the repeated readings are identical: the resolution is too coarse to evaluate the study")
    grr2 = ev2 + av2
    tv2 = grr2 + pv2
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
    notes = ["nested"]
    if o < 3 or o * p < 10:
        notes.append("small_design")
    if pct_tol is None:
        notes.append("no_tolerance")
    if t_op["truncated"] or t_part["truncated"]:
        notes.append("negative_component")
    return {"nested": True, "parts": o * p, "operators": o, "parts_per_operator": p, "trials": r, "pooled_interaction": False,
            "ms": {"operators": t_op["ms"], "parts": t_part["ms"], "error": err["ms"]},
            "table": [{"level": "operator", "df": t_op["df"], "ms": t_op["ms"], "f": t_op["f"], "p_value": t_op["p_value"], "variance": t_op["variance"], "truncated": t_op["truncated"]},
                      {"level": "part", "df": t_part["df"], "ms": t_part["ms"], "f": t_part["f"], "p_value": t_part["p_value"], "variance": t_part["variance"], "truncated": t_part["truncated"]},
                      {"level": "error", "df": err["df"], "ms": err["ms"], "variance": err["variance"]}],
            "sigma": {"ev": math.sqrt(ev2), "av": math.sqrt(av2), "interaction": 0.0, "grr": s_grr, "pv": s_pv, "tv": s_tv},
            "pct_tv": pct_tv, "pct_tol": pct_tol, "ndc": float(ndc), "basis": "tolerance" if pct_tol is not None else "total_variation",
            "limits": {"pass": pass_pct, "conditional": conditional_pct, "ndc": ndc_min}, "verdict": verdict, "notes": notes}


# ------------------------------------------------------------------ uncertainty budget

def budget(components: Sequence[dict], tolerance: float | None, k: float = 2.0, pass_pct: float = 15.0, conditional_pct: float = 30.0,
           lsl: float | None = None, usl: float | None = None) -> dict[str, Any]:
    if not isinstance(components, (list, tuple)) or not 1 <= len(components) <= MAX_COMPONENTS:
        raise MsaError(f"give 1 to {MAX_COMPONENTS} components")
    if not (math.isfinite(k) and 1.0 <= k <= 4.0):
        raise MsaError("the coverage factor k must be from 1 to 4")
    rows = []
    for i, c in enumerate(components, start=1):
        if not isinstance(c, dict) or set(c) - {"name", "type", "value", "distribution", "sensitivity", "dof"}:
            raise MsaError(f"component {i}: allowed keys are name, type, value, distribution, sensitivity, dof")
        name = str(c.get("name") or "").strip()
        dist = c.get("distribution", "standard")
        typ = c.get("type", "B")
        val, sens, dof = c.get("value"), c.get("sensitivity", 1.0), c.get("dof")
        if not name or len(name) > 100:
            raise MsaError(f"component {i}: a name of 1 to 100 characters is needed")
        if dist not in DIVISORS or typ not in ("A", "B"):
            raise MsaError(f"component {i}: distribution must be one of {sorted(DIVISORS)} and type A or B")
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or val < 0:
            raise MsaError(f"component {i}: the value must be a number of 0 or more")
        if isinstance(sens, bool) or not isinstance(sens, (int, float)) or not math.isfinite(sens):
            raise MsaError(f"component {i}: the sensitivity coefficient must be a number")
        if dof is not None and (isinstance(dof, bool) or not isinstance(dof, (int, float)) or not math.isfinite(dof) or dof < 1):
            raise MsaError(f"component {i}: the degrees of freedom must be 1 or more (leave empty for infinite)")
        u = float(val) / DIVISORS[dist]
        rows.append({"name": name, "type": typ, "value": float(val), "distribution": dist, "divisor": DIVISORS[dist], "u": u, "sensitivity": float(sens),
                     "contribution": abs(float(sens)) * u, "dof": None if dof is None else float(dof)})
    uc2 = sum(r["contribution"] ** 2 for r in rows)
    if not uc2 > 0:
        raise MsaError("every component is zero: there is no uncertainty to evaluate")
    uc = math.sqrt(uc2)
    for r in rows:
        r["share"] = r["contribution"] ** 2 / uc2
    den = sum(r["contribution"] ** 4 / r["dof"] for r in rows if r["dof"] is not None and r["contribution"] > 0)
    nu_eff = None if den == 0 else uc ** 4 / den
    big_u = k * uc
    out: dict[str, Any] = {"components": rows, "u_c": uc, "k": k, "U": big_u, "nu_eff": _f(nu_eff),
                           "k_95": float(stats.t.ppf(0.975, nu_eff)) if nu_eff is not None else 1.959964, "tolerance": tolerance,
                           "largest": max(rows, key=lambda r: r["contribution"])["name"], "limits": {"pass": pass_pct, "conditional": conditional_pct}}
    if not tolerance:
        raise MsaError("an uncertainty budget is judged against the tolerance of the measurement system")
    q = 100.0 * 2.0 * big_u / tolerance
    out["q_ms"] = q
    out["verdict"] = "pass" if q <= pass_pct else "conditional" if q <= conditional_pct else "fail"
    if lsl is not None and usl is not None:
        out["zones"] = {"acceptance": [lsl + big_u, usl - big_u], "rejection": [lsl - big_u, usl + big_u], "acceptance_exists": bool(lsl + big_u < usl - big_u)}
    return out

"""Methods of the AIAG MSA Reference Manual, 4th edition (docs/372890491-MSA-Reference-Manual-4th-Edition.pdf) that the program did not have:

  bias, independent sample method (chapter III-B)   n >= 10 readings of one reference: t test and confidence interval of the bias, repeatability against the process
  bias, control chart method (chapter III-B)        the readings of a stability study (g subgroups of m): bias, repeatability from the average range with d2*, degrees of freedom from Appendix C
  gauge R&R, range method (chapter III-B)           two appraisers, each part once: a quick approximation of the overall GRR, not a split into repeatability and reproducibility
  signal detection approach (chapter III-C)         attribute: the width of the grey zone around each specification limit, from the parts the appraisers did not all agree on
  analytic method (chapter III-C)                   attribute: bias and repeatability from the acceptance probabilities of parts with known reference values (gauge performance curve)

Appendix C (Table C 1): d2* and the degrees of freedom of the average range of g subgroups of size m, for g up to 20; for more subgroups the degrees of freedom grow by the
constant difference cd per subgroup and d2* = d2 (1 + 1 / (4 degrees of freedom)).
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np
from scipy import stats

from spc.core.msa import MsaError

# Appendix C: rows g = 1 .. 20, columns m = 2 .. 20. Degrees of freedom of the average range (printed with one decimal) and d2*.
DF_TABLE = [
    [1.0, 2.0, 2.9, 3.8, 4.7, 5.5, 6.3, 7.0, 7.7, 8.3, 9.0, 9.6, 10.2, 10.8, 11.3, 11.9, 12.4, 12.9, 13.4],
    [1.9, 3.8, 5.7, 7.5, 9.2, 10.8, 12.3, 13.8, 15.1, 16.5, 17.8, 19.0, 20.2, 21.3, 22.4, 23.5, 24.5, 25.5, 26.5],
    [2.8, 5.7, 8.4, 11.1, 13.6, 16.0, 18.3, 20.5, 22.6, 24.6, 26.5, 28.4, 30.1, 31.9, 33.5, 35.1, 36.7, 38.2, 39.7],
    [3.7, 7.5, 11.2, 14.7, 18.1, 21.3, 24.4, 27.3, 30.1, 32.7, 35.3, 37.7, 40.1, 42.4, 44.6, 46.7, 48.8, 50.8, 52.8],
    [4.6, 9.3, 13.9, 18.4, 22.6, 26.6, 30.4, 34.0, 37.5, 40.8, 44.0, 47.1, 50.1, 52.9, 55.7, 58.4, 61.0, 63.5, 65.9],
    [5.5, 11.1, 16.7, 22.0, 27.0, 31.8, 36.4, 40.8, 45.0, 49.0, 52.8, 56.5, 60.1, 63.5, 66.8, 70.0, 73.1, 76.1, 79.1],
    [6.4, 12.9, 19.4, 25.6, 31.5, 37.1, 42.5, 47.6, 52.4, 57.1, 61.6, 65.9, 70.0, 74.0, 77.9, 81.6, 85.3, 88.8, 92.2],
    [7.2, 14.8, 22.1, 29.2, 36.0, 42.4, 48.5, 54.3, 59.9, 65.2, 70.3, 75.2, 80.0, 84.6, 89.0, 93.3, 97.4, 101.4, 105.3],
    [8.1, 16.6, 24.9, 32.9, 40.4, 47.7, 54.5, 61.1, 67.3, 73.3, 79.1, 84.6, 90.0, 95.1, 100.1, 104.9, 109.5, 114.1, 118.5],
    [9.0, 18.4, 27.6, 36.5, 44.9, 52.9, 60.6, 67.8, 74.8, 81.5, 87.9, 94.0, 99.9, 105.6, 111.2, 116.5, 121.7, 126.7, 131.6],
    [9.9, 20.2, 30.4, 40.1, 49.4, 58.2, 66.6, 74.6, 82.2, 89.6, 96.6, 103.4, 109.9, 116.2, 122.3, 128.1, 133.8, 139.4, 144.7],
    [10.7, 22.0, 33.1, 43.7, 53.8, 63.5, 72.6, 81.3, 89.7, 97.7, 105.4, 112.7, 119.9, 126.7, 133.3, 139.8, 146.0, 152.0, 157.9],
    [11.6, 23.8, 35.8, 47.3, 58.3, 68.7, 78.6, 88.1, 97.1, 105.8, 114.1, 122.1, 129.8, 137.3, 144.4, 151.4, 158.1, 164.7, 171.0],
    [12.5, 25.7, 38.6, 51.0, 62.8, 74.0, 84.7, 94.9, 104.6, 113.9, 122.9, 131.5, 139.8, 147.8, 155.5, 163.0, 170.3, 177.3, 184.2],
    [13.4, 27.5, 41.3, 54.6, 67.2, 79.3, 90.7, 101.6, 112.1, 122.1, 131.7, 140.9, 149.8, 158.3, 166.6, 174.6, 182.4, 190.0, 197.3],
    [14.3, 29.3, 44.1, 58.2, 71.7, 84.5, 96.7, 108.4, 119.5, 130.2, 140.4, 150.2, 159.7, 168.9, 177.7, 186.3, 194.6, 202.6, 210.4],
    [15.1, 31.1, 46.8, 61.8, 76.2, 89.8, 102.8, 115.1, 127.0, 138.3, 149.2, 159.6, 169.7, 179.4, 188.8, 197.9, 206.7, 215.2, 223.6],
    [16.0, 32.9, 49.5, 65.5, 80.6, 95.1, 108.8, 121.9, 134.4, 146.4, 157.9, 169.0, 179.7, 190.0, 199.9, 209.5, 218.8, 227.9, 236.7],
    [16.9, 34.7, 52.3, 69.1, 85.1, 100.3, 114.8, 128.7, 141.9, 154.5, 166.7, 178.4, 189.6, 200.5, 211.0, 221.1, 231.0, 240.5, 249.8],
    [17.8, 36.5, 55.0, 72.7, 89.6, 105.6, 120.9, 135.4, 149.3, 162.7, 175.5, 187.8, 199.6, 211.0, 222.1, 232.8, 243.1, 253.2, 263.0],
]
D2STAR_TABLE = [
    [1.41421, 1.91155, 2.23887, 2.48124, 2.67253, 2.82981, 2.96288, 3.07794, 3.17905, 3.26909, 3.35016, 3.42378, 3.49116, 3.55333, 3.61071, 3.66422, 3.71424, 3.76118, 3.80537],
    [1.27931, 1.80538, 2.15069, 2.40484, 2.60438, 2.76779, 2.90562, 3.02446, 3.12869, 3.22134, 3.30463, 3.38017, 3.44922, 3.51287, 3.57156, 3.62625, 3.67734, 3.72524, 3.77032],
    [1.23105, 1.76858, 2.12049, 2.37883, 2.58127, 2.74681, 2.88628, 3.00643, 3.11173, 3.20526, 3.28931, 3.36550, 3.43512, 3.49927, 3.55842, 3.61351, 3.66495, 3.71319, 3.75857],
    [1.20621, 1.74989, 2.10522, 2.36571, 2.56964, 2.73626, 2.87656, 2.99737, 3.10321, 3.19720, 3.28163, 3.35815, 3.42805, 3.49246, 3.55183, 3.60712, 3.65875, 3.70715, 3.75268],
    [1.19105, 1.73857, 2.09601, 2.35781, 2.56263, 2.72991, 2.87071, 2.99192, 3.09808, 3.19235, 3.27701, 3.35372, 3.42381, 3.48836, 3.54787, 3.60328, 3.65502, 3.70352, 3.74914],
    [1.18083, 1.73099, 2.08985, 2.35253, 2.55795, 2.72567, 2.86680, 2.98829, 3.09467, 3.18911, 3.27392, 3.35077, 3.42097, 3.48563, 3.54522, 3.60072, 3.65253, 3.70109, 3.74678],
    [1.17348, 1.72555, 2.08543, 2.34875, 2.55460, 2.72263, 2.86401, 2.98568, 3.09222, 3.18679, 3.27172, 3.34866, 3.41894, 3.48368, 3.54333, 3.59888, 3.65075, 3.69936, 3.74509],
    [1.16794, 1.72147, 2.08212, 2.34591, 2.55208, 2.72036, 2.86192, 2.98373, 3.09039, 3.18506, 3.27006, 3.34708, 3.41742, 3.48221, 3.54192, 3.59751, 3.64941, 3.69806, 3.74382],
    [1.16361, 1.71828, 2.07953, 2.34370, 2.55013, 2.71858, 2.86028, 2.98221, 3.08896, 3.18370, 3.26878, 3.34585, 3.41624, 3.48107, 3.54081, 3.59644, 3.64838, 3.69705, 3.74284],
    [1.16014, 1.71573, 2.07746, 2.34192, 2.54856, 2.71717, 2.85898, 2.98100, 3.08781, 3.18262, 3.26775, 3.34486, 3.41529, 3.48016, 3.53993, 3.59559, 3.64755, 3.69625, 3.74205],
    [1.15729, 1.71363, 2.07577, 2.34048, 2.54728, 2.71600, 2.85791, 2.98000, 3.08688, 3.18174, 3.26690, 3.34406, 3.41452, 3.47941, 3.53921, 3.59489, 3.64687, 3.69558, 3.74141],
    [1.15490, 1.71189, 2.07436, 2.33927, 2.54621, 2.71504, 2.85702, 2.97917, 3.08610, 3.18100, 3.26620, 3.34339, 3.41387, 3.47879, 3.53861, 3.59430, 3.64630, 3.69503, 3.74087],
    [1.15289, 1.71041, 2.07316, 2.33824, 2.54530, 2.71422, 2.85627, 2.97847, 3.08544, 3.18037, 3.26561, 3.34282, 3.41333, 3.47826, 3.53810, 3.59381, 3.64582, 3.69457, 3.74041],
    [1.15115, 1.70914, 2.07213, 2.33737, 2.54452, 2.71351, 2.85562, 2.97787, 3.08487, 3.17984, 3.26510, 3.34233, 3.41286, 3.47781, 3.53766, 3.59339, 3.64541, 3.69417, 3.74002],
    [1.14965, 1.70804, 2.07125, 2.33661, 2.54385, 2.71290, 2.85506, 2.97735, 3.08438, 3.17938, 3.26465, 3.34191, 3.41245, 3.47742, 3.53728, 3.59302, 3.64505, 3.69382, 3.73969],
    [1.14833, 1.70708, 2.07047, 2.33594, 2.54326, 2.71237, 2.85457, 2.97689, 3.08395, 3.17897, 3.26427, 3.34154, 3.41210, 3.47707, 3.53695, 3.59270, 3.64474, 3.69351, 3.73939],
    [1.14717, 1.70623, 2.06978, 2.33535, 2.54274, 2.71190, 2.85413, 2.97649, 3.08358, 3.17861, 3.26393, 3.34121, 3.41178, 3.47677, 3.53666, 3.59242, 3.64447, 3.69325, 3.73913],
    [1.14613, 1.70547, 2.06917, 2.33483, 2.54228, 2.71148, 2.85375, 2.97613, 3.08324, 3.17829, 3.26362, 3.34092, 3.41150, 3.47650, 3.53640, 3.59216, 3.64422, 3.69301, 3.73890],
    [1.14520, 1.70480, 2.06862, 2.33436, 2.54187, 2.71111, 2.85341, 2.97581, 3.08294, 3.17801, 3.26335, 3.34066, 3.41125, 3.47626, 3.53617, 3.59194, 3.64400, 3.69280, 3.73869],
    [1.14437, 1.70419, 2.06813, 2.33394, 2.54149, 2.71077, 2.85310, 2.97552, 3.08267, 3.17775, 3.26311, 3.34042, 3.41103, 3.47605, 3.53596, 3.59174, 3.64380, 3.69260, 3.73850],
]
D2_INF = [1.12838, 1.69257, 2.05875, 2.32593, 2.53441, 2.70436, 2.8472, 2.97003, 3.07751, 3.17287, 3.25846, 3.33598, 3.40676, 3.47193, 3.53198, 3.58788, 3.64006, 3.68896, 3.735]  # m = 2 .. 20
CD = [0.876, 1.815, 2.7378, 3.623, 4.4658, 5.2673, 6.0305, 6.7582, 7.4539, 8.1207, 8.7602, 9.3751, 9.9679, 10.5396, 11.0913, 11.6259, 12.144, 12.6468, 13.1362]  # constant difference of the degrees of freedom per added subgroup


def d2_star(m: int, g: int) -> tuple[float, float]:
    """(d2*, degrees of freedom) of the average range of g subgroups of size m (Appendix C)."""
    if isinstance(m, bool) or isinstance(g, bool) or not isinstance(m, int) or not isinstance(g, int) or not 2 <= m <= 20 or g < 1:
        raise MsaError("the subgroup size must be 2 to 20 and the number of subgroups 1 or more")
    k = m - 2
    if g <= 20:
        return float(D2STAR_TABLE[g - 1][k]), float(DF_TABLE[g - 1][k])
    nu = DF_TABLE[19][k] + (g - 20) * CD[k]
    return float(D2_INF[k] * (1.0 + 1.0 / (4.0 * nu))), float(nu)


def _tv(process_sd: float | None, tolerance: float | None) -> tuple[float | None, str | None]:
    """The total variation for %EV and %GRR: the expected process standard deviation (preferred) or the tolerance divided by 6."""
    if process_sd is not None:
        if not (math.isfinite(process_sd) and process_sd > 0):
            raise MsaError("the process standard deviation must be positive")
        return float(process_sd), "process"
    if tolerance is not None:
        if not (math.isfinite(tolerance) and tolerance > 0):
            raise MsaError("the tolerance must be positive")
        return float(tolerance) / 6.0, "tolerance"
    return None, None


def _grade(pct: float | None, pass_pct: float, conditional_pct: float) -> str | None:
    return None if pct is None else "pass" if pct <= pass_pct else "conditional" if pct <= conditional_pct else "fail"


def _bias_result(bias: float, sd_r: float, n_readings: int, df: float, reference: float, alpha: float, tv: float | None, tv_basis: str | None, pass_pct: float, conditional_pct: float) -> dict[str, Any]:
    se = sd_r / math.sqrt(n_readings)
    t = bias / se
    q = float(stats.t.ppf(1.0 - alpha / 2.0, df))
    p = float(2.0 * stats.t.sf(abs(t), df))
    lo, hi = bias - q * se, bias + q * se
    ev_pct = None if tv is None else 100.0 * sd_r / tv
    acceptable = lo <= 0.0 <= hi
    ev_grade = _grade(ev_pct, pass_pct, conditional_pct)
    # the bias analysis assumes that the repeatability is acceptable: with a large %EV a statistically zero bias says little
    verdict = "fail" if not acceptable or ev_grade == "fail" else ("conditional" if ev_grade == "conditional" else "pass")
    return {"n": n_readings, "reference": float(reference), "bias": float(bias), "repeatability": float(sd_r), "se": float(se), "t": float(t), "df": float(df), "t_critical": q, "p_value": p,
            "alpha": alpha, "ci": [float(lo), float(hi)], "bias_acceptable": bool(acceptable), "ev_pct": ev_pct, "tv": tv, "tv_basis": tv_basis,
            "limits": {"pass": pass_pct, "conditional": conditional_pct}, "ev_verdict": ev_grade, "verdict": verdict}


def bias_independent(values: Sequence[float], reference: float, process_sd: float | None = None, tolerance: float | None = None, alpha: float = 0.05,
                     pass_pct: float = 10.0, conditional_pct: float = 30.0) -> dict[str, Any]:
    """Bias, independent sample method: n >= 10 readings of one part of known reference value. bias = average - reference; repeatability = s of the readings; standard error s / sqrt(n);
    t = bias / standard error with n - 1 degrees of freedom; the bias is acceptable (statistically zero) when zero lies inside the 1 - alpha confidence interval of the bias."""
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or not np.all(np.isfinite(x)) or not math.isfinite(reference):
        raise MsaError("give the readings as a list of numbers and the reference value as a number")
    if x.size < 10:
        raise MsaError("the independent sample method needs at least 10 readings of the reference part (15 or more are usual)")
    if not 0 < alpha < 0.5:
        raise MsaError("alpha must be between 0 and 0.5")
    s = float(x.std(ddof=1))
    if not s > 0:
        raise MsaError("the readings do not vary: the resolution is too coarse to evaluate the study")
    tv, basis = _tv(process_sd, tolerance)
    out = _bias_result(float(x.mean()) - reference, s, int(x.size), x.size - 1.0, reference, alpha, tv, basis, pass_pct, conditional_pct)
    out["method"] = "independent"
    out["mean"] = float(x.mean())
    return out


def bias_from_chart(grand_mean: float, average_range: float, m: int, g: int, reference: float, process_sd: float | None = None, tolerance: float | None = None,
                    alpha: float = 0.05, pass_pct: float = 10.0, conditional_pct: float = 30.0) -> dict[str, Any]:
    """Bias, control chart method, from the numbers of a stable X-bar and R chart of a stability study: bias = grand average - reference, repeatability = R-bar / d2*, standard error
    = repeatability / sqrt(g m), degrees of freedom and d2* from Appendix C for g subgroups of size m."""
    if m < 2:
        raise MsaError("with one reading per subgroup use the independent sample method")
    if not (math.isfinite(grand_mean) and math.isfinite(average_range) and average_range > 0 and math.isfinite(reference)):
        raise MsaError("the grand average, the average range (positive) and the reference value must be numbers")
    if not 0 < alpha < 0.5:
        raise MsaError("alpha must be between 0 and 0.5")
    d2s, df = d2_star(m, g)
    tv, basis = _tv(process_sd, tolerance)
    out = _bias_result(grand_mean - reference, average_range / d2s, g * m, df, reference, alpha, tv, basis, pass_pct, conditional_pct)
    out.update(method="control_chart", m=m, g=g, grand_mean=float(grand_mean), average_range=float(average_range), d2_star=d2s)
    return out


def bias_control_chart(subgroups, reference: float, process_sd: float | None = None, tolerance: float | None = None, alpha: float = 0.05,
                       pass_pct: float = 10.0, conditional_pct: float = 30.0) -> dict[str, Any]:
    """The same from the readings (g subgroups of m, in time order). The chart must show a stable measurement system first: the points of the X-bar chart (limits from R-bar) and of the R chart
    are checked, and `stable` says whether all of them are inside; the bias of an unstable system is not evaluated."""
    try:
        a = np.asarray(subgroups, dtype=float)
    except (TypeError, ValueError):
        raise MsaError("give the subgroups as lists of numbers, the same size in every subgroup") from None
    if a.ndim != 2 or not np.all(np.isfinite(a)):
        raise MsaError("give the subgroups as lists of numbers, the same size in every subgroup")
    g, m = a.shape
    if m < 2 or m > 20:
        raise MsaError("the subgroups need 2 to 20 readings each (with one reading use the independent sample method)")
    if g < 5:
        raise MsaError("a stability study for the bias needs at least 5 subgroups (20 or more are usual)")
    ranges = a.max(axis=1) - a.min(axis=1)
    rbar = float(ranges.mean())
    if not rbar > 0:
        raise MsaError("the readings do not vary within the subgroups: the resolution is too coarse to evaluate the study")
    means = a.mean(axis=1)
    grand = float(means.mean())
    from spc.core.constants import d2 as d2_const, d3 as d3_const

    a2 = 3.0 / (d2_const(m) * math.sqrt(m))  # the factors of the X-bar and R chart with 3 sigma limits
    d4 = 1.0 + 3.0 * d3_const(m) / d2_const(m)
    d3f = max(0.0, 1.0 - 3.0 * d3_const(m) / d2_const(m))
    out_x = int(np.sum(np.abs(means - grand) > a2 * rbar))
    out_r = int(np.sum((ranges > d4 * rbar) | (ranges < d3f * rbar)))
    out = bias_from_chart(grand, rbar, m, g, reference, process_sd, tolerance, alpha, pass_pct, conditional_pct)
    out.update(stable=(out_x == 0 and out_r == 0), points_outside={"xbar": out_x, "range": out_r})
    if not out["stable"]:
        out["verdict"] = "fail"
    return out


def range_method(a: Sequence[float], b: Sequence[float], process_sd: float | None = None, tolerance: float | None = None,
                 pass_pct: float = 10.0, conditional_pct: float = 30.0) -> dict[str, Any]:
    """Gauge R&R, range method: two appraisers measure each part once; R-bar is the average absolute difference; GRR = R-bar / d2* with m = 2 and g = number of parts. It does not split the
    variation into repeatability and reproducibility: it is a quick check, typically that the GRR has not changed. %GRR is against the process standard deviation (the manual's
    example) or, with the tolerance, 6 GRR / tolerance."""
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if x.ndim != 1 or x.shape != y.shape or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise MsaError("give the readings of the two appraisers as two lists of numbers, one reading per part")
    g = int(x.size)
    if g < 5:
        raise MsaError("the range method uses at least 5 parts (10 detect an unacceptable system more often)")
    r = np.abs(x - y)
    rbar = float(r.mean())
    if not rbar > 0:
        raise MsaError("the two appraisers read exactly the same on every part: the resolution is too coarse to evaluate the study")
    d2s, df = d2_star(2, g)
    grr = rbar / d2s
    out: dict[str, Any] = {"parts": g, "ranges": [float(v) for v in r], "average_range": rbar, "d2_star": d2s, "df": df, "grr": float(grr), "pct_process": None, "pct_tol": None,
                          "process_sd": process_sd, "tolerance": tolerance, "limits": {"pass": pass_pct, "conditional": conditional_pct}}
    if process_sd is not None:
        if not (math.isfinite(process_sd) and process_sd > 0):
            raise MsaError("the process standard deviation must be positive")
        out["pct_process"] = float(100.0 * grr / process_sd)
    if tolerance is not None:
        if not (math.isfinite(tolerance) and tolerance > 0):
            raise MsaError("the tolerance must be positive")
        out["pct_tol"] = float(100.0 * 6.0 * grr / tolerance)
    if out["pct_process"] is None and out["pct_tol"] is None:
        raise MsaError("give the process standard deviation or the tolerance to judge the GRR")
    basis = "process" if out["pct_process"] is not None else "tolerance"
    out["basis"] = basis
    out["pct"] = out["pct_process"] if basis == "process" else out["pct_tol"]
    out["verdict"] = _grade(out["pct"], pass_pct, conditional_pct)
    return out


def signal_detection(reference: Sequence[float], codes: Sequence[str], lsl: float, usl: float, process_sd: float | None = None,
                     pass_pct: float = 10.0, conditional_pct: float = 30.0) -> dict[str, Any]:
    """Signal detection approach for an attribute study. Each part has a reference value measured by a variable system and a code: '+' all appraisers accepted it in all trials, '-' all
    rejected it, 'x' they did not all agree. Around each specification limit the grey zone (region II) runs from the last part accepted by all to the first part rejected by all; d_LSL and
    d_USL are their widths and d, the average, estimates the GRR (6 sigma of the measurement). %GRR is d against the tolerance, or against the process 6 sigma when that is smaller (the
    manual: compare with the more restrictive of the two)."""
    x = np.asarray(reference, dtype=float)
    c = [str(v) for v in codes]
    if x.ndim != 1 or x.size != len(c) or not np.all(np.isfinite(x)) or any(v not in ("+", "-", "x") for v in c):
        raise MsaError("give one reference value and one code (+, - or x) for each part")
    if not (math.isfinite(lsl) and math.isfinite(usl) and lsl < usl):
        raise MsaError("the lower specification limit must be smaller than the upper one")
    if x.size < 20:
        raise MsaError("a signal detection study needs at least 20 parts, with some of them near each specification limit")
    mid = (lsl + usl) / 2.0
    acc = np.array([v == "+" for v in c])
    rej = np.array([v == "-" for v in c])
    # upper side: the first part rejected by all above the middle, and the last part accepted by all below it
    up = x[rej & (x >= mid)]
    lo = x[rej & (x < mid)]
    if up.size == 0 or lo.size == 0:
        raise MsaError("the study has no part rejected by all appraisers on one side of the tolerance: parts beyond both specification limits are needed")
    out_hi = float(up.min())
    out_lo = float(lo.max())
    ok_hi = x[acc & (x < out_hi) & (x >= mid)]
    ok_lo = x[acc & (x > out_lo) & (x < mid)]
    inner = x[acc & (x > out_lo) & (x < out_hi)]
    if inner.size == 0:
        raise MsaError("no part was accepted by all appraisers: the study cannot find the grey zones")
    last_ok_hi = float(ok_hi.max()) if ok_hi.size else float(inner.max())
    first_ok_lo = float(ok_lo.min()) if ok_lo.size else float(inner.min())
    d_usl = out_hi - last_ok_hi
    d_lsl = first_ok_lo - out_lo
    d = (d_usl + d_lsl) / 2.0
    tol = usl - lsl
    six = None if process_sd is None else 6.0 * process_sd
    if process_sd is not None and not (math.isfinite(process_sd) and process_sd > 0):
        raise MsaError("the process standard deviation must be positive")
    denom, basis = (min(tol, six), "process" if six < tol else "tolerance") if six is not None else (tol, "tolerance")
    pct = float(100.0 * d / denom)
    return {"parts": int(x.size), "lsl": lsl, "usl": usl, "d_lsl": float(d_lsl), "d_usl": float(d_usl), "d": float(d), "tolerance": float(tol), "process_6sigma": six,
            "basis": basis, "pct": pct, "pct_tol": float(100.0 * d / tol), "last_rejected_below": out_lo, "first_accepted_above": first_ok_lo,
            "last_accepted_below_usl": last_ok_hi, "first_rejected_above": out_hi, "counts": {"accepted_by_all": int(acc.sum()), "rejected_by_all": int(rej.sum()), "disagreement": int(x.size - acc.sum() - rej.sum())},
            "limits": {"pass": pass_pct, "conditional": conditional_pct}, "verdict": _grade(pct, pass_pct, conditional_pct)}


def codes_from_ratings(ratings: Mapping[str, Sequence[Sequence[int]]]) -> list[str]:
    """'+' when every appraiser accepted the part in every trial, '-' when all rejected it, 'x' otherwise. `ratings[name]` is one list per trial with a 0/1 decision for each part."""
    cols = [np.asarray(t, dtype=int) for trials in ratings.values() for t in trials]
    if not cols or len({c.shape for c in cols}) != 1 or cols[0].ndim != 1:
        raise MsaError("every appraiser and trial needs one decision (1 accept, 0 reject) for each part")
    votes = np.vstack(cols)
    if not np.all((votes == 0) | (votes == 1)):
        raise MsaError("the decisions are 1 (accept) or 0 (reject)")
    return ["+" if v.all() else "-" if not v.any() else "x" for v in votes.T]


ANALYTIC_TRIALS = 20
ANALYTIC_UNBIAS = 1.08  # the adjustment for 20 trials, with the 99 % repeatability range
ANALYTIC_T_FACTOR = 6.078
ANALYTIC_T_CRITICAL = 2.093  # t(0.025; 19)


def analytic_method(reference: Sequence[float], accepts: Sequence[int], limit: float, side: str = "lower", m: int = ANALYTIC_TRIALS, tolerance: float | None = None) -> dict[str, Any]:
    """Analytic method for an attribute gauge: parts of known reference value are run m = 20 times through the gauge and the accepts a counted. The probability of acceptance is
    a/m with the adjustments (a + 0.5)/m below one half and (a - 0.5)/m above; a = 0 gives 0 except 0.025 for the largest reference with a = 0, a = m gives 1 except 0.975 for the smallest
    with a = m. A line is fitted through the points on normal probability paper (reference value on the normal quantile of the probability, least squares); the reference value at P = 0.5 gives
    the bias (lower limit minus that value; for the upper limit that value minus the limit) and the values at P = 0.995 and 0.005 the repeatability (their difference / 1.08, / 5.15 for sigma).
    The bias differs from zero when t = 6.078 |bias| / sigma exceeds t(0.025; 19) = 2.093. The constants 1.08, 6.078 and 2.093 are for 20 trials."""
    x = np.asarray(reference, dtype=float)
    a = np.asarray(accepts, dtype=float)
    if m != ANALYTIC_TRIALS:
        raise MsaError("the constants of the analytic method (1.08, 6.078, 2.093) are for 20 trials per part")
    if side not in ("lower", "upper"):
        raise MsaError("side must be lower or upper")
    if x.ndim != 1 or a.shape != x.shape or not np.all(np.isfinite(x)) or not math.isfinite(limit):
        raise MsaError("give one reference value and one number of accepts for each part")
    if np.any(a != np.floor(a)) or np.any(a < 0) or np.any(a > m):
        raise MsaError(f"the number of accepts is a whole number from 0 to {m}")
    if np.unique(x).size != x.size:
        raise MsaError("the reference values must differ from part to part")
    if x.size < 8:
        raise MsaError("the analytic method uses at least 8 parts")
    sign = 1.0 if side == "lower" else -1.0  # the upper limit is the mirror image: the acceptance falls with the reference value, so it is fitted on the negated reference value
    xs = sign * x
    order = np.argsort(xs)
    xs, acc, x_orig = xs[order], a[order], x[order]
    if acc[0] != 0 or acc[-1] != m:
        raise MsaError(("the part with the smallest reference value must have no accepts and the largest all " + str(m) + ": add parts until it does") if side == "lower" else
                       ("at the upper limit the part with the largest reference value must have no accepts and the smallest all " + str(m) + ": add parts until it does"))
    between = int(np.sum((acc >= 1) & (acc <= m - 1)))
    p = np.empty(xs.size)
    for i, ai in enumerate(acc):
        r = ai / m
        p[i] = 0.0 if ai == 0 else 1.0 if ai == m else (ai + 0.5) / m if r < 0.5 else (ai - 0.5) / m if r > 0.5 else 0.5
    p[np.flatnonzero(acc == 0)[-1]] = 0.5 / m  # the largest reference value (in the fitted direction) with no accepts: 0.025
    p[np.flatnonzero(acc == m)[0]] = 1.0 - 0.5 / m  # the smallest reference value with all accepts: 0.975
    use = (p > 0.0) & (p < 1.0)
    if between < 6 or use.sum() < 5:
        raise MsaError("the study needs at least 6 parts with 1 to 19 accepts (the criteria of the manual); run more parts at the midpoints of the intervals")
    z = stats.norm.ppf(p[use])
    slope, icpt = np.polyfit(z, xs[use], 1)  # fitted reference value = icpt + slope * z
    if not slope > 0:
        raise MsaError("the acceptance does not rise with the reference value")

    def x_at(prob: float) -> float:  # the reference value (in the units given) at which the acceptance probability of the fitted line is `prob`
        return float(sign * (icpt + slope * stats.norm.ppf(prob)))

    x50, x995, x005 = x_at(0.5), x_at(0.995), x_at(0.005)
    corrected_range = abs(x995 - x005) / ANALYTIC_UNBIAS
    sigma = corrected_range / 5.15
    bias = (limit - x50) if side == "lower" else (x50 - limit)  # positive: the gauge accepts parts beyond the limit
    t = ANALYTIC_T_FACTOR * abs(bias) / sigma
    out = {"parts": int(x.size), "trials": m, "side": side, "limit": float(limit), "between": between,
           "points": [{"reference": float(x_orig[i]), "accepts": int(acc[i]), "pac": float(p[i])} for i in range(x.size)],
           "x_at_0_5": x50, "x_at_0_995": x995, "x_at_0_005": x005, "bias": float(bias), "range_99": float(abs(x995 - x005)), "repeatability_range": float(corrected_range),
           "repeatability": float(sigma), "grr_range": float(6.0 * sigma), "t": float(t), "t_critical": ANALYTIC_T_CRITICAL, "bias_significant": bool(t > ANALYTIC_T_CRITICAL),
           "pct_tol": None if tolerance is None else float(100.0 * 6.0 * sigma / tolerance)}
    out["verdict"] = "fail" if out["bias_significant"] else "pass"
    return out

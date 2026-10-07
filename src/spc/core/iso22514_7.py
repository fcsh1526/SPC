"""Capability of measurement processes after ISO 22514-7:2012 (first edition, the BSI copy that was uploaded; the 2021 edition is not available here).

Everything follows the clauses of the standard; the numbers of the clauses are in the docstrings. Where the printed examples and formulas of the standard disagree, the
formula is used and the difference is stated (see `docs/TRACEABILITY.md`).

Measuring system (clauses 6.2.2, 7.1):
    u_CAL   calibration of the standard: U_CAL / k_CAL                                      (Table 3)
    u_MPE   maximum permissible error, rectangular: MPE / sqrt 3; several: sqrt(sum MPE_i^2 / 3)   (Table 1)
    u_RE    resolution, rectangular: RE / sqrt 12                                           (Table 2)
    u_BI    bias: |mean - x_m| / sqrt 3                                                     (7.1.2.3)
    u_EVR   repeatability on a standard: s of K >= 30 measurements, or of N >= 2 standards with N K >= 30 (average variance)
    u_LIN   linearity: 0 (known to be linear), a / sqrt 3 (known deviation a or MPE), or from the lack of fit of the regression y = b0 + b1 x:
            u_LIN = sqrt(SS_LIN / (N - 2)), u_EVR = sqrt(SS_EVR / (N K - N)), SS_LIN = SS_E - SS_EVR     (Tables A.3, B.1)
    u_MS-REST  other components
Measurement process (clauses 6.2.3, 7.2):
    u_EVO, u_AV (u_GV), u_IA   analysis of variance of workpieces x operators (or measuring systems) x repetitions (Tables A.5, A.6, B.2, B.3):
            u_EVO = sqrt(MS_pool), u_AV = sqrt((MS_AV - MS_pool) / (N_P N_R)); the interaction is pooled with the repeatability when it is not significant (F < F0, alpha 5 %)
    u_STAB  stability over time, u_OBJ = a_OBJ / sqrt 3, u_T = sqrt(u_TD^2 + u_TA^2), u_TD = dT alpha l / sqrt 3, u_TA = |T - 20| u_alpha l / sqrt 3, u_REST
Combination (Table 9, clause 8):
    u_MS^2 = u_CAL^2 + u_LIN^2 + u_BI^2 + u_EV^2 + u_MS-REST^2,                 u_EV = max(u_EVR, u_RE)
    u_MP^2 = u_CAL^2 + u_LIN^2 + u_BI^2 + u_EV^2 + u_MS-REST^2 + u_AV^2 + u_GV^2 + u_STAB^2 + u_OBJ^2 + u_T^2 + u_REST^2 + sum u_IAi^2,   u_EV = max(u_EVR, u_EVO, u_RE)
    U = k u with k = 2; with fewer than 30 measurements U = t(1 - alpha/2; nu) u, nu = workpieces x operators x gauges x (repetitions - 1)   (8.2)
Capability (clause 9):  Q_MS = 2 U_MS / (U - L), Q_MP = 2 U_MP / (U - L)  (recommended <= 15 % and <= 30 %);
    C_MS = 0.3 (U - L) / (6 u_MS), C_MP = 0.3 (U - L) / (3 u_MP)  (recommended > 1.33)
Production process against measurement process (clause 10, B.4):  C_p,p = (1 / C_obs^2 - 2.25 Q_MP^2)^(-1/2)
Attribute processes (clause 12): Bowker test of symmetry on the classes of three trials per part; the uncertainty range from reference values (signal detection).
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import Any, Mapping, Sequence

import numpy as np
from scipy import stats

from spc.core.msa import MsaError

SQRT3 = math.sqrt(3.0)
SQRT12 = math.sqrt(12.0)
MIN_STANDARD_VALUES = 30  # 7.1.2.1, 7.1.3.1, 7.2.2: the least sample size of each study
UNIMPORTANT_SHARE = 0.10  # 6.1: a component below 10 % of the largest one is unimportant
Q_MS_MAX, Q_MP_MAX, C_MIN = 15.0, 30.0, 1.33  # 9.1.1, 9.2 (by common practice)
Q_ATTR_RULE = 20.0  # 12.1: the uncertainty zone should, as a rule of thumb, not exceed 20 %
REFERENCE_TEMPERATURE = 20.0


def _f(x):
    return None if x is None or not math.isfinite(float(x)) else float(x)


def _num(value, name, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MsaError(f"{name} must be a number")
    if positive and not value > 0 or nonnegative and value < 0:
        raise MsaError(f"{name} must be {'positive' if positive else 'zero or more'}")
    return float(value)


# ------------------------------------------------------------------ single components (Tables 1, 2, 3, 6)

def u_mpe(*mpe: float) -> float:
    """Table 1: rectangular distribution, u_MPE = MPE / sqrt 3; with several limits sqrt(MPE_1^2 / 3 + MPE_2^2 / 3 + ...)."""
    if not mpe:
        raise MsaError("give at least one maximum permissible error")
    return math.sqrt(sum(_num(m, "MPE", nonnegative=True) ** 2 / 3.0 for m in mpe))


def u_resolution(resolution: float) -> float:
    """Table 2: u_RE = RE / sqrt 12 (the half of the digital step, rectangular)."""
    return _num(resolution, "the resolution", positive=True) / SQRT12


def u_calibration(expanded: float | None = None, k: float | None = None, standard: float | None = None) -> float:
    """Table 3: the standard uncertainty of the certificate, or U_CAL / k_CAL."""
    if standard is not None:
        return _num(standard, "the calibration uncertainty", nonnegative=True)
    if expanded is None or k is None:
        raise MsaError("give the standard uncertainty of the calibration, or the expanded one with its coverage factor")
    return _num(expanded, "the expanded calibration uncertainty", nonnegative=True) / _num(k, "the coverage factor", positive=True)


def u_object(a_obj: float) -> float:
    """Table 6: non-homogeneity of the part, u_OBJ = a_OBJ / sqrt 3."""
    return _num(a_obj, "a_OBJ", nonnegative=True) / SQRT3


def u_temperature(delta_t: float, alpha: float, length: float, mean_temperature: float = REFERENCE_TEMPERATURE, u_alpha: float = 0.0) -> dict:
    """Table 6, 6.2.3.6: u_T = sqrt(u_TD^2 + u_TA^2), u_TD = dT alpha l / sqrt 3 (temperature difference), u_TA = |T - 20 C| u_alpha l / sqrt 3 (expansion coefficient)."""
    d, a, l_, t, ua = (_num(delta_t, "the temperature difference"), _num(alpha, "the expansion coefficient", nonnegative=True), _num(length, "the length", nonnegative=True),
                       _num(mean_temperature, "the mean temperature"), _num(u_alpha, "u_alpha", nonnegative=True))
    td = abs(d) * a * l_ / SQRT3
    ta = abs(t - REFERENCE_TEMPERATURE) * ua * l_ / SQRT3
    return {"u_td": td, "u_ta": ta, "u_t": math.hypot(td, ta)}


# ------------------------------------------------------------------ repeatability and bias on standards (7.1.2)

def repeatability_standard(values: Sequence[float], reference: float) -> dict:
    """7.1.2: at least 30 measurements of one standard (removed and replaced between the measurements). u_EVR = s, B = mean - x_m, u_BI = |B| / sqrt 3."""
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or not np.all(np.isfinite(x)):
        raise MsaError("the measurements must be a list of numbers")
    if x.size < MIN_STANDARD_VALUES:
        raise MsaError(f"at least {MIN_STANDARD_VALUES} measurements of the standard are needed (ISO 22514-7, 7.1.2.1)")
    ref = _num(reference, "the reference value")
    s = float(x.std(ddof=1))
    if not s > 0:
        raise MsaError("the measurements do not vary: the resolution is too coarse to evaluate the study")
    bias = float(x.mean()) - ref
    return {"n": int(x.size), "mean": float(x.mean()), "reference": ref, "bias": bias, "u_evr": s, "u_bi": abs(bias) / SQRT3}


def repeatability_standards(references: Sequence[float], values: Sequence[Sequence[float]]) -> dict:
    """7.1.2.3 (more than one standard): K measurements on each of N >= 2 standards, N K >= 30. The largest mean deviation is the bias; the variance is assumed constant,
    so the average variance is used for u_EVR."""
    try:
        y = np.asarray(values, dtype=float)
        x = np.asarray(references, dtype=float)
    except (TypeError, ValueError):
        raise MsaError("give the same number of measurements for every standard") from None
    if y.ndim != 2 or x.ndim != 1 or y.shape[0] != x.size or not np.all(np.isfinite(y)) or not np.all(np.isfinite(x)):
        raise MsaError("give one reference value per standard and its measurements")
    n, k = y.shape
    if n < 2 or k < 2 or n * k < MIN_STANDARD_VALUES:
        raise MsaError(f"at least 2 standards with N x K >= {MIN_STANDARD_VALUES} measurements are needed (ISO 22514-7, Table 4)")
    var = y.var(axis=1, ddof=1)
    if not float(var.mean()) > 0:
        raise MsaError("the measurements do not vary: the resolution is too coarse to evaluate the study")
    dev = y.mean(axis=1) - x
    worst = int(np.argmax(np.abs(dev)))
    return {"standards": int(n), "repeats": int(k), "u_evr": math.sqrt(float(var.mean())), "bias": float(dev[worst]), "bias_standard": float(x[worst]), "u_bi": abs(float(dev[worst])) / SQRT3,
            "deviations": [float(d) for d in dev], "variances_differ": bool(var.max() > 4.0 * var.min())}


# ------------------------------------------------------------------ linearity (7.1.3, Tables A.1 to A.3, B.1)

def linearity(references: Sequence[float], values: Sequence[Sequence[float]], alpha: float = 0.05) -> dict:
    """Regression y = b0 + b1 x of the readings on the reference values, and the lack of fit against the pure error (analysis of variance, Table B.1).
    N standards, K readings each (at least three standards and three readings, N K >= 30)."""
    try:
        y = np.asarray(values, dtype=float)
        x = np.asarray(references, dtype=float)
    except (TypeError, ValueError):
        raise MsaError("give the same number of readings for every standard") from None
    if y.ndim != 2 or x.ndim != 1 or y.shape[0] != x.size or not np.all(np.isfinite(y)) or not np.all(np.isfinite(x)):
        raise MsaError("give one reference value per standard and its readings")
    n, k = y.shape
    if n < 3 or k < 3 or n * k < MIN_STANDARD_VALUES:
        raise MsaError(f"a linearity study needs at least 3 standards with 3 readings each and N x K >= {MIN_STANDARD_VALUES} (ISO 22514-7, 7.1.3.1)")
    if np.unique(x).size != n:
        raise MsaError("the reference values must differ from standard to standard")
    if not 0 < alpha < 0.5:
        raise MsaError("alpha must be between 0 and 0.5")
    xs = np.repeat(x, k)
    ys = y.ravel()
    sxx = float(((xs - xs.mean()) ** 2).sum())
    b1 = float(((xs - xs.mean()) * (ys - ys.mean())).sum() / sxx)
    b0 = float(ys.mean() - b1 * xs.mean())
    ss_e = float(((ys - (b0 + b1 * xs)) ** 2).sum())
    row_mean = y.mean(axis=1)
    ss_evr = float(((y - row_mean[:, None]) ** 2).sum())
    ss_lin = max(ss_e - ss_evr, 0.0)
    df_lin, df_evr = n - 2, n * k - n
    ms_lin, ms_evr = ss_lin / df_lin, ss_evr / df_evr
    if not ms_evr > 0:
        raise MsaError("the repeated readings are identical: the resolution is too coarse to evaluate the study")
    f_value = ms_lin / ms_evr
    f_crit = float(stats.f.ppf(1 - alpha, df_lin, df_evr))
    sd = y.std(axis=1, ddof=1)
    levene_p = float(stats.bartlett(*[row for row in y]).pvalue) if float(sd.min()) > 0 else 0.0  # 7.1.3.2: the residual standard deviation is the same on every standard (normal readings: Bartlett)
    return {"standards": int(n), "repeats": int(k), "n": int(n * k), "x_mean": float(xs.mean()), "y_mean": float(ys.mean()), "beta0": b0, "beta1": b1,
            "ss_e": ss_e, "ss_evr": ss_evr, "ss_lin": ss_lin, "df_lin": int(df_lin), "df_evr": int(df_evr), "ms_lin": ms_lin, "ms_evr": ms_evr,
            "f": float(f_value), "f_crit": f_crit, "lack_of_fit": bool(f_value > f_crit), "alpha": alpha,
            "u_lin": math.sqrt(ms_lin), "u_evr": math.sqrt(ms_evr), "residual_sd": math.sqrt(ss_e / (n * k - 2)), "standard_sd": [float(s) for s in sd],
            "sd_constant": bool(levene_p >= alpha), "sd_test_p": levene_p}


def linearity_deviation(references: Sequence[float], values: Sequence[Sequence[float]], at: float | None = None) -> dict:
    """7.1.3.4, Table 8 (instance 2): the bias y - x of every reading is regressed on x, bias(x) = a + b x; the linearity is the deviation of that line at `at`
    (the upper specification limit in the example; by default the larger deviation at the two ends of the range of the standards), u_LIN = deviation / sqrt 3."""
    try:
        y = np.asarray(values, dtype=float)
        x = np.asarray(references, dtype=float)
    except (TypeError, ValueError):
        raise MsaError("give the same number of readings for every standard") from None
    if y.ndim != 2 or x.ndim != 1 or y.shape[0] != x.size or x.size < 3 or y.shape[1] < 2 or not np.all(np.isfinite(y)):
        raise MsaError("give at least 3 standards with at least 2 readings each")
    if np.unique(x).size != x.size:
        raise MsaError("the reference values must differ from standard to standard")
    xs = np.repeat(x, y.shape[1])
    bias = y.ravel() - xs
    sxx = float(((xs - xs.mean()) ** 2).sum())
    b = float(((xs - xs.mean()) * (bias - bias.mean())).sum() / sxx)
    a = float(bias.mean() - b * xs.mean())
    points = [float(x.min()), float(x.max())] if at is None else [_num(at, "the place of the linearity")]
    devs = [abs(a + b * p) for p in points]
    i = int(np.argmax(devs))
    return {"intercept": a, "slope": b, "at": points[i], "linearity": float(devs[i]), "u_lin": float(devs[i]) / SQRT3}


def correct_reading(reading: float, beta0: float, beta1: float) -> float:
    """7.1.3.1 step 5, 11.2: the reading corrected with the regression function, x = (y - b0) / b1."""
    if not abs(beta1) > 1e-12:
        raise MsaError("the slope of the regression function is zero")
    return (reading - beta0) / beta1


# ------------------------------------------------------------------ repeatability and reproducibility of the process (7.2, Tables A.4 to A.6, B.2, B.3)

def reproducibility(data, alpha: float = 0.05) -> dict:
    """`data[operator][part][repeat]`, balanced. The operator can be a measuring system (u_GV instead of u_AV). With one operator the model of Table B.3 is used (parts and error only)."""
    try:
        a = np.asarray(data, dtype=float)
    except (ValueError, TypeError):
        raise MsaError("the data must be operators x parts x repetitions, the same number of values everywhere") from None
    if a.ndim != 3 or not np.all(np.isfinite(a)):
        raise MsaError("the data must be operators x parts x repetitions of numbers")
    o, p, r = a.shape
    if p < 3 or r < 2:
        raise MsaError("at least 3 workpieces (5 are required) with 2 repetitions are needed")
    if not 0 < alpha < 0.5:
        raise MsaError("alpha must be between 0 and 0.5")
    notes = []
    n_total = o * p * r
    if p < 5:
        notes.append("few_workpieces")
    if n_total < MIN_STANDARD_VALUES:
        notes.append("few_measurements")
    if o >= 2 and not (o >= 3 and r >= 2 or o >= 2 and r >= 3):
        notes.append("few_repetitions")
    grand = float(a.mean())
    part_m, op_m, cell_m = a.mean(axis=(0, 2)), a.mean(axis=(1, 2)), a.mean(axis=2)
    ss_pv = float(r * o * ((part_m - grand) ** 2).sum())
    ss_evo = float(((a - cell_m[:, :, None]) ** 2).sum())
    if o == 1:  # Table B.3
        df_pv, df_evo = p - 1, p * (r - 1)
        ms_pv, ms_evo = ss_pv / df_pv, ss_evo / df_evo
        if not ms_evo > 0:
            raise MsaError("the repeated measurements are identical: the resolution is too coarse to evaluate the study")
        return {"operators": 1, "parts": p, "repeats": r, "n": n_total, "model": "parts_only", "pooled": False, "alpha": alpha,
                "table": [{"source": "part", "df": df_pv, "ss": ss_pv, "ms": ms_pv, "variance": max((ms_pv - ms_evo) / r, 0.0), "f": ms_pv / ms_evo, "f_crit": float(stats.f.ppf(1 - alpha, df_pv, df_evo))},
                          {"source": "error", "df": df_evo, "ss": ss_evo, "ms": ms_evo, "variance": ms_evo}],
                "u_evo": math.sqrt(ms_evo), "u_av": None, "u_ia": 0.0, "part_sd": math.sqrt(max((ms_pv - ms_evo) / r, 0.0)), "notes": notes}
    ss_av = float(r * p * ((op_m - grand) ** 2).sum())
    ss_ia = float(r * ((cell_m - op_m[:, None] - part_m[None, :] + grand) ** 2).sum())
    df_av, df_pv, df_ia, df_evo = o - 1, p - 1, (o - 1) * (p - 1), o * p * (r - 1)
    ms_av, ms_pv, ms_ia, ms_evo = ss_av / df_av, ss_pv / df_pv, ss_ia / df_ia, ss_evo / df_evo
    if not ms_evo > 0:
        raise MsaError("the repeated measurements are identical: the resolution is too coarse to evaluate the study")
    f_ia = ms_ia / ms_evo
    f_crit_ia = float(stats.f.ppf(1 - alpha, df_ia, df_evo))
    pooled = f_ia < f_crit_ia  # B.2: "if F < F0 repeatability and interaction should be combined to a single component"
    if pooled:
        ms_pool = (ss_ia + ss_evo) / (df_ia + df_evo)
        denom = ms_pool
        sigma_ia, df_error = 0.0, df_ia + df_evo
    else:
        ms_pool = ms_evo
        denom = ms_ia
        sigma_ia, df_error = max((ms_ia - ms_evo) / r, 0.0), df_evo
    sigma_av = max((ms_av - denom) / (p * r), 0.0)
    sigma_pv = max((ms_pv - denom) / (o * r), 0.0)
    table = [{"source": "operator", "df": df_av, "ss": ss_av, "ms": ms_av, "variance": sigma_av, "f": ms_av / denom, "f_crit": float(stats.f.ppf(1 - alpha, df_av, df_ia if not pooled else df_error))},
             {"source": "part", "df": df_pv, "ss": ss_pv, "ms": ms_pv, "variance": sigma_pv, "f": ms_pv / denom, "f_crit": float(stats.f.ppf(1 - alpha, df_pv, df_ia if not pooled else df_error))},
             {"source": "interaction", "df": df_ia, "ss": ss_ia, "ms": ms_ia, "variance": sigma_ia, "f": f_ia, "f_crit": f_crit_ia},
             {"source": "error", "df": df_evo, "ss": ss_evo, "ms": ms_evo, "variance": ms_evo}]
    if pooled:
        table[2]["pooled"] = True
    return {"operators": o, "parts": p, "repeats": r, "n": n_total, "model": "pooled" if pooled else "full", "pooled": bool(pooled), "alpha": alpha, "ms_pool": ms_pool, "df_error": int(df_error),
            "table": table, "u_evo": math.sqrt(ms_pool if pooled else ms_evo), "u_av": math.sqrt(sigma_av), "u_ia": math.sqrt(sigma_ia), "part_sd": math.sqrt(sigma_pv), "notes": notes}


# ------------------------------------------------------------------ combination (Table 9, clause 8) and capability (clause 9)

SYSTEM_KEYS = ("cal", "lin", "bi", "evr", "re", "ms_rest")
PROCESS_KEYS = ("evo", "av", "gv", "stab", "obj", "t", "rest")


def coverage_factor(workpieces: int, operators: int, gauges: int, repeats: int, alpha: float = 0.05, n_measurements: int | None = None) -> dict:
    """8.2: k = 2; with fewer than 30 measurements Student's t with nu = workpieces x operators x gauges x (repetitions - 1)."""
    nu = int(workpieces) * int(operators) * int(gauges) * (int(repeats) - 1)
    n = n_measurements if n_measurements is not None else int(workpieces) * int(operators) * int(gauges) * int(repeats)
    if nu < 1:
        raise MsaError("the degrees of freedom are below 1")
    if n >= MIN_STANDARD_VALUES:
        return {"k": 2.0, "student": False, "nu": nu}
    return {"k": float(stats.t.ppf(1 - alpha / 2, nu)), "student": True, "nu": nu}


def combine(components: Mapping[str, Any], tolerance: float, k: float = 2.0, interactions: Sequence[float] = (), mpe: Sequence[float] | None = None,
            q_ms_max: float = Q_MS_MAX, q_mp_max: float = Q_MP_MAX, c_min: float = C_MIN) -> dict:
    """The standard uncertainties of Table 9 (keys cal, lin, bi, evr, re, ms_rest | evo, av, gv, stab, obj, t, rest; missing ones are 0), `interactions` the u_IAi.
    With `mpe` the MPE route of 5.3 is used for the measuring system: u_MS^2 = u_MPE^2 + u_EV^2 + u_MS-REST^2 (the experimental u_CAL, u_LIN and u_BI are left out), which is our
    reading of 5.3 (the standard does not print this formula)."""
    unknown = set(components) - set(SYSTEM_KEYS) - set(PROCESS_KEYS)
    if unknown:
        raise MsaError(f"unknown component(s) {sorted(unknown)}")
    c = {key: _num(components.get(key, 0.0) or 0.0, f"u_{key.upper()}", nonnegative=True) for key in (*SYSTEM_KEYS, *PROCESS_KEYS)}
    ia = [_num(v, "u_IA", nonnegative=True) for v in interactions]
    tol = _num(tolerance, "the specification interval U - L", positive=True)
    kk = _num(k, "the coverage factor", positive=True)
    if mpe:
        c_mpe = u_mpe(*mpe)
        ev_ms = max(c["evr"], c["re"])
        u_ms2 = c_mpe ** 2 + ev_ms ** 2 + c["ms_rest"] ** 2
        base2 = u_ms2
        route = "mpe"
        parts = {"mpe": c_mpe, "ev": ev_ms, "ms_rest": c["ms_rest"]}
    else:
        ev_ms = max(c["evr"], c["re"])
        u_ms2 = c["cal"] ** 2 + c["lin"] ** 2 + c["bi"] ** 2 + ev_ms ** 2 + c["ms_rest"] ** 2
        base2 = c["cal"] ** 2 + c["lin"] ** 2 + c["bi"] ** 2 + c["ms_rest"] ** 2
        route = "experimental"
        parts = {"cal": c["cal"], "lin": c["lin"], "bi": c["bi"], "ev": ev_ms, "ms_rest": c["ms_rest"]}
    if not u_ms2 > 0:
        raise MsaError("every component of the measuring system is zero: there is no uncertainty to evaluate")
    u_ms = math.sqrt(u_ms2)
    out: dict[str, Any] = {"route": route, "k": kk, "tolerance": tol, "u_ms": u_ms, "U_ms": kk * u_ms, "q_ms": 100.0 * 2.0 * kk * u_ms / tol, "c_ms": 0.3 * tol / (6.0 * u_ms),
                           "system_parts": parts, "q_ms_max": q_ms_max, "q_mp_max": q_mp_max, "c_min": c_min}
    process_given = any(c[key] > 0 for key in PROCESS_KEYS) or bool(ia)
    if process_given:
        ev_mp = max(c["evr"], c["evo"], c["re"])
        u_mp2 = base2 + ev_mp ** 2 + c["av"] ** 2 + c["gv"] ** 2 + c["stab"] ** 2 + c["obj"] ** 2 + c["t"] ** 2 + c["rest"] ** 2 + sum(v * v for v in ia)
        u_mp = math.sqrt(u_mp2)
        out.update(u_mp=u_mp, U_mp=kk * u_mp, q_mp=100.0 * 2.0 * kk * u_mp / tol, c_mp=0.3 * tol / (3.0 * u_mp),
                   process_parts={**{key: c[key] for key in PROCESS_KEYS}, "ev": ev_mp, "ia": [float(v) for v in ia]})
    else:
        out.update(u_mp=None, U_mp=None, q_mp=None, c_mp=None, process_parts=None)
    # 6.1: a component below 10 % of the largest one is unimportant
    values = {**{key: c[key] for key in (*SYSTEM_KEYS, *PROCESS_KEYS)}, **{f"ia{i + 1}": v for i, v in enumerate(ia)}}
    if mpe:
        values = {"mpe": parts["mpe"], **{k_: v for k_, v in values.items() if k_ not in ("cal", "lin", "bi")}}
    largest = max(values.values())
    out["components"] = {key: float(v) for key, v in values.items()}
    out["unimportant"] = sorted(key for key, v in values.items() if 0 < v < UNIMPORTANT_SHARE * largest)
    out["largest"] = max(values, key=values.get)
    ok_ms = out["q_ms"] <= q_ms_max and out["c_ms"] > c_min
    ok_mp = out["q_mp"] is None or (out["q_mp"] <= q_mp_max and out["c_mp"] > c_min)
    out["ms_ok"], out["mp_ok"] = bool(ok_ms), None if out["q_mp"] is None else bool(ok_mp)
    out["verdict"] = "pass" if ok_ms and ok_mp else "fail"
    return out


def resolution_check(resolution: float, tolerance: float | None = None, process_variation: float | None = None) -> dict:
    """5.2: to judge conformity with a bilateral specification the resolution is below 1/20 of the specification interval; to control a process with SPC tools below 1/5 of the process variation."""
    re_ = _num(resolution, "the resolution", positive=True)
    out: dict[str, Any] = {"resolution": re_, "u_re": re_ / SQRT12, "conformity": None, "spc": None}
    if tolerance is not None:
        out["conformity"] = {"limit": tolerance / 20.0, "ok": bool(re_ < tolerance / 20.0)}
    if process_variation is not None:
        out["spc"] = {"limit": process_variation / 5.0, "ok": bool(re_ < process_variation / 5.0)}
    return out


# ------------------------------------------------------------------ production process against measurement process (clause 10, B.4)

def real_capability(c_obs: float, q_mp: float) -> float | None:
    """10.1, B.4: C_p,p = (1 / C_obs^2 - 2.25 Q_MP^2)^(-1/2), Q_MP as a fraction (0.30 for 30 %). None when the observed index is too low for this much measurement uncertainty."""
    c, q = _num(c_obs, "the observed capability index", positive=True), _num(q_mp, "Q_MP", nonnegative=True)
    inner = 1.0 / (c * c) - 2.25 * q * q
    return float(inner ** -0.5) if inner > 1e-12 else None


def real_capability_from_cmp(c_obs: float, c_mp: float) -> float | None:
    """The same with the capability index of the measurement process: Q_MP = 0.4 / C_MP follows from C_MP = 0.3 (U - L) / (3 u_MP) and Q_MP = 4 u_MP / (U - L)."""
    return real_capability(c_obs, 0.4 / _num(c_mp, "C_MP", positive=True))


# ------------------------------------------------------------------ monitoring (clause 11.2)

def linearity_monitor(beta0: float, beta1: float, sigma: float, df: int, references: Sequence[float], readings: Sequence[Sequence[float]], epsilon: float = 0.05) -> dict:
    """11.2: control limits +- (sigma / b1) t(1 - epsilon / 2K; df) on the differences between the true values and the readings transformed with x = (y - b0) / b1, K the number of
    standards that are measured. sigma and df are those of the regression of the study (df = N K - 2). The place of the limits in the standard is garbled in the copy that was
    available; the Bonferroni reading (epsilon / 2K for the K standards) is ours."""
    x = np.asarray(references, dtype=float)
    try:
        y = [np.asarray(r, dtype=float) for r in readings]
    except (TypeError, ValueError):
        raise MsaError("the readings must be numbers") from None
    if x.ndim != 1 or x.size < 2 or len(y) != x.size or any(r.ndim != 1 or r.size == 0 or not np.all(np.isfinite(r)) for r in y):
        raise MsaError("give at least 2 standards with their readings")
    if not (_num(sigma, "sigma", positive=True) and df >= 1 and 0 < epsilon < 0.5 and abs(beta1) > 1e-12):
        raise MsaError("sigma and the slope must not be zero, df at least 1 and epsilon between 0 and 0.5")
    kk = x.size
    half = float(sigma / abs(beta1) * stats.t.ppf(1 - epsilon / (2 * kk), df))
    rows = []
    for ref, vals in zip(x, y):
        diffs = [float((v - beta0) / beta1 - ref) for v in vals]
        rows.append({"reference": float(ref), "differences": diffs, "out": [bool(abs(d) > half) for d in diffs]})
    return {"ucl": half, "lcl": -half, "standards": kk, "rows": rows, "valid": not any(any(r["out"]) for r in rows), "epsilon": epsilon}


# ------------------------------------------------------------------ attribute measurement processes (clause 12)

def trial_class(results: Sequence[int]) -> int:
    """12.2: class 1 all trials 'good', class 2 different results, class 3 all trials 'bad'. results are 1 (good, approved) or 0 (bad)."""
    s = set(int(v) for v in results)
    return 1 if s == {1} else 3 if s == {0} else 2


def bowker(table: Sequence[Sequence[float]], alpha: float = 0.05) -> dict:
    """12.2: Bowker's test of symmetry for a k x k table of the classes of two operators. chi2 = sum over i > j of (n_ij - n_ji)^2 / (n_ij + n_ji), compared with the
    1 - alpha quantile of chi2 with k (k - 1) / 2 degrees of freedom (the pairs with no part at all are left out of the count)."""
    try:
        t = np.asarray(table, dtype=float)
    except (TypeError, ValueError):
        raise MsaError("the table must be square with counts of 0 or more") from None
    if t.ndim != 2 or t.shape[0] != t.shape[1] or t.shape[0] < 2 or np.any(t < 0) or not np.all(np.isfinite(t)):
        raise MsaError("the table must be square with counts of 0 or more")
    k = t.shape[0]
    stat, df = 0.0, 0
    for i in range(k):
        for j in range(i):
            s = t[i, j] + t[j, i]
            if s > 0:
                stat += float((t[i, j] - t[j, i]) ** 2 / s)
                df += 1
    if df == 0:
        raise MsaError("the table has no pair of different classes to compare")
    crit = float(stats.chi2.ppf(1 - alpha, df))
    return {"chi2": float(stat), "df": df, "critical": crit, "p_value": float(stats.chi2.sf(stat, df)), "symmetric": bool(stat <= crit), "alpha": alpha, "table": t.astype(int).tolist()}


def bowker_study(results: Mapping[str, Sequence[Sequence[int]]], alpha: float = 0.05) -> dict:
    """12.2: at least 40 parts, 3 trials, 2 operators. `results[operator][trial][part]` is 1 (good) or 0 (bad). Every pair of operators is tested; the level of the
    pairs is not adjusted for the number of pairs (the standard says so in a note)."""
    names = list(results)
    if len(names) < 2:
        raise MsaError("at least 2 operators are needed")
    arrays = {}
    for name in names:
        a = np.asarray(results[name], dtype=int)
        if a.ndim != 2 or a.shape[0] < 2 or not set(np.unique(a)) <= {0, 1}:
            raise MsaError("every operator needs the decisions of at least 2 trials, 1 for good and 0 for bad")
        arrays[name] = a
    parts = {a.shape[1] for a in arrays.values()}
    trials = {a.shape[0] for a in arrays.values()}
    if len(parts) != 1 or len(trials) != 1:
        raise MsaError("every operator judges the same parts in the same number of trials")
    n_parts, n_trials = parts.pop(), trials.pop()
    notes = []
    if n_parts < 40:
        notes.append("few_parts")
    if n_trials < 3:
        notes.append("few_trials")
    classes = {name: [trial_class(a[:, i]) for i in range(n_parts)] for name, a in arrays.items()}
    pairs = []
    for a_name, b_name in combinations(names, 2):
        table = np.zeros((3, 3), dtype=int)
        for ca, cb in zip(classes[a_name], classes[b_name]):
            table[ca - 1, cb - 1] += 1
        try:
            pairs.append({"a": a_name, "b": b_name, **bowker(table, alpha)})
        except MsaError:
            pairs.append({"a": a_name, "b": b_name, "chi2": 0.0, "df": 0, "critical": None, "p_value": 1.0, "symmetric": True, "alpha": alpha, "table": table.tolist()})
    return {"parts": n_parts, "trials": n_trials, "operators": names, "pairs": pairs, "symmetric": all(p["symmetric"] for p in pairs), "notes": notes, "alpha": alpha}


def uncertainty_range(reference: Sequence[float], results: Mapping[str, Sequence[Sequence[int]]], lsl: float, usl: float) -> dict:
    """12.3: the uncertainty range from reference values (signal detection). `results[operator][trial][part]` is 1 (approved) or 0 (not approved).
    1. sort the parts by reference value, highest first; 2. the last reference value of the top block in which every operator rejected every result;
    3. the first reference value at which every operator approved every result; 4. the last such value; 5. the first reference value after it at which every
    operator rejects every result again; 6. d_UR = (2) - (3); 7. d_LR = (4) - (5); 8. d = (d_UR + d_LR) / 2; 9. U_attr = d / 2, Q_attr = 2 U_attr / (U - L)."""
    x = np.asarray(reference, dtype=float)
    if x.ndim != 1 or x.size < 20 or not np.all(np.isfinite(x)):
        raise MsaError("give the reference value of every part (at least 20, the standard has 50)")
    if not _num(lsl, "the lower specification limit") < _num(usl, "the upper specification limit"):
        raise MsaError("the lower limit must be below the upper limit")
    decisions = []
    for name, trials in results.items():
        a = np.asarray(trials, dtype=int)
        if a.ndim != 2 or a.shape[1] != x.size or not set(np.unique(a)) <= {0, 1}:
            raise MsaError("every operator needs one decision (1 approved, 0 not approved) per part in every trial")
        decisions.append(a)
    if len(decisions) < 2 or any(d.shape[0] < 2 for d in decisions):
        raise MsaError("at least 2 operators with 2 trials each are needed (the standard has 3 operators and 3 trials)")
    allres = np.concatenate(decisions, axis=0)  # every result of every operator on every part
    approve_all = allres.min(axis=0) == 1
    reject_all = allres.max(axis=0) == 0
    order = np.argsort(-x, kind="stable")  # highest first
    ref = x[order]
    ap, rj = approve_all[order], reject_all[order]
    if not ap.any():
        raise MsaError("no part was approved by every operator in every trial: the uncertainty range cannot be found")
    first_ap = int(np.argmax(ap))
    last_ap = int(len(ap) - 1 - np.argmax(ap[::-1]))
    top = 0
    while top < len(rj) and rj[top]:
        top += 1
    if top == 0:
        raise MsaError("the highest reference values were not all rejected: add parts above the upper specification limit")
    low = [i for i in range(last_ap + 1, len(rj)) if rj[i]]
    if not low:
        raise MsaError("no part below the specification interval was rejected by every operator in every trial: add parts below the lower limit")
    ref2, ref3, ref4, ref5 = float(ref[top - 1]), float(ref[first_ap]), float(ref[last_ap]), float(ref[low[0]])
    if first_ap < top:
        raise MsaError("the results are not ordered by the reference values: a part above the zone was approved")
    d_ur, d_lr = ref2 - ref3, ref4 - ref5
    d = (d_ur + d_lr) / 2.0
    u_attr = d / 2.0
    tol = float(usl) - float(lsl)
    q = 100.0 * 2.0 * u_attr / tol
    consistent = []
    for i in range(x.size):
        right = 1 if lsl <= x[i] <= usl else 0
        consistent.append(bool(np.all(allres[:, i] == right)))
    return {"parts": int(x.size), "operators": len(decisions), "trials": int(allres.shape[0] // len(decisions)), "last_rejected_above": ref2, "first_approved": ref3, "last_approved": ref4,
            "first_rejected_below": ref5, "d_ur": d_ur, "d_lr": d_lr, "d": d, "u_attr": u_attr, "q_attr": q, "tolerance": tol, "consistent_parts": int(sum(consistent)),
            "inconsistent_parts": int(x.size - sum(consistent)), "rule_ok": bool(q <= Q_ATTR_RULE)}


def attribute_review(reference: Sequence[float], results: Sequence[Sequence[int]], lsl: float, usl: float, u_mp_max: float | None = None) -> dict:
    """12.4: at least one operator measures at least three workpieces whose reference values lie outside the uncertainty ranges (zone I below, zone III, zone I above:
    clear results are expected). `results[trial][part]` is 1 (approved) or 0. Accepted when every result agrees with the reference; the exact binomial interval of the
    share of agreeing results is given. U_MP,max = Q_MP (U - L) / 2 gives the size of the range."""
    x = np.asarray(reference, dtype=float)
    a = np.asarray(results, dtype=int)
    if x.ndim != 1 or x.size < 3 or a.ndim != 2 or a.shape[1] != x.size or not set(np.unique(a)) <= {0, 1}:
        raise MsaError("give at least 3 workpieces with their reference values and one decision (1 approved, 0 not approved) per part in every trial")
    if not _num(lsl, "the lower specification limit") < _num(usl, "the upper specification limit"):
        raise MsaError("the lower limit must be below the upper limit")
    zone = []
    for v in x:
        if u_mp_max is not None and (abs(v - lsl) < u_mp_max or abs(v - usl) < u_mp_max):
            raise MsaError("a workpiece lies inside the uncertainty range: choose workpieces with clear results")
        zone.append("below" if v < lsl else "above" if v > usl else "inside")
    expected = np.array([1 if lsl <= v <= usl else 0 for v in x])
    agree = (a == expected[None, :])
    k, n = int(agree.sum()), int(agree.size)
    lo = 0.0 if k == 0 else float(stats.beta.ppf(0.025, k, n - k + 1))
    hi = 1.0 if k == n else float(stats.beta.ppf(0.975, k + 1, n - k))
    return {"workpieces": int(x.size), "results": n, "agreeing": k, "accepted": bool(k == n), "agreement_ci": [lo, hi], "zones": zone,
            "has_inside": "inside" in zone, "has_below": "below" in zone, "has_above": "above" in zone}


def u_mp_max(q_mp: float, tolerance: float) -> float:
    """12.4: U_MP,max = Q_MP (U - L) / 2, Q_MP as a fraction."""
    return _num(q_mp, "Q_MP", positive=True) * _num(tolerance, "the specification interval", positive=True) / 2.0

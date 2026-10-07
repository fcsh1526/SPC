"""Process characterization (AIAG-VDA SPC draft 6.4): the relationship between process parameters and product characteristics by regression or by designed experiments,
to find the primary and secondary control factors that the out-of-control action plan adjusts (6.6). The draft names DoE and regression and gives no method; the standard
ones are used here: multiple linear regression by least squares, and the analysis of a full two-level factorial design (effects, analysis of variance, Lenth's method when
there is no replication).

This is a tool for the analysis of data from an experiment or from the process; it does not plan the experiment.
"""

from __future__ import annotations

import math
from itertools import combinations, product
from typing import Mapping, Sequence

import numpy as np
from scipy import stats

MAX_FACTORS_REGRESSION = 15
MAX_FACTORS_FACTORIAL = 5


def _f(x):
    return None if x is None or not math.isfinite(float(x)) else float(x)


def regression(y: Sequence[float], factors: Mapping[str, Sequence[float]], alpha: float = 0.05) -> dict:
    """Multiple linear regression y = b0 + b1 x1 + ... with an intercept. For each factor: the coefficient, its standard error, t, p, the interval, the standardised
    coefficient (the effect of one standard deviation of the factor in standard deviations of y), the partial R-squared and the variance inflation factor (VIF)."""
    yv = np.asarray(y, dtype=float).ravel()
    names = list(factors)
    k = len(names)
    if not 1 <= k <= MAX_FACTORS_REGRESSION:
        raise ValueError(f"give 1 to {MAX_FACTORS_REGRESSION} factors")
    if not 0 < alpha < 0.5:
        raise ValueError("alpha must be between 0 and 0.5")
    cols = []
    for nm in names:
        c = np.asarray(factors[nm], dtype=float).ravel()
        if c.shape != yv.shape:
            raise ValueError(f"factor {nm!r} must have one value per run ({yv.size})")
        cols.append(c)
    n = yv.size
    if not (np.all(np.isfinite(yv)) and all(np.all(np.isfinite(c)) for c in cols)):
        raise ValueError("values must be finite numbers")
    dfe = n - k - 1
    if dfe < 2:
        raise ValueError(f"{n} runs are too few for {k} factors: at least {k + 3} are needed")
    X = np.column_stack([np.ones(n), *cols])
    if np.linalg.matrix_rank(X) < k + 1:
        raise ValueError("a factor is constant or a linear function of the others")
    beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
    resid = yv - X @ beta
    sse = float(resid @ resid)
    sst = float(((yv - yv.mean()) ** 2).sum())
    if not sst > 0:
        raise ValueError("the response has no variation")
    mse = sse / dfe
    cov = mse * np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    t = beta / se
    p = 2 * stats.t.sf(np.abs(t), dfe)
    tq = float(stats.t.isf(alpha / 2, dfe))
    sy = float(yv.std(ddof=1))
    terms = []
    for j, nm in enumerate(names, start=1):
        others = np.column_stack([np.ones(n), *[cols[i] for i in range(k) if i != j - 1]])
        r2j = 0.0
        if k > 1:
            fit = np.linalg.lstsq(others, cols[j - 1], rcond=None)[0]
            res = cols[j - 1] - others @ fit
            tot = float(((cols[j - 1] - cols[j - 1].mean()) ** 2).sum())
            r2j = 1.0 - float(res @ res) / tot if tot > 0 else 0.0
        vif = 1.0 / (1.0 - r2j) if r2j < 1 - 1e-12 else float("inf")
        terms.append({"factor": nm, "coefficient": float(beta[j]), "se": float(se[j]), "t": float(t[j]), "p_value": float(p[j]), "significant": bool(p[j] < alpha),
                      "ci": [float(beta[j] - tq * se[j]), float(beta[j] + tq * se[j])], "standardised": float(beta[j] * cols[j - 1].std(ddof=1) / sy),
                      "partial_r2": float(t[j] ** 2 / (t[j] ** 2 + dfe)), "vif": _f(vif)})
    ranked = sorted(range(k), key=lambda i: -abs(terms[i]["t"]))
    for rank, i in enumerate(ranked, start=1):
        terms[i]["rank"] = rank
    r2 = 1.0 - sse / sst
    f_stat = ((sst - sse) / k) / mse
    warnings = []
    if any(tm["vif"] is None or tm["vif"] > 10 for tm in terms):
        vifs = [tm["vif"] for tm in terms if tm["vif"] is not None]
        warnings.append({"code": "doe_collinear", "vif": max(vifs) if vifs and len(vifs) == len(terms) else None})
    if n < 5 * k:
        warnings.append({"code": "doe_few_runs", "n": n, "k": k})
    return {"n": n, "k": k, "alpha": alpha, "intercept": {"coefficient": float(beta[0]), "se": float(se[0]), "t": float(t[0]), "p_value": float(p[0]),
                                                           "ci": [float(beta[0] - tq * se[0]), float(beta[0] + tq * se[0])]}, "terms": terms,
            "r2": float(r2), "r2_adjusted": float(1.0 - (sse / dfe) / (sst / (n - 1))), "f": float(f_stat), "df_model": k, "df_error": dfe,
            "p_model": float(stats.f.sf(f_stat, k, dfe)), "sigma": float(math.sqrt(mse)), "warnings": warnings}


def _code(levels: Sequence) -> tuple[np.ndarray, list]:
    """The two levels of a factor as -1 (the lower or the first in order) and +1."""
    vals = list(levels)
    try:
        uniq = sorted({float(v) for v in vals})
        key = float
    except (TypeError, ValueError):
        uniq = sorted({str(v) for v in vals})
        key = str
    if len(uniq) != 2:
        raise ValueError(f"a two-level factor has exactly 2 levels (it has {len(uniq)})")
    return np.array([-1.0 if key(v) == uniq[0] else 1.0 for v in vals]), uniq


def factorial(y: Sequence[float], factors: Mapping[str, Sequence], alpha: float = 0.05, max_order: int | None = None) -> dict:
    """Full two-level factorial 2^k with the same number r of runs for each combination (r = 1: unreplicated). Effects are the mean at the high level minus the mean at the low level.
    With replication each effect is tested against the pure error (analysis of variance, F with 1 and N - 2^k degrees of freedom). Without it Lenth's pseudo standard error
    gives the margin of error ME and the simultaneous margin SME (Lenth 1989): effects beyond them are active."""
    yv = np.asarray(y, dtype=float).ravel()
    names = list(factors)
    k = len(names)
    if not 2 <= k <= MAX_FACTORS_FACTORIAL:
        raise ValueError(f"a factorial design here has 2 to {MAX_FACTORS_FACTORIAL} factors")
    if not np.all(np.isfinite(yv)):
        raise ValueError("values must be finite numbers")
    codes, levels = {}, {}
    for nm in names:
        if len(factors[nm]) != yv.size:
            raise ValueError(f"factor {nm!r} must have one level per run ({yv.size})")
        codes[nm], levels[nm] = _code(factors[nm])
    n = yv.size
    cells: dict[tuple, list[int]] = {}
    for i in range(n):
        cells.setdefault(tuple(codes[nm][i] for nm in names), []).append(i)
    if len(cells) != 2 ** k:
        raise ValueError(f"the design is not a full factorial: {len(cells)} of the {2 ** k} combinations are present")
    reps = {len(v) for v in cells.values()}
    if len(reps) != 1:
        raise ValueError("every combination needs the same number of runs")
    r = reps.pop()
    order = k if max_order is None else max_order
    terms = []
    for size in range(1, order + 1):
        for combo in combinations(names, size):
            sign = np.prod([codes[nm] for nm in combo], axis=0)
            contrast = float(sign @ yv)
            effect = contrast / (n / 2.0)
            terms.append({"term": ":".join(combo), "order": size, "effect": effect, "coefficient": effect / 2.0, "ss": contrast ** 2 / n})
    sst = float(((yv - yv.mean()) ** 2).sum())
    out: dict = {"n": n, "k": k, "replicates": r, "levels": levels, "alpha": alpha, "mean": float(yv.mean()), "sst": sst}
    if r >= 2:
        sse = float(sum(((yv[idx] - yv[idx].mean()) ** 2).sum() for idx in cells.values()))
        dfe = n - 2 ** k
        mse = sse / dfe
        if not mse > 0:
            raise ValueError("the replicates do not vary: there is no pure error to test against")
        for tm in terms:
            f_stat = tm["ss"] / mse
            tm.update(f=float(f_stat), p_value=float(stats.f.sf(f_stat, 1, dfe)), contribution=float(tm["ss"] / sst) if sst > 0 else None)
            tm["significant"] = bool(tm["p_value"] < alpha)
        out.update(sse=sse, df_error=dfe, mse=mse, method="anova")
    else:
        eff = np.array([tm["effect"] for tm in terms])
        m = eff.size
        s0 = 1.5 * float(np.median(np.abs(eff)))
        trimmed = np.abs(eff)[np.abs(eff) < 2.5 * s0] if s0 > 0 else np.abs(eff)
        pse = 1.5 * float(np.median(trimmed)) if trimmed.size else 0.0
        if not pse > 0:
            raise ValueError("the effects do not vary enough for Lenth's method (most are exactly 0)")
        d = m / 3.0
        me = float(stats.t.isf(alpha / 2, d) * pse)
        gamma = (1 + (1 - alpha) ** (1.0 / m)) / 2
        sme = float(stats.t.ppf(gamma, d) * pse)
        for tm in terms:
            tm.update(contribution=float(tm["ss"] / sst) if sst > 0 else None, significant=bool(abs(tm["effect"]) > me), strongly_significant=bool(abs(tm["effect"]) > sme))
        out.update(pse=pse, me=me, sme=sme, method="lenth", df_lenth=d, warnings=[{"code": "doe_unreplicated"}])
    out["terms"] = sorted(terms, key=lambda tm: -abs(tm["effect"]))
    out.setdefault("warnings", [])
    return out

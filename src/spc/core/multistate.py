"""Machine performance of a process with several states: ISO 22514-8:2014, with the tests of its Annex B and the example 11 of ISO/TR 11462-3.

The tests are written out here, not called from scipy, so that the validation can compare them with scipy as an independent reference:
* Grubbs (B.1): the most extreme value of a state; repeated after removing an outlier (7.2), not more than one third of the values.
* Bartlett (B.2): equal variances, with the bias correction c, and the variance that is forced up when a sample shows only one or two scale
  marks of the measuring device (table B.2).
* Fisher (B.3): equal means of k states, F = n * s_x^2 / s^2 (one-way analysis of variance). For two states the standard recommends the
  F-test of the variances and the t-test, or the Aspin-Welch test when the variances differ (7.3, 7.4).
`analyse` carries the whole procedure (clause 7): outliers, the widths of the local dispersions, their locations, the type of global
dispersion of table 1 and Pm and Pmk of table 2. Bartlett and Fisher assume normal data in each state. The tests inform; whether the
differences in location are constant or variable over time, and whether an outlier has a physical reality, are decisions of the analyst
and are arguments.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

import numpy as np
from scipy import stats

MIN_STATE_SIZE = 3


def grubbs_critical(n: int, alpha: float = 0.05) -> float:
    """Critical value of the two-sided Grubbs test for n values."""
    if n < 3:
        raise ValueError("the Grubbs test needs at least 3 values")
    t = stats.t.ppf(1 - alpha / (2 * n), n - 2)
    return float((n - 1) / math.sqrt(n) * math.sqrt(t * t / (n - 2 + t * t)))


def grubbs(x: Sequence[float], alpha: float = 0.05) -> dict:
    """The most extreme value of one state. `outlier` when |x − mean| / s exceeds the critical value."""
    a = np.asarray(x, dtype=float)
    n = a.size
    s = a.std(ddof=1)
    if n < 3 or s == 0:
        return {"n": int(n), "g": None, "g_crit": None, "index": None, "value": None, "outlier": False}
    i = int(np.argmax(np.abs(a - a.mean())))
    g = float(abs(a[i] - a.mean()) / s)
    crit = grubbs_critical(n, alpha)
    return {"n": int(n), "g": g, "g_crit": crit, "index": i, "value": float(a[i]), "outlier": bool(g > crit)}


def bartlett(groups: Sequence[Sequence[float]]) -> dict:
    """Bartlett's test of equal variances: statistic, degrees of freedom and p-value."""
    g = [np.asarray(x, dtype=float) for x in groups]
    k = len(g)
    if k < 2 or any(x.size < 2 for x in g):
        raise ValueError("Bartlett needs at least 2 states with 2 values each")
    df = np.array([x.size - 1 for x in g], dtype=float)
    var = np.array([x.var(ddof=1) for x in g])
    if np.any(var == 0):
        raise ValueError("a state without any variation cannot be tested")
    total = df.sum()
    pooled = float((df * var).sum() / total)
    c = 1 + (np.sum(1 / df) - 1 / total) / (3 * (k - 1))
    chi2 = float((total * math.log(pooled) - np.sum(df * np.log(var))) / c)
    return {"statistic": chi2, "df": k - 1, "p": float(stats.chi2.sf(chi2, k - 1))}


def fisher(groups: Sequence[Sequence[float]]) -> dict:
    """Fisher's F test of equal means (one-way analysis of variance)."""
    g = [np.asarray(x, dtype=float) for x in groups]
    k = len(g)
    n = sum(x.size for x in g)
    if k < 2 or n <= k:
        raise ValueError("Fisher needs at least 2 states and more values than states")
    grand = np.concatenate(g).mean()
    ssb = sum(x.size * (x.mean() - grand) ** 2 for x in g)
    ssw = sum(((x - x.mean()) ** 2).sum() for x in g)
    if ssw == 0:
        raise ValueError("no variation within the states")
    f = float((ssb / (k - 1)) / (ssw / (n - k)))
    return {"statistic": f, "df1": k - 1, "df2": n - k, "p": float(stats.f.sf(f, k - 1, n - k))}


def fisher_standard(groups: Sequence[Sequence[float]], n: int | None = None) -> dict:
    """The F statistic as ISO 22514-8 (B.3) writes it: n * s_x^2 / s^2 with s_x^2 the variance of the k state means, s^2 the mean variance, and
    k - 1 and n*k - k degrees of freedom. n is the sample size per state; when a value was taken out as an outlier the sizes differ by one and
    n stays the size of the sampling plan (the example A.3 of the standard computes it so: F = 46,85). With equal sizes this is the analysis of variance."""
    g = [np.asarray(x, dtype=float) for x in groups]
    k = len(g)
    n = n or max(x.size for x in g)
    dof = sum(x.size - 1 for x in g)
    if k < 2 or dof <= 0:
        raise ValueError("Fisher needs at least 2 states and some variation")
    s2 = sum((x.size - 1) * float(np.var(x, ddof=1)) for x in g) / dof
    if s2 == 0:
        raise ValueError("no variation within the states")
    sx2 = float(np.var([x.mean() for x in g], ddof=1))
    f = n * sx2 / s2
    df1, df2 = k - 1, n * k - k
    return {"statistic": float(f), "df1": df1, "df2": df2, "p": float(stats.f.sf(f, df1, df2)), "critical": float(stats.f.ppf(0.95, df1, df2))}


def state_tests(states: Mapping[str, Sequence[float]], alpha: float = 0.05) -> dict:
    """All three tests for the states of a process. States with fewer than 3 values are listed and left out."""
    if not 0 < alpha < 0.5:
        raise ValueError("alpha must be between 0 and 0.5")
    usable = {k: np.asarray(v, dtype=float) for k, v in states.items() if len(v) >= MIN_STATE_SIZE}
    skipped = sorted(k for k in states if k not in usable)
    if len(usable) < 2:
        raise ValueError("at least 2 states with 3 values each are needed")
    per_state = {k: {"n": int(v.size), "mean": float(v.mean()), "sd": float(v.std(ddof=1)), "grubbs": grubbs(v, alpha)} for k, v in usable.items()}
    groups = list(usable.values())
    out = {"alpha": alpha, "states": per_state, "skipped": skipped, "bartlett": bartlett(groups), "fisher": fisher(groups)}
    out["variance_differs"] = out["bartlett"]["p"] < alpha
    out["location_differs"] = out["fisher"]["p"] < alpha
    out["outliers"] = sorted(k for k, s in per_state.items() if s["grubbs"]["outlier"])
    return out


def type1_capability(states: Mapping[str, Sequence[float]], lsl: float, usl: float) -> dict:
    """Machine performance of a multi-state process of Type 1 (equal dispersion in the states, ISO 22514-8): sigma is the pooled standard deviation
    of the states; the lower index uses the lowest state mean, the upper index the highest. Pm = (Pmk,l + Pmk,u) / 2 = (U - L - range of the
    state means) / (6 sigma). The three Pmk values reproduce the example of ISO/TR 11462-3 (data set 11); the text of ISO 22514-8 is not at hand,
    so the formula is the one that reproduces them, not a quotation.
    """
    groups = [np.asarray(v, dtype=float) for v in states.values()]
    if len(groups) < 2 or any(g.size < 2 for g in groups):
        raise ValueError("at least 2 states with 2 values each are needed")
    sigma = float(np.sqrt(np.mean([g.var(ddof=1) for g in groups])))
    means = [float(g.mean()) for g in groups]
    low = (min(means) - lsl) / (3 * sigma)
    up = (usl - max(means)) / (3 * sigma)
    return {"sigma": sigma, "pmk_l": low, "pmk_u": up, "pmk": min(low, up), "pm": (low + up) / 2}


# ---------------------------------------------------------------------------------------------- the rest of the procedure (clause 7, annex B)

# table B.2: the factor d for the variance that is forced up, by the range in scale marks (0, 1, 2) and the sample size
_FORCED = {0: {3: 0.25, 4: 0.19, 5: 0.16, 6: 0.14, 7: 0.13, 8: 0.12, 9: 0.12, 10: 0.11},
           1: {3: 1.0, 4: 0.74, 5: 0.63, 6: 0.56, 7: 0.52, 8: 0.49},
           2: {3: 2.25, 4: 1.67, 5: 1.41}}
_FORCED_OVER_10 = 0.10  # range 0, more than 10 values


def forced_variance(x: Sequence[float], resolution: float) -> float | None:
    """Table B.2: when the range of a sample is 0, 1 or 2 scale marks the calculated variance is too low to be credible; the variance is then
    d * resolution^2 (when that is higher than the calculated one). None when the table has no entry."""
    a = np.asarray(x, dtype=float)
    marks = int(round(float(np.ptp(a)) / resolution))
    row = _FORCED.get(marks)
    if row is None:
        return None
    d = _FORCED_OVER_10 if (marks == 0 and a.size > 10) else row.get(a.size)
    return None if d is None else float(d * resolution ** 2)


def _variances(groups, resolution):
    out = []
    for g in groups:
        v = float(np.var(g, ddof=1))
        if resolution:
            f = forced_variance(g, resolution)
            if f is not None and f > v:
                v = f
        out.append(v)
    return np.array(out)


def bartlett_forced(groups: Sequence[Sequence[float]], resolution: float | None = None, bias_correction: bool = True) -> dict:
    """Bartlett's test (B.2). With `resolution` the variances of samples with a range of at most two scale marks are forced up (table B.2)."""
    g = [np.asarray(x, dtype=float) for x in groups]
    k = len(g)
    if k < 2 or any(x.size < 2 for x in g):
        raise ValueError("Bartlett needs at least 2 states with 2 values each")
    var = _variances(g, resolution)
    if np.any(var <= 0):
        raise ValueError("a state without any variation cannot be tested: give the resolution of the measuring device")
    df = np.array([x.size - 1 for x in g], dtype=float)
    total = df.sum()
    pooled = float((df * var).sum() / total)
    c = 1 + (np.sum(1 / df) - 1 / total) / (3 * (k - 1)) if bias_correction else 1.0
    chi2 = float((total * math.log(pooled) - np.sum(df * np.log(var))) / c)
    return {"statistic": chi2, "df": k - 1, "p": float(stats.chi2.sf(chi2, k - 1)), "critical": float(stats.chi2.ppf(0.95, k - 1)), "pooled_variance": pooled,
            "correction": float(c), "forced": [bool(v != float(np.var(x, ddof=1))) for v, x in zip(var, g)]}


def f_test_variances(a: Sequence[float], b: Sequence[float], alpha: float = 0.05) -> dict:
    """Two states: s1^2 / s2^2 against the F distribution (B.3), two-sided."""
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if x.size < 2 or y.size < 2:
        raise ValueError("at least 2 values in each state")
    f = float(x.var(ddof=1) / y.var(ddof=1))
    d1, d2 = x.size - 1, y.size - 1
    p = float(2 * min(stats.f.sf(f, d1, d2), stats.f.cdf(f, d1, d2)))
    return {"statistic": f, "df1": d1, "df2": d2, "p": min(p, 1.0), "critical_low": float(stats.f.ppf(alpha / 2, d1, d2)), "critical_high": float(stats.f.ppf(1 - alpha / 2, d1, d2))}


def t_test(a: Sequence[float], b: Sequence[float], equal_variances: bool = True) -> dict:
    """Two states, the means: the t-test with pooled variance, or the Aspin-Welch test when the variances differ (7.4)."""
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    n1, n2, v1, v2 = x.size, y.size, x.var(ddof=1), y.var(ddof=1)
    if equal_variances:
        df = n1 + n2 - 2
        sp = ((n1 - 1) * v1 + (n2 - 1) * v2) / df
        t = float((x.mean() - y.mean()) / math.sqrt(sp * (1 / n1 + 1 / n2)))
    else:
        se = v1 / n1 + v2 / n2
        t = float((x.mean() - y.mean()) / math.sqrt(se))
        df = float(se ** 2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)))
    return {"statistic": t, "df": df, "p": float(2 * stats.t.sf(abs(t), df)), "welch": not equal_variances}


def screen_outliers(states: Mapping[str, Sequence[float]], alpha: float = 0.05) -> dict:
    """7.2: Grubbs in each state and in the whole set; a value that is an outlier is taken out and the test is repeated until none is left,
    not more than one third of the values. delta_a is the value minus the mean of the other values of its state (the amplitude of the outlier)."""
    work = {k: list(map(float, v)) for k, v in states.items()}
    total = sum(len(v) for v in work.values())
    removed: list[dict] = []
    first = {k: grubbs(v, alpha) for k, v in work.items()}
    first_all = grubbs([x for v in work.values() for x in v], alpha)
    while True:
        hit = None
        for k, v in work.items():
            g = grubbs(v, alpha)
            if g["outlier"]:
                hit = (k, g)
                break
        if hit is None:
            allv = [x for v in work.values() for x in v]
            ga = grubbs(allv, alpha)
            if ga["outlier"]:
                for k, v in work.items():
                    if ga["value"] in v:
                        hit = (k, {**ga, "index": v.index(ga["value"])})
                        break
        if hit is None:
            break
        if len(removed) + 1 > total / 3:
            raise ValueError("more than one third of the values would be removed as outliers: the data cannot be used")
        k, g = hit
        v = work[k]
        value = v[g["index"]]
        rest = v[: g["index"]] + v[g["index"] + 1:]
        removed.append({"state": k, "value": value, "g": g["g"], "critical": g["g_crit"], "delta_a": float(value - np.mean(rest))})
        work[k] = rest
    return {"states": work, "removed": removed, "first": {"states": first, "all": first_all}}


def analyse(states: Mapping[str, Sequence[float]], lsl: float, usl: float, *, alpha: float = 0.05, resolution: float | None = None,
            location: str = "mean", widths_equal: bool | None = None, locations_equal: bool | None = None, delta_m_variable: bool = False,
            delta_m_star: float | None = None, outlier_physical: bool = False, outlier_direction: str = "both") -> dict:
    """The procedure of ISO 22514-8, clause 7, for states of a normal characteristic.

    location: "mean" or "median" (7.4: the median by default, the mean for a symmetric distribution; the examples of the standard use the mean).
    widths_equal, locations_equal: None lets the tests of 7.3 and 7.4 decide; True or False is the decision of the analyst.
    delta_m_variable: the difference in location is expected to vary over time (types 2 and 5). delta_m_star: the largest difference to expect
    when the locations vary independently of each other; the observed difference when not given.
    outlier_physical: an outlier is real: it is left out of widths and locations but its amplitude is added to the width of the dispersion (7.5);
    outlier_direction: "negative" (to D_il only), "positive" (to D_iu only) or "both".
    """
    if location not in ("mean", "median"):
        raise ValueError("location must be mean or median")
    if not lsl < usl:
        raise ValueError("lsl must be below usl")
    screened = screen_outliers(states, alpha)
    removed = screened["removed"]
    data = {k: np.asarray(v, dtype=float) for k, v in screened["states"].items()}
    if len(data) < 2 or any(v.size < 2 for v in data.values()):
        raise ValueError("at least 2 states with 2 values each are needed")
    names = list(data)
    groups = [data[k] for k in names]
    k = len(groups)
    # 7.3 the widths
    if k == 2:
        wtest = f_test_variances(groups[0], groups[1], alpha)
        wtest["accepted"] = wtest["critical_low"] <= wtest["statistic"] <= wtest["critical_high"]
        wtest["name"] = "fisher"
    else:
        wtest = bartlett_forced(groups, resolution)
        wtest["accepted"] = wtest["statistic"] <= wtest["critical"]
        wtest["name"] = "bartlett"
    equal_w = wtest["accepted"] if widths_equal is None else widths_equal
    # 7.4 the locations
    centres = {n: float(np.mean(v) if location == "mean" else np.median(v)) for n, v in data.items()}
    if k == 2:
        ltest = t_test(groups[0], groups[1], equal_variances=bool(equal_w))
        ltest["accepted"] = ltest["p"] >= alpha
        ltest["name"] = "welch" if ltest["welch"] else "t"
    else:
        n_plan = max(len(v) for v in states.values())
        ltest = {**fisher_standard(groups, n_plan)}
        ltest["critical"] = float(stats.f.ppf(1 - alpha, ltest["df1"], ltest["df2"]))
        ltest["accepted"] = ltest["statistic"] <= ltest["critical"]
        ltest["name"] = "fisher"
    equal_l = ltest["accepted"] if locations_equal is None else locations_equal
    dm = max(centres.values()) - min(centres.values())
    # the local intrinsic dispersions (normal distribution: x50 -/+ 3 s)
    dof = sum(v.size - 1 for v in groups)
    pooled = math.sqrt(sum((v.size - 1) * float(np.var(v, ddof=1)) for v in groups) / dof)
    add_l = add_u = 0.0
    if outlier_physical and removed:
        da = max(abs(r["delta_a"]) for r in removed)
        if outlier_direction in ("negative", "both"):
            add_l = da
        if outlier_direction in ("positive", "both"):
            add_u = da
    table = {}
    for n, v in data.items():
        s = float(np.std(v, ddof=1))
        sd = pooled if equal_w else s
        table[n] = {"n": int(v.size), "mean": float(np.mean(v)), "median": float(np.median(v)), "s": s, "x50": centres[n], "x0135": centres[n] - 3 * sd, "x99865": centres[n] + 3 * sd,
                    "d_l": 3 * sd + add_l, "d_u": 3 * sd + add_u}
    T = usl - lsl
    xs = [r["x50"] for r in table.values()]
    dl = [r["d_l"] for r in table.values()]
    du = [r["d_u"] for r in table.values()]
    star = dm if delta_m_star is None else delta_m_star
    if equal_w and equal_l:
        ptype = 0  # one dispersion for all states: ISO 22514-3
        x50 = float(np.mean(np.concatenate(groups))) if location == "mean" else float(np.median(np.concatenate(groups)))
        pm = T / (dl[0] + du[0])
        pml, pmu = (x50 - lsl) / dl[0], (usl - x50) / du[0]
    elif equal_w:
        ptype = 2 if delta_m_variable else 1
        pm = (T - dm) / (dl[0] + du[0]) if ptype == 1 else T / (dl[0] + du[0] + star)
        pml, pmu = (min(xs) - lsl) / dl[0], (usl - max(xs)) / du[0]
    elif equal_l:
        ptype = 3
        x50 = float(np.mean(np.concatenate(groups))) if location == "mean" else float(np.median(np.concatenate(groups)))
        pm = T / max(a + b for a, b in zip(dl, du))
        pml, pmu = (x50 - lsl) / max(dl), (usl - x50) / max(du)
    elif not delta_m_variable:
        ptype = 4
        # the state with the smallest lower bound (el) and the one with the highest upper bound (er)
        el = min(table.values(), key=lambda r: r["x0135"])
        er = max(table.values(), key=lambda r: r["x99865"])
        pm = (T - dm) / (el["d_l"] + er["d_u"])
        pml, pmu = (min(xs) - lsl) / max(dl), (usl - max(xs)) / max(du)
    else:
        ptype = 5
        pm = T / (max(dl) + max(du) + star)
        pml = min((r["x50"] - lsl) / r["d_l"] for r in table.values())
        pmu = min((usl - r["x50"]) / r["d_u"] for r in table.values())
    return {"type": ptype, "alpha": alpha, "location": location, "states": table, "outliers": removed, "screening": {k_: v for k_, v in screened["first"].items()},
            "widths": {**wtest, "equal": bool(equal_w)}, "locations": {**ltest, "equal": bool(equal_l)}, "delta_m": float(dm), "delta_m_star": float(star),
            "sigma_pooled": pooled, "dof": int(dof), "pm": float(pm), "pmk_l": float(pml), "pmk_u": float(pmu), "pmk": float(min(pml, pmu))}

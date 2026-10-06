"""Tests for a process with several states (ISO 22514-8, the example 11 of ISO/TR 11462-3): outliers by Grubbs, equal variances by Bartlett,
equal locations by Fisher (one-way analysis of variance).

The three tests are written out here, not called from scipy, so that the validation can compare them with scipy as an independent reference.
Bartlett and Fisher assume normal data within a state; Bartlett is sensitive to skewed data. The result says what the tests found and
leaves the decision (analyse each state alone, or the states together) to the person.
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

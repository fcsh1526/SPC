"""A control chart for processes with a trend that cannot be removed, such as tool wear (draft 7.2 time model C3, 8.2.1 "1.5 dressing cycles", 10.3.5).

The draft names the problem and gives no formula. The standard method is the regression control chart: the centre line is the least squares line of the plotted
statistic (subgroup mean, or the value itself) against the position, and the limits lie u residual standard deviations around it. With a known cycle (for example a dressing cycle
of `cycle` samples) the position is counted inside the cycle, so one line serves every cycle (a saw tooth) and the chart restarts after each dressing.

The cycles can also be given as positions (how far each point is from the start of its cycle, for cycles that people mark when the tool is changed, not at a fixed length), with the cycle of each
point. A one-way analysis of variance of the residuals by cycle tells whether the cycles start at different levels (then one line does not serve all of them).

The criteria are applied to the residuals about the line: points beyond the limits, runs on one side of the line, trends of the residuals. The slope is tested
(t test with k - 2 degrees of freedom). The residual standard deviation includes whatever random variation is left after the trend; it is compared with the variation within the subgroups.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats

from spc.core.constants import ALPHA_3SIGMA
from spc.core.rules import RuleSet, evaluate

MIN_POINTS = 8


@dataclass(frozen=True)
class TrendChart:
    position: np.ndarray  # position inside the cycle
    values: np.ndarray
    center: np.ndarray
    lcl: np.ndarray
    ucl: np.ndarray
    intercept: float
    slope: float
    sigma: float
    r2: float
    slope_p: float
    slope_ci: tuple[float, float]
    df: int
    violations: tuple
    cycle: int | None
    n_cycles: int = 1
    cycle_level_p: float | None = None  # p of the analysis of variance of the residuals by cycle: small = the cycles start at different levels


def fit(values, cycle: int | None = None, alpha: float = ALPHA_3SIGMA, rules: RuleSet | None = None, confidence: float = 0.95,
        positions=None, groups=None) -> TrendChart:
    """`positions` (distance of each point from the start of its cycle) and `groups` (the cycle of each point) replace the fixed `cycle` length."""
    y = np.asarray(values, dtype=float).ravel()
    k = y.size
    if k < MIN_POINTS or not np.all(np.isfinite(y)):
        raise ValueError(f"need at least {MIN_POINTS} finite points")
    if positions is not None:
        t = np.asarray(positions, dtype=float).ravel()
        if t.size != k or not np.all(np.isfinite(t)) or np.any(t < 0):
            raise ValueError("positions: one non-negative number for each point")
        cycle = None
    elif cycle is not None and not (isinstance(cycle, int) and 3 <= cycle <= k):
        raise ValueError("the cycle must be a whole number of at least 3 samples and at most the number of samples")
    if positions is None:
        t = (np.arange(k) % cycle if cycle else np.arange(k)).astype(float)
    if np.ptp(t) == 0:
        raise ValueError("all points have the same position")
    tm, ym = t.mean(), y.mean()
    sxx = float(np.sum((t - tm) ** 2))
    b = float(np.sum((t - tm) * (y - ym)) / sxx)
    a = float(ym - b * tm)
    fitted = a + b * t
    res = y - fitted
    df = k - 2
    sse = float(np.sum(res ** 2))
    sigma = math.sqrt(sse / df)
    if not sigma > 0:
        raise ValueError("the points lie exactly on a line: there is no residual variation")
    sst = float(np.sum((y - ym) ** 2))
    r2 = 1.0 - sse / sst if sst > 0 else 0.0
    se_b = sigma / math.sqrt(sxx)
    p = float(2 * stats.t.sf(abs(b / se_b), df))
    tq = float(stats.t.isf((1 - confidence) / 2, df))
    u = float(stats.norm.isf(alpha / 2))
    z = res / sigma
    rs = rules or RuleSet(beyond_limits=True, run_length=7, trend_length=7)
    viol = evaluate(z, 0.0, -u, u, rs, sigma=1.0).violations
    n_cycles, level_p = 1, None
    if groups is not None:
        g = np.asarray(groups).ravel()
        if g.size != k:
            raise ValueError("groups: one cycle number for each point")
        labels = list(dict.fromkeys(g.tolist()))
        n_cycles = len(labels)
        parts = [res[g == lab] for lab in labels]
        usable = [x for x in parts if x.size >= 2]
        if len(usable) >= 2 and len(usable) == len(parts) and k - len(labels) >= 1:
            level_p = float(stats.f_oneway(*parts).pvalue)
            level_p = level_p if math.isfinite(level_p) else None
    elif cycle:
        n_cycles = int(math.ceil(k / cycle))
    return TrendChart(t, y, fitted, fitted - u * sigma, fitted + u * sigma, a, b, sigma, r2, p, (b - tq * se_b, b + tq * se_b), df, tuple(viol), cycle, n_cycles, level_p)

"""Shewhart charts for continuous characteristics: X̄-s, X̄-R, I-MR.

Limits are written with an explicit risk alpha (AIAG-VDA SPC draft, 10.3.3):

* location chart  : mu_hat ± u(1-alpha/2) * sigma_hat / sqrt(n)
* s chart         : sqrt(chi2(f; p) / f) * sigma_hat, f = n - 1
* R chart         : w(n; p) * sigma_hat
* alpha is the two-sided risk. The default is the 3-sigma risk (non-intervention 99.73 %).

Read the variation chart first. Only a stable variation chart allows reading the location chart.
Spec limits never appear on these charts.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import chi2

from spc.core.constants import ALPHA_3SIGMA, check_alpha, cn, d2, u_quantile, w_quantile


@dataclass(frozen=True)
class Limits:
    lcl: float
    center: float
    ucl: float

    def alarms(self, values) -> np.ndarray:
        v = np.asarray(values, dtype=float)
        return (v > self.ucl) | (v < self.lcl)


@dataclass(frozen=True)
class SubgroupChart:
    """Location chart and variation chart with the estimators that produced them."""

    kind: str
    n: int
    k: int
    alpha: float
    mu_hat: float
    sigma_hat: float
    location: Limits
    variation: Limits
    location_values: np.ndarray
    variation_values: np.ndarray


def _subgroups(data) -> np.ndarray:
    x = np.asarray(data, dtype=float)
    if x.ndim != 2:
        raise ValueError("subgroup data must be a 2-D array with shape (k subgroups, n values)")
    k, n = x.shape
    if n < 2:
        raise ValueError("subgroups need at least 2 values; use imr() for individual values")
    if k < 2:
        raise ValueError("at least 2 subgroups are needed")
    if not np.all(np.isfinite(x)):
        raise ValueError("data contains NaN or inf; mark outliers as invalid and exclude them first")
    return x


def xbar_s(data, alpha: float = ALPHA_3SIGMA) -> SubgroupChart:
    """X̄-s chart. Estimators: mu_hat = grand mean, sigma_hat = sqrt(mean of subgroup variances)."""
    check_alpha(alpha)
    x = _subgroups(data)
    k, n = x.shape
    means = x.mean(axis=1)
    s = x.std(axis=1, ddof=1)
    mu_hat = float(means.mean())
    sigma_hat = float(np.sqrt(np.mean(s**2)))
    u = u_quantile(alpha)
    half = u * sigma_hat / np.sqrt(n)
    f = n - 1
    s_ucl = float(np.sqrt(chi2.ppf(1.0 - alpha / 2.0, f) / f) * sigma_hat)
    s_lcl = float(np.sqrt(chi2.ppf(alpha / 2.0, f) / f) * sigma_hat)
    return SubgroupChart(
        kind="xbar-s",
        n=n,
        k=k,
        alpha=alpha,
        mu_hat=mu_hat,
        sigma_hat=sigma_hat,
        location=Limits(mu_hat - half, mu_hat, mu_hat + half),
        variation=Limits(s_lcl, float(s.mean()), s_ucl),
        location_values=means,
        variation_values=s,
    )


def xbar_r(data, alpha: float = ALPHA_3SIGMA) -> SubgroupChart:
    """X̄-R chart. Estimators: mu_hat = grand mean, sigma_hat = R̄ / d2. R limits use the w distribution."""
    check_alpha(alpha)
    x = _subgroups(data)
    k, n = x.shape
    means = x.mean(axis=1)
    ranges = x.max(axis=1) - x.min(axis=1)
    rbar = float(ranges.mean())
    mu_hat = float(means.mean())
    sigma_hat = rbar / d2(n)
    half = u_quantile(alpha) * sigma_hat / np.sqrt(n)
    return SubgroupChart(
        kind="xbar-r",
        n=n,
        k=k,
        alpha=alpha,
        mu_hat=mu_hat,
        sigma_hat=sigma_hat,
        location=Limits(mu_hat - half, mu_hat, mu_hat + half),
        variation=Limits(
            w_quantile(n, alpha / 2.0) * sigma_hat,
            rbar,
            w_quantile(n, 1.0 - alpha / 2.0) * sigma_hat,
        ),
        location_values=means,
        variation_values=ranges,
    )


def median_r(data, alpha: float = ALPHA_3SIGMA) -> SubgroupChart:
    """Median and range chart (draft 10.3.3.4). mu_hat = mean of the subgroup medians (the choice of ISO 7870-2),
    sigma_hat = R̄ / d2. The location limits are mu_hat ± u(1-alpha/2) * c_n * sigma_hat / sqrt(n); the R chart is the same
    as for X̄-R. The median reacts more slowly to a changing process than the mean, and is less sensitive to a single extreme value."""
    check_alpha(alpha)
    x = _subgroups(data)
    k, n = x.shape
    medians = np.median(x, axis=1)
    ranges = x.max(axis=1) - x.min(axis=1)
    rbar = float(ranges.mean())
    mu_hat = float(medians.mean())
    sigma_hat = rbar / d2(n)
    half = u_quantile(alpha) * cn(n) * sigma_hat / np.sqrt(n)
    return SubgroupChart(
        kind="median-r", n=n, k=k, alpha=alpha, mu_hat=mu_hat, sigma_hat=sigma_hat,
        location=Limits(mu_hat - half, mu_hat, mu_hat + half),
        variation=Limits(w_quantile(n, alpha / 2.0) * sigma_hat, rbar, w_quantile(n, 1.0 - alpha / 2.0) * sigma_hat),
        location_values=medians, variation_values=ranges,
    )


def imr(values, alpha: float = ALPHA_3SIGMA) -> SubgroupChart:
    """I-MR chart with moving range of 2.

    Estimators: mu_hat = mean, sigma_hat = MR̄ / d2(2). The MR limits use the w distribution for n = 2.
    For restarts and moving samples larger than 2 see `imr_moving`.
    """
    check_alpha(alpha)
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or x.size < 3:
        raise ValueError("values must be a 1-D array with at least 3 observations")
    if not np.all(np.isfinite(x)):
        raise ValueError("data contains NaN or inf; mark outliers as invalid and exclude them first")
    mr = np.abs(np.diff(x))
    mrbar = float(mr.mean())
    mu_hat = float(x.mean())
    sigma_hat = mrbar / d2(2)
    half = u_quantile(alpha) * sigma_hat
    return SubgroupChart(
        kind="imr",
        n=1,
        k=int(x.size),
        alpha=alpha,
        mu_hat=mu_hat,
        sigma_hat=sigma_hat,
        location=Limits(mu_hat - half, mu_hat, mu_hat + half),
        variation=Limits(
            w_quantile(2, alpha / 2.0) * sigma_hat,
            mrbar,
            w_quantile(2, 1.0 - alpha / 2.0) * sigma_hat,
        ),
        location_values=x,
        variation_values=mr,
    )


MAX_MOVING_N = 10


@dataclass(frozen=True)
class MovingChart(SubgroupChart):
    """I-MR with a moving sample size n >= 1, restarts and phases (draft 10.3.3.5). The limits are per point.

    With more than one phase `location.center`, `variation.center`, `mu_hat` per phase are arrays with one
    value per point; `mu_hat` and `sigma_hat` then describe all values together and are not used for limits.
    """

    moving_n: int = 1
    segment_starts: tuple[int, ...] = (0,)  # index of the first value of every segment (a restart begins one)
    phase_starts: tuple[int, ...] = (0,)  # index of the first value of every phase (a restart with new limits)
    phase_mu: tuple[float, ...] = ()
    phase_sigma: tuple[float, ...] = ()
    location_window: np.ndarray = None  # size of the moving sample behind each location point
    variation_window: np.ndarray = None  # size of the moving range behind each variation point
    variation_end: np.ndarray = None  # index of the last value of each moving range
    location_sigma: np.ndarray = None  # sigma_hat / sqrt(t) of each location point, for sigma-based criteria


def imr_moving(values, alpha: float = ALPHA_3SIGMA, moving_n: int = 1, restarts=(), phase_starts=()) -> MovingChart:
    """Individuals chart with moving characteristics that restart (AIAG-VDA SPC draft 10.3.3.5).

    After a planned intervention (tool change) or a violation the moving characteristic must not use
    values from before it: its causal observation would raise more alarms although the process is
    corrected. `restarts` holds indices into `values` where a new segment begins. A moving range never
    spans a restart.

    `phase_starts` (a subset of `restarts`) also start a new phase: the process was changed on purpose,
    so the centre line and the limits are calculated again from the values of that phase alone (draft 7.2:
    limits are calculated again after an improvement). Every phase needs at least one complete moving sample.

    Every segment starts again with a moving sample of size 1 and grows by one value per point up to
    `moving_n`, so there is no blind spot of n - 1 points. The limits follow the sample size of each point:

    * location : moving mean of t values,  mu_hat ± u(1-alpha/2) * sigma_hat / sqrt(t)
    * variation: moving range of t values (t = 2 .. max(moving_n, 2)),  w(t; p) * sigma_hat

    mu_hat is the mean of the values of the phase. sigma_hat = R̄ / d2(t_max), where R̄ is the mean of the
    ranges of complete moving samples of the phase only. For moving_n = 1 or 2 that is MR̄ / d2(2), as in `imr`.
    The risk alpha holds for every plotted point on its own (normal data, independent values). Moving
    means share values with their neighbours, so their alarms come in clusters.

    The draft prints the location limit with the quantile u of (1 - alpha/2) replaced by an expression with
    the n-th root of (1 - alpha). Measured on the draft's figure 10-11 (half widths 1 : 0.68 : 0.575 for n = 1, 2, 3),
    sigma_hat / sqrt(t) with a constant u (1 : 0.71 : 0.58) fits and the n-th root version (1 : 0.76 : 0.64) does not.
    """
    check_alpha(alpha)
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or x.size < 3:
        raise ValueError("values must be a 1-D array with at least 3 observations")
    if not np.all(np.isfinite(x)):
        raise ValueError("data contains NaN or inf; mark outliers as invalid and exclude them first")
    m = int(moving_n)
    if not 1 <= m <= MAX_MOVING_N:
        raise ValueError(f"moving_n must be between 1 and {MAX_MOVING_N}")
    mv = max(m, 2)
    cuts = sorted({int(r) for r in restarts})
    if any(not 0 < r < x.size for r in cuts):
        raise ValueError("restart indices must be between 1 and the number of values - 1")
    new_phase = {int(r) for r in phase_starts}
    if not new_phase <= set(cuts):
        raise ValueError("a phase can only start at a restart")
    starts = [0] + cuts
    stops = starts[1:] + [x.size]

    t_loc = np.empty(x.size, dtype=int)
    loc = np.empty(x.size)
    pid = np.empty(x.size, dtype=int)  # phase of each value
    ranges: list[float] = []
    t_var: list[int] = []
    ends: list[int] = []
    phase = 0
    for a, b in zip(starts, stops):
        if a in new_phase:
            phase += 1
        for i in range(a, b):
            j = i - a
            t = min(j + 1, m)
            t_loc[i], pid[i] = t, phase
            loc[i] = x[i - t + 1 : i + 1].mean()
            if j >= 1:
                tv = min(j + 1, mv)
                window = x[i - tv + 1 : i + 1]
                ranges.append(float(window.max() - window.min()))
                t_var.append(tv)
                ends.append(i)
    rng = np.array(ranges)
    tv_arr = np.array(t_var, dtype=int)
    end_arr = np.array(ends, dtype=int)
    var_pid = pid[end_arr]
    n_phases = phase + 1
    mus, rbars, sigmas = [], [], []
    for k in range(n_phases):
        full = (var_pid == k) & (tv_arr == mv)
        if not full.any():
            raise ValueError(
                f"no complete moving sample in phase {k + 1}: every segment of it is shorter than the moving sample size"
                if n_phases > 1 else
                "no complete moving sample: every segment is shorter than the moving sample size"
            )
        rbars.append(float(rng[full].mean()))
        sigmas.append(rbars[-1] / d2(mv))
        mus.append(float(x[pid == k].mean()))
    mu_a, sig_a, rbar_a = np.array(mus)[pid], np.array(sigmas)[pid], np.array(rbars)
    u = u_quantile(alpha)
    half = u * sig_a / np.sqrt(t_loc)
    low = {t: w_quantile(t, alpha / 2.0) for t in set(t_var)}
    high = {t: w_quantile(t, 1.0 - alpha / 2.0) for t in set(t_var)}
    sig_v = np.array(sigmas)[var_pid]
    one = n_phases == 1
    return MovingChart(
        kind="imr", n=m, k=int(x.size), alpha=alpha,
        mu_hat=mus[0] if one else float(x.mean()), sigma_hat=sigmas[0] if one else float(np.mean(sigmas)),
        location=Limits(mu_a - half, mus[0] if one else mu_a, mu_a + half),
        variation=Limits(np.array([low[t] for t in t_var]) * sig_v, rbars[0] if one else rbar_a[var_pid],
                         np.array([high[t] for t in t_var]) * sig_v),
        location_values=loc, variation_values=rng,
        moving_n=m, segment_starts=tuple(starts),
        phase_starts=tuple(a for a in starts if a == 0 or a in new_phase),
        phase_mu=tuple(mus), phase_sigma=tuple(sigmas),
        location_window=t_loc, variation_window=tv_arr, variation_end=end_arr, location_sigma=sig_a / np.sqrt(t_loc),
    )

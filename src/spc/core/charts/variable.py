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

from spc.core.constants import ALPHA_3SIGMA, check_alpha, d2, u_quantile, w_quantile


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


def imr(values, alpha: float = ALPHA_3SIGMA) -> SubgroupChart:
    """I-MR chart with moving range of 2.

    Estimators: mu_hat = mean, sigma_hat = MR̄ / d2(2). The MR limits use the w distribution for n = 2.
    The start-up handling with growing moving-sample size (draft 10.3.3.5) is not implemented yet.
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

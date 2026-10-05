"""Shewhart chart with extended limits and Pearson chart (draft 10.3.5.3 and 10.3.5.2).

Extended limits (processes whose mean moves in a way that is expected: tool wear, several machines):
    UCL = mu + u_in sigma_in / sqrt(n) + u_out sigma_out,   LCL = mu - u_in sigma_in / sqrt(n) - u_out sigma_out
  sigma_in: within the subgroup (root of the mean variance); sigma_out: between the subgroups, from the analysis of variance,
  s_A^2 = max(0, variance of the subgroup means - sigma_in^2 / n). u_out = 1.5 is the frequently used empirical value.
  The other estimate of the draft uses the mean of the 3 largest and of the 3 smallest subgroup means as mu_max and mu_min:
    UCL = mu_max(3) + u_in sigma_in / sqrt(n),   LCL = mu_min(3) - u_in sigma_in / sqrt(n).

Pearson chart (skewed distribution): CL = mean of the plotted values, UCL and LCL are the 99.865 % and 0.135 % quantiles
  (Clements: mean + s * quantile of the standardised Pearson curve of the skewness and kurtosis of the plotted values).
  The draft also allows any suitable fitted unimodal distribution; `method='fitted'` takes the quantiles of the
  best fitting one of the families of the analysis.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

from spc.core import distributions as dist
from spc.core.constants import ALPHA_3SIGMA, u_quantile
from spc.core.pearson import Pearson, PearsonError

MIN_PLOTTED = 30
U_OUT_DEFAULT = 1.5


def within_between(matrix) -> tuple[float, float, float, np.ndarray]:
    """(mean of means, sigma_in, sigma_out, subgroup means) of reference subgroups (k, n)."""
    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2 or m.shape[0] < 2 or m.shape[1] < 2 or not np.all(np.isfinite(m)):
        raise ValueError("the reference needs subgroups of at least 2 values")
    n = m.shape[1]
    means = m.mean(axis=1)
    s2_in = float(m.var(axis=1, ddof=1).mean())
    s2_between = float(means.var(ddof=1)) - s2_in / n
    return float(means.mean()), math.sqrt(s2_in), math.sqrt(max(0.0, s2_between)), means


def extended_limits(n: int, alpha: float, mu: float, sigma_in: float, sigma_out: float, u_out: float = U_OUT_DEFAULT,
                    mu_max: float | None = None, mu_min: float | None = None) -> dict[str, float]:
    if not sigma_in > 0 or sigma_out < 0:
        raise ValueError("sigma_in must be positive and sigma_out must not be negative")
    if not 0 < u_out <= 4.0:
        raise ValueError("u_out must be between 0 and 4 (the draft names 1.5 as the usual value)")
    u_in = u_quantile(alpha)
    half_in = u_in * sigma_in / math.sqrt(n)
    if mu_max is not None and mu_min is not None:
        return {"lcl": mu_min - half_in, "cl": mu, "ucl": mu_max + half_in, "u_in": u_in}
    return {"lcl": mu - half_in - u_out * sigma_out, "cl": mu, "ucl": mu + half_in + u_out * sigma_out, "u_in": u_in}


def extremes(means, k: int = 3) -> tuple[float, float]:
    """(mu_max, mu_min): the mean of the 3 largest and of the 3 smallest subgroup means."""
    x = np.sort(np.asarray(means, dtype=float))
    return float(x[-k:].mean()), float(x[:k].mean())


def moments(values) -> tuple[float, float, float, float]:
    """(mean, standard deviation, skewness, kurtosis) of the plotted values, with the usual small sample corrections."""
    x = np.asarray(values, dtype=float).ravel()
    if x.size < MIN_PLOTTED or not np.all(np.isfinite(x)):
        raise ValueError(f"the reference needs at least {MIN_PLOTTED} plotted values to estimate skewness and kurtosis")
    s = float(x.std(ddof=1))
    if not s > 0:
        raise ValueError("the reference values do not vary")
    return float(x.mean()), s, float(stats.skew(x, bias=False)), float(stats.kurtosis(x, fisher=False, bias=False))


def pearson_limits(mean: float, s: float, g1: float, b2: float) -> dict:
    """Limits of a Pearson chart from the four moments of the plotted values."""
    if not s > 0:
        raise ValueError("the standard deviation must be positive")
    try:
        p = Pearson(g1, b2)
        lo, hi = (float(v) for v in p.ppf([0.00135, 0.99865]))
    except PearsonError as exc:
        raise ValueError(str(exc)) from None
    return {"lcl": mean + s * lo, "cl": mean, "ucl": mean + s * hi, "type": p.type}


def fitted_limits(values, family: str | None = None) -> dict:
    """The draft's first formula: the quantiles of a fitted distribution (best of the analysis families, or the one asked for)."""
    x = np.asarray(values, dtype=float).ravel()
    if x.size < MIN_PLOTTED:
        raise ValueError(f"the reference needs at least {MIN_PLOTTED} plotted values to fit a distribution")
    cands = dist.fit_candidates(x, families=(family,) if family else ("normal", "lognormal", "weibull", "gamma", "johnson_su", "box_cox"))
    try:
        best = dist.choose_automatically(cands)
    except dist.FitError as exc:
        raise ValueError(str(exc)) from None
    lo, _med, hi = dist.quantiles(best.dist)
    return {"lcl": float(lo), "cl": float(x.mean()), "ucl": float(hi), "family": best.family}

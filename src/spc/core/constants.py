"""Constants for subgroup statistics, computed exactly instead of read from tables.

The range of n normal observations divided by sigma (the relative range w) follows the
studentized range distribution with infinite degrees of freedom. d2 and d3 are its mean and
standard deviation. Quantiles of w give the exact R-chart limits (AIAG-VDA SPC draft, 10.3.3.3).
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from scipy import integrate, special
from scipy.stats import norm, studentized_range

# Two-sided risk of a 3-sigma chart (non-intervention probability 99.73 %).
ALPHA_3SIGMA: float = 2.0 * norm.cdf(-3.0)


def _check_n(n: int) -> int:
    if int(n) != n or n < 2:
        raise ValueError(f"subgroup size must be an integer >= 2, got {n!r}")
    return int(n)


@lru_cache(maxsize=None)
def d2(n: int) -> float:
    """Expected value of the relative range R/sigma for subgroup size n."""
    n = _check_n(n)
    value, _ = integrate.quad(lambda w: 1.0 - studentized_range.cdf(w, n, np.inf), 0.0, 30.0)
    return float(value)


@lru_cache(maxsize=None)
def d3(n: int) -> float:
    """Standard deviation of the relative range R/sigma for subgroup size n."""
    n = _check_n(n)
    second_moment, _ = integrate.quad(
        lambda w: 2.0 * w * (1.0 - studentized_range.cdf(w, n, np.inf)), 0.0, 30.0
    )
    return math.sqrt(second_moment - d2(n) ** 2)


def c4(n: int) -> float:
    """Expected value of s/sigma for subgroup size n."""
    n = _check_n(n)
    return math.sqrt(2.0 / (n - 1)) * math.exp(special.gammaln(n / 2.0) - special.gammaln((n - 1) / 2.0))


def w_quantile(n: int, probability: float) -> float:
    """Quantile of the relative range R/sigma (w distribution) for subgroup size n."""
    n = _check_n(n)
    if not 0.0 < probability < 1.0:
        raise ValueError("probability must be strictly between 0 and 1")
    return float(studentized_range.ppf(probability, n, np.inf))


def u_quantile(alpha: float) -> float:
    """Standard normal quantile u(1 - alpha/2) for a two-sided risk alpha."""
    check_alpha(alpha)
    return float(norm.ppf(1.0 - alpha / 2.0))


def check_alpha(alpha: float) -> float:
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be strictly between 0 and 1, got {alpha!r}")
    return alpha

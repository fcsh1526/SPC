"""Attribute charts p, np, c, u with exact binomial / Poisson limits (AIAG-VDA SPC draft, 10.3.6).

Limits are solved from the cumulative distribution, not from the normal approximation.
With count limits l = ppf(alpha/2) and h = ppf(1 - alpha/2):

* a point alarms when its count is below l or above h
* P(X > h) <= alpha/2 and P(X < l) < alpha/2
* when l is 0 the lower limit cannot alarm, so no improvement signal is possible there

Attribute charts need some nonconformities to work. They do not fit a zero-defect strategy.
Charts need a subgroup size above 50. A size change below 25 % needs no recalculation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import binom, poisson

from spc.core.constants import ALPHA_3SIGMA, check_alpha
from spc.core.notes import Note

MIN_SUBGROUP_SIZE = 50  # "should be above 50" (draft 10.3.6.2)


@dataclass(frozen=True)
class AttributeChart:
    kind: str
    alpha: float
    center: float | np.ndarray
    lcl: np.ndarray
    ucl: np.ndarray
    values: np.ndarray
    sizes: np.ndarray
    warnings: tuple[Note, ...] = ()

    def alarms(self) -> np.ndarray:
        return (self.values > self.ucl) | (self.values < self.lcl)


def exact_limits(kind: str, center: float, size: float, alpha: float = ALPHA_3SIGMA) -> tuple[float, float]:
    """(lcl, ucl) of one sample of `size`, in the unit that the chart plots, from the binomial or Poisson distribution.

    center: p-bar for "p" and "np", the mean count per unit for "c" (size is ignored: the unit is constant),
    u-bar for "u". A sample alarms when its value is below lcl or above ucl.
    """
    check_alpha(alpha)
    lo, hi = alpha / 2.0, 1.0 - alpha / 2.0
    if kind == "p":
        return float(binom.ppf(lo, size, center) / size), float(binom.ppf(hi, size, center) / size)
    if kind == "np":
        return float(binom.ppf(lo, size, center)), float(binom.ppf(hi, size, center))
    if kind == "c":
        return float(poisson.ppf(lo, center)), float(poisson.ppf(hi, center))
    if kind == "u":
        return float(poisson.ppf(lo, center * size) / size), float(poisson.ppf(hi, center * size) / size)
    raise ValueError(f"unknown attribute chart {kind!r}")


def _counts(x, name: str) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if a.ndim != 1 or a.size < 1:
        raise ValueError(f"{name} must be a non-empty 1-D array")
    if np.any(a < 0) or np.any(a != np.floor(a)):
        raise ValueError(f"{name} must be non-negative integers")
    return a


def _sizes(n, k: int) -> np.ndarray:
    a = np.broadcast_to(np.asarray(n, dtype=float), (k,)).copy()
    if np.any(a <= 0):
        raise ValueError("sample sizes must be positive")
    return a


def _warnings(sizes: np.ndarray, constant_required: bool) -> tuple[Note, ...]:
    out: list[Note] = []
    if np.any(sizes <= MIN_SUBGROUP_SIZE):
        out.append(Note("small_sample", {"limit": MIN_SUBGROUP_SIZE}))
    spread = (sizes.max() - sizes.min()) / sizes.mean()
    if constant_required and spread > 0:
        raise ValueError("this chart needs a constant sample size; use the proportion / per-unit chart instead")
    if spread >= 0.25:
        out.append(Note("size_varies"))
    return tuple(out)


def p_chart(defectives, n, alpha: float = ALPHA_3SIGMA) -> AttributeChart:
    """Proportion of nonconforming units. Sample size may vary."""
    check_alpha(alpha)
    x = _counts(defectives, "defectives")
    sizes = _sizes(n, x.size)
    if np.any(x > sizes):
        raise ValueError("defectives cannot exceed the sample size")
    p_bar = float(x.sum() / sizes.sum())
    lcl = binom.ppf(alpha / 2.0, sizes, p_bar) / sizes
    ucl = binom.ppf(1.0 - alpha / 2.0, sizes, p_bar) / sizes
    return AttributeChart("p", alpha, p_bar, lcl, ucl, x / sizes, sizes, _warnings(sizes, False))


def np_chart(defectives, n: int, alpha: float = ALPHA_3SIGMA) -> AttributeChart:
    """Number of nonconforming units. Sample size must be constant."""
    check_alpha(alpha)
    x = _counts(defectives, "defectives")
    sizes = _sizes(n, x.size)
    warnings = _warnings(sizes, True)
    if np.any(x > sizes):
        raise ValueError("defectives cannot exceed the sample size")
    p_bar = float(x.sum() / sizes.sum())
    size = float(sizes[0])
    lcl = np.full(x.size, binom.ppf(alpha / 2.0, size, p_bar))
    ucl = np.full(x.size, binom.ppf(1.0 - alpha / 2.0, size, p_bar))
    return AttributeChart("np", alpha, size * p_bar, lcl, ucl, x, sizes, warnings)


def c_chart(counts, alpha: float = ALPHA_3SIGMA) -> AttributeChart:
    """Number of nonconformities per inspection unit. The unit must be constant."""
    check_alpha(alpha)
    x = _counts(counts, "counts")
    mu = float(x.mean())
    lcl = np.full(x.size, poisson.ppf(alpha / 2.0, mu))
    ucl = np.full(x.size, poisson.ppf(1.0 - alpha / 2.0, mu))
    return AttributeChart("c", alpha, mu, lcl, ucl, x, np.ones(x.size))


def u_chart(counts, units, alpha: float = ALPHA_3SIGMA) -> AttributeChart:
    """Nonconformities per unit. The number of units may vary."""
    check_alpha(alpha)
    x = _counts(counts, "counts")
    sizes = _sizes(units, x.size)
    u_bar = float(x.sum() / sizes.sum())
    mean = u_bar * sizes
    lcl = poisson.ppf(alpha / 2.0, mean) / sizes
    ucl = poisson.ppf(1.0 - alpha / 2.0, mean) / sizes
    return AttributeChart("u", alpha, u_bar, lcl, ucl, x / sizes, sizes, _warnings(sizes, False))

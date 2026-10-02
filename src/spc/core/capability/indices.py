"""Capability and performance indices (AIAG-VDA SPC draft, 7.8; ISO 22514 series).

Pm/Pmk, Pp/Ppk and Cp/Cpk are calculated in the same way. The name only states the boundary
conditions of the study. This module returns "p" and "pk" and leaves the naming to
`spc.core.capability.naming`.

Methods:

* overall_indices   : normal distribution, total standard deviation s
* within_indices    : Cw/Cwk, within-subgroup sigma. For diagnosis. Not for reports.
* geometric_*       : General Geometric method (.G), quantiles 0.135 %, 50 %, 99.865 %
* zscore_indices    : exceedance proportion method (.Z)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import chi2, norm

from spc.core.constants import c4, d2

P_LOW = 0.00135
P_MID = 0.5
P_HIGH = 0.99865


@dataclass(frozen=True)
class CapabilityIndices:
    """p is the two-sided potential index (None for one-sided specs), pk the location index."""

    p: float | None
    pk: float
    pu: float | None
    pl: float | None
    location: float
    spread: float  # standard deviation, or the 99.73 % spread for quantile methods
    n: int | None = None
    method: str = ""


def _limits(lsl, usl) -> tuple[float | None, float | None]:
    if lsl is None and usl is None:
        raise ValueError("at least one specification limit is needed")
    if lsl is not None and usl is not None and not lsl < usl:
        raise ValueError("lsl must be below usl")
    return lsl, usl


def _from_sigma(mean: float, sigma: float, lsl, usl, n, method: str) -> CapabilityIndices:
    if sigma <= 0:
        raise ValueError("standard deviation must be positive")
    pu = (usl - mean) / (3.0 * sigma) if usl is not None else None
    pl = (mean - lsl) / (3.0 * sigma) if lsl is not None else None
    p = (usl - lsl) / (6.0 * sigma) if (usl is not None and lsl is not None) else None
    pk = min(v for v in (pu, pl) if v is not None)
    return CapabilityIndices(p, pk, pu, pl, mean, sigma, n, method)


def overall_indices(values, lsl=None, usl=None) -> CapabilityIndices:
    """Normal distribution. Uses the total sample standard deviation, ddof = 1.

    One-sided specs give only the location index. For a one-sided spec with a natural limit,
    compute p separately by giving both limits and do not set a target for it.
    """
    _limits(lsl, usl)
    x = np.asarray(values, dtype=float).ravel()
    if x.size < 2 or not np.all(np.isfinite(x)):
        raise ValueError("need at least 2 finite values")
    return _from_sigma(float(x.mean()), float(x.std(ddof=1)), lsl, usl, int(x.size), "normal, total s")


def within_indices(subgroups, lsl=None, usl=None, estimator: str = "sqrt_mean_var") -> CapabilityIndices:
    """Cw/Cwk. The within-subgroup sigma comes from the chosen estimator.

    estimator "sqrt_mean_var" : sqrt of the mean subgroup variance (any subgroup size)
    estimator "rbar"          : R̄ / d2 (n < 10)
    estimator "sbar"          : s̄ / c4
    This is the old AIAG 2nd edition "Cpk". Use it for analysis only.
    """
    _limits(lsl, usl)
    x = np.asarray(subgroups, dtype=float)
    if x.ndim != 2 or x.shape[1] < 2:
        raise ValueError("subgroups must be a 2-D array with at least 2 values per subgroup")
    n = x.shape[1]
    if estimator == "sqrt_mean_var":
        sigma = float(np.sqrt(np.mean(x.var(axis=1, ddof=1))))
    elif estimator == "rbar":
        if n >= 10:
            raise ValueError("the range estimator is meant for subgroup sizes below 10")
        sigma = float(np.mean(x.max(axis=1) - x.min(axis=1)) / d2(n))
    elif estimator == "sbar":
        sigma = float(np.mean(x.std(axis=1, ddof=1)) / c4(n))
    else:
        raise ValueError(f"unknown estimator {estimator!r}")
    return _from_sigma(float(x.mean()), sigma, lsl, usl, int(x.size), f"within, {estimator}")


def geometric_from_quantiles(lsl, usl, x_low: float, x_mid: float, x_high: float) -> CapabilityIndices:
    """General Geometric method from the 0.135 %, 50 % and 99.865 % quantiles (index suffix .G)."""
    _limits(lsl, usl)
    if not x_low < x_mid < x_high:
        raise ValueError("quantiles must satisfy x_low < x_mid < x_high")
    spread = x_high - x_low
    pu = (usl - x_mid) / (x_high - x_mid) if usl is not None else None
    pl = (x_mid - lsl) / (x_mid - x_low) if lsl is not None else None
    p = (usl - lsl) / spread if (usl is not None and lsl is not None) else None
    pk = min(v for v in (pu, pl) if v is not None)
    return CapabilityIndices(p, pk, pu, pl, x_mid, spread, None, "geometric (.G)")


def geometric_indices(dist, lsl=None, usl=None) -> CapabilityIndices:
    """.G indices for a fitted distribution with a `ppf` method (for example a scipy frozen dist)."""
    return geometric_from_quantiles(lsl, usl, float(dist.ppf(P_LOW)), float(dist.ppf(P_MID)), float(dist.ppf(P_HIGH)))


def zscore_indices(dist, lsl=None, usl=None) -> CapabilityIndices:
    """Exceedance proportion (z-Score / Bothe) method, index suffix .Z.

    p_L and p_U are integrated from the fitted distribution and converted to z values.
    Ppk.Z = min(z_L, z_U) / 3 and Pp.Z = (z_L + z_U) / 6 with both z measured inward.
    """
    _limits(lsl, usl)
    # Both values are positive sigma distances to the limits: z_l = -z_L, z_u = z_U.
    z_l = float(-norm.ppf(dist.cdf(lsl))) if lsl is not None else None
    z_u = float(norm.ppf(dist.cdf(usl))) if usl is not None else None
    pl = z_l / 3.0 if z_l is not None else None
    pu = z_u / 3.0 if z_u is not None else None
    p = (z_l + z_u) / 6.0 if (z_l is not None and z_u is not None) else None
    pk = min(v for v in (pu, pl) if v is not None)
    mid = float(dist.ppf(P_MID))
    return CapabilityIndices(p, pk, pu, pl, mid, float(dist.ppf(P_HIGH) - dist.ppf(P_LOW)), None, "z-score (.Z)")


def ppm_out_of_spec(dist, lsl=None, usl=None) -> float:
    """Expected parts per million outside the specification for a fitted distribution."""
    _limits(lsl, usl)
    p = 0.0
    if lsl is not None:
        p += float(dist.cdf(lsl))
    if usl is not None:
        p += float(dist.sf(usl))
    return p * 1e6


def cp_confidence_interval(cp: float, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Approximate interval for a normal Cp: Cp * sqrt(chi2 / nu), nu = n - 1.

    The exact method is in ISO 22514-4. This is the common chi-square form.
    """
    nu = n - 1
    a = 1.0 - confidence
    return (
        cp * float(np.sqrt(chi2.ppf(a / 2.0, nu) / nu)),
        cp * float(np.sqrt(chi2.ppf(1.0 - a / 2.0, nu) / nu)),
    )


def cpk_confidence_interval(cpk: float, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Approximate interval for a normal Cpk: Cpk ± u * sqrt(1/(9n) + Cpk^2 / (2(n-1)))."""
    u = float(norm.ppf(1.0 - (1.0 - confidence) / 2.0))
    half = u * float(np.sqrt(1.0 / (9.0 * n) + cpk**2 / (2.0 * (n - 1))))
    return cpk - half, cpk + half

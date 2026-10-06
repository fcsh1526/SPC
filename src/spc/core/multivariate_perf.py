"""Machine performance of multidimensional characteristics (AIAG-VDA SPC draft 8.5.2, ISO 22514-6): Pm and Pmk from hyper-ellipsoids.

The draft: a multivariate normal model is fitted (mean mu, covariance S). The tolerance of d characteristics with limits L_i, U_i is a hyper-ellipsoid with the
tolerance centre m_i = (L_i + U_i) / 2 and semi-axes a_i = (U_i - L_i) / 2. The random variation range is a hyper-ellipsoid of equal density
(x - mu)' S^-1 (x - mu) <= c^2. Its probability is p = P(chi-square with d degrees of freedom <= c^2).

    Pmk: the range whose border touches the tolerance hyper-ellipsoid and is otherwise entirely inside it (centre inside the tolerance:
         case 1, Pmk = u_p / 3) or the smallest one that reaches it from outside (centre outside: case 2, Pmk = -u_p / 3).
    Pm : the same with the centre of the range moved to the tolerance centre.

The draft says u_p is the "p-quantile of the standardised univariate normal distribution" compared with the 99.865 % quantile (= 3), which stands for the two-sided
boundary of the range with probability 99.73 %. We read this as the two-sided quantile u_p = Phi^-1((1 + p) / 2); then d = 1 gives exactly the univariate
Pmk = min(U - mu, mu - L) / (3 sigma) and Pm = (U - L) / (6 sigma), and p = 99.73 % gives an index of 1. ISO 22514-6 itself is not available to us: this reading is
checked against the one-dimensional case and by brute force, not against the standard.

The distance c is the smallest Mahalanobis distance from mu to the border of the tolerance ellipsoid: a quadratic function on a sphere
(trust region problem), solved exactly with the secular equation.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats
from scipy.optimize import brentq


def _quadratic_on_sphere(B: np.ndarray, g: np.ndarray) -> float:
    """min over |y| = 1 of y'By + 2 g'y, B symmetric positive definite (global minimum, hard case included)."""
    beta, V = np.linalg.eigh(B)
    gt = V.T @ g
    lo = -beta[0]
    scale = max(1.0, float(np.max(np.abs(beta))), float(np.linalg.norm(g)))
    eps = 1e-12 * scale

    def norm2(lam: float) -> float:
        return float(np.sum(gt ** 2 / (beta + lam) ** 2))

    tied = beta - beta[0] <= 1e-9 * scale  # eigenvalues equal to the smallest one: any direction among them is as good
    if np.all(np.abs(gt[tied]) <= eps * max(1.0, np.linalg.norm(gt))):  # the hard case may apply: g has no part along them
        free = ~tied
        rest = float(np.sum(gt[free] ** 2 / (beta[free] - beta[0]) ** 2))
        if rest <= 1.0:
            y = np.zeros_like(beta)
            y[free] = -gt[free] / (beta[free] - beta[0])
            y[np.flatnonzero(tied)[0]] = math.sqrt(max(0.0, 1.0 - rest))
            return float(y @ (beta * y) + 2 * gt @ y)
    a = lo + eps
    while norm2(a) < 1.0:  # |y(lambda)| falls from infinity (at -beta_min) to 0
        a = lo + (a - lo) / 2.0
        if a - lo < 1e-300:
            break
    b = max(a + 1.0, 1.0)
    while norm2(b) > 1.0:
        b = a + 2 * (b - a)
    lam = brentq(lambda t: norm2(t) - 1.0, a, b, xtol=1e-14, rtol=1e-14, maxiter=500)
    y = -gt / (beta + lam)
    return float(y @ (beta * y) + 2 * gt @ y)


def distance_to_tolerance(mean, cov, lower, upper) -> tuple[float, bool]:
    """(c, inside): the smallest Mahalanobis distance from `mean` to the border of the tolerance hyper-ellipsoid, and whether `mean` lies inside it."""
    mean, lower, upper = (np.asarray(v, dtype=float).ravel() for v in (mean, lower, upper))
    cov = np.asarray(cov, dtype=float)
    m, a = (lower + upper) / 2.0, (upper - lower) / 2.0
    inside = bool(np.sum(((mean - m) / a) ** 2) <= 1.0)
    return _distance(mean, cov, m, a), inside


def _distance(mean: np.ndarray, cov: np.ndarray, m: np.ndarray, a: np.ndarray) -> float:
    Sinv = np.linalg.inv(cov)
    A = np.diag(a)
    B = A @ Sinv @ A
    B = (B + B.T) / 2.0
    delta = m - mean
    g = A @ Sinv @ delta
    c2 = float(delta @ Sinv @ delta) + _quadratic_on_sphere(B, g)
    return math.sqrt(max(c2, 0.0))


def _u_of(c: float, d: int) -> tuple[float, float]:
    """(p, u_p): the probability of the range with Mahalanobis radius c, and its two-sided normal quantile (from the tail, so 1e-12 is not lost)."""
    q = float(stats.chi2.sf(c * c, d))
    u = float(stats.norm.isf(min(q, 1.0) / 2.0)) if q > 0 else 37.0
    return 1.0 - q, min(u, 37.0)


def performance(data, lower, upper, names: list[str] | None = None) -> dict:
    """Pm and Pmk of the rows of `data` (n x d) against the two-sided limits of each characteristic."""
    x = np.asarray(data, dtype=float)
    if x.ndim != 2:
        raise ValueError("data must be a table with one row per part and one column per characteristic")
    n, d = x.shape
    lower, upper = np.asarray(lower, dtype=float).ravel(), np.asarray(upper, dtype=float).ravel()
    if lower.size != d or upper.size != d:
        raise ValueError(f"give a lower and an upper limit for each of the {d} characteristics")
    if not np.all(lower < upper):
        raise ValueError("each lower limit must be below its upper limit")
    if not np.all(np.isfinite(x)):
        raise ValueError("values contain NaN or inf")
    if d < 2:
        raise ValueError("a multivariate characteristic has at least 2 dimensions")
    if n < d + 2 or n < 10:
        raise ValueError(f"need at least {max(10, d + 2)} parts for {d} characteristics")
    names = names or [f"x{i + 1}" for i in range(d)]
    if len(names) != d:
        raise ValueError("one name per characteristic")
    mean = x.mean(axis=0)
    cov = np.cov(x, rowvar=False, ddof=1)
    if np.linalg.matrix_rank(cov) < d or np.linalg.cond(cov) > 1e12:
        raise ValueError("the covariance matrix is singular: a characteristic is constant or a linear function of the others")
    m, a = (lower + upper) / 2.0, (upper - lower) / 2.0
    c_k, inside = distance_to_tolerance(mean, cov, lower, upper)
    p_k, u_k = _u_of(c_k, d)
    pmk = (u_k if inside else -u_k) / 3.0
    c_m = _distance(m, cov, m, a)
    p_m, u_m = _u_of(c_m, d)
    sd = np.sqrt(np.diag(cov))
    corr = cov / np.outer(sd, sd)
    out = {
        "n": int(n), "d": int(d), "names": names, "lower": lower.tolist(), "upper": upper.tolist(),
        "mean": mean.tolist(), "sd": sd.tolist(), "covariance": cov.tolist(), "correlation": corr.tolist(),
        "centre_inside": inside, "case": 1 if inside else 2,
        "pmk": float(pmk), "pm": float(u_m / 3.0), "c_pmk": float(c_k), "p_pmk": float(p_k), "c_pm": float(c_m), "p_pm": float(p_m),
        "expected_outside": float(_expected_outside(mean, cov, m, a)) if d <= 4 else None,
        "univariate": [{"name": nm, "mean": float(mean[i]), "sd": float(sd[i]),
                        "pm": float((upper[i] - lower[i]) / (6 * sd[i])), "pmk": float(min(upper[i] - mean[i], mean[i] - lower[i]) / (3 * sd[i]))}
                       for i, nm in enumerate(names)],
        "normality": mardia(x),
    }
    return out


def _expected_outside(mean, cov, m, a, n: int = 400_000, seed: int = 1) -> float:
    """Share of the fitted normal outside the tolerance ellipsoid (Monte Carlo, fixed seed), for information next to the indices."""
    rng = np.random.default_rng(seed)
    z = rng.multivariate_normal(mean, cov, size=n)
    return float(np.mean(np.sum(((z - m) / a) ** 2, axis=1) > 1.0))


def mardia(x: np.ndarray) -> dict:
    """Mardia's tests of multivariate skewness and kurtosis, as a description of the fit. They are not a pass/fail test of the draft."""
    n, d = x.shape
    xc = x - x.mean(axis=0)
    S = xc.T @ xc / n
    D = xc @ np.linalg.inv(S) @ xc.T
    b1 = float(np.sum(D ** 3) / n ** 2)
    b2 = float(np.sum(np.diag(D) ** 2) / n)
    skew_stat = n * b1 / 6.0
    df = d * (d + 1) * (d + 2) / 6.0
    p_skew = float(stats.chi2.sf(skew_stat, df))
    z_kurt = (b2 - d * (d + 2)) / math.sqrt(8.0 * d * (d + 2) / n)
    return {"skewness": b1, "skewness_p": p_skew, "kurtosis": b2, "kurtosis_z": float(z_kurt), "kurtosis_p": float(2 * stats.norm.sf(abs(z_kurt)))}

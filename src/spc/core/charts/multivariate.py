"""Multivariate charts for several related characteristics (draft 10.3.2.8): Hotelling's T2 and MEWMA.

The draft names the charts and gives no formulas, so the standard ones are used (Hotelling 1947; Lowry et al. 1992).

* T2 of a sample of m observations with mean xbar: T2 = m (xbar - mu)' S^-1 (xbar - mu).
  Known mu and S: T2 ~ chi-square with p degrees of freedom, UCL = chi2(1 - alpha; p).
  Estimated from N reference observations: T2 = (xbar - xbar0)' S^-1 (xbar - xbar0) / (1/m + 1/N), UCL = p (N-1)/(N-p) F(1 - alpha; p, N-p).
  There is no lower limit: only a large T2 signals.
* MEWMA: Z_i = lambda x_i + (1 - lambda) Z_(i-1), Z_0 = 0 (x_i centred), Q_i = Z_i' [lambda/(2-lambda) (1 - (1-lambda)^(2i)) S/m]^-1 Z_i.
  Signal when Q_i > h. h is found by simulating the run length for the in-control ARL asked for (no closed form exists).
* MCUSUM (Crosier 1988): C_i = sqrt((S_(i-1) + x_i - mu)' S^-1 (S_(i-1) + x_i - mu)),
  S_i = (S_(i-1) + x_i - mu)(1 - k / C_i) when C_i > k, else 0; signal when Y_i = sqrt(S_i' S^-1 S_i) > h. k = 0.5 is usual.
  h by simulating the run length, like the MEWMA. Crosier's value for p = 2, k = 0.5, ARL 200 is h = 5.5.
* A signal says that the *combination* of the characteristics is unusual. The share of each characteristic is shown by
  d_j = T2 - T2 without characteristic j (Runger, Alt and Montgomery 1996).
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from scipy.stats import chi2, f as f_dist

MIN_P, MAX_P = 2, 10


def check_covariance(cov) -> np.ndarray:
    c = np.asarray(cov, dtype=float)
    if c.ndim != 2 or c.shape[0] != c.shape[1] or not MIN_P <= c.shape[0] <= MAX_P:
        raise ValueError(f"the covariance matrix must be square, with {MIN_P} to {MAX_P} characteristics")
    if not np.all(np.isfinite(c)) or not np.allclose(c, c.T, rtol=1e-8, atol=1e-12 * max(1.0, float(np.max(np.abs(c))))):
        raise ValueError("the covariance matrix must be symmetric and contain finite numbers")
    try:
        np.linalg.cholesky((c + c.T) / 2.0)
    except np.linalg.LinAlgError:
        raise ValueError("the covariance matrix must be positive definite: no characteristic may be a copy or a mix of the others") from None
    return (c + c.T) / 2.0


def estimate(rows) -> tuple[np.ndarray, np.ndarray, int]:
    """(mean, covariance, N) of reference observations (N, p)."""
    x = np.asarray(rows, dtype=float)
    if x.ndim != 2 or not np.all(np.isfinite(x)):
        raise ValueError("the reference needs rows of finite numbers, one value per characteristic")
    n, p = x.shape
    if not MIN_P <= p <= MAX_P:
        raise ValueError(f"{MIN_P} to {MAX_P} characteristics are possible")
    if n < max(20, 3 * p):
        raise ValueError(f"the reference needs at least {max(20, 3 * p)} observations for {p} characteristics")
    return x.mean(axis=0), check_covariance(np.cov(x, rowvar=False)), n


def hotelling_ucl(p: int, alpha: float, n_ref: int | None) -> float:
    if n_ref is None:
        return float(chi2.isf(alpha, p))
    return float(p * (n_ref - 1) / (n_ref - p) * f_dist.isf(alpha, p, n_ref - p))


def hotelling_center(p: int, n_ref: int | None) -> float:
    """Expected T2 (the centre line)."""
    return float(p) if n_ref is None else float(p * (n_ref - 1) / (n_ref - p - 2))


def _scale(m: int, n_ref: int | None) -> float:
    return float(m) if n_ref is None else 1.0 / (1.0 / m + 1.0 / n_ref)


def t2_value(xbar, mu, cov, m: int, n_ref: int | None) -> float:
    d = np.asarray(xbar, dtype=float) - np.asarray(mu, dtype=float)
    return float(_scale(m, n_ref) * d @ np.linalg.solve(cov, d))


def contributions(xbar, mu, cov, m: int, n_ref: int | None) -> list[float]:
    """d_j = T2 - T2 of the other characteristics only."""
    full = t2_value(xbar, mu, cov, m, n_ref)
    out = []
    x, mu = np.asarray(xbar, dtype=float), np.asarray(mu, dtype=float)
    for j in range(x.size):
        keep = [i for i in range(x.size) if i != j]
        out.append(float(full - t2_value(x[keep], mu[keep], cov[np.ix_(keep, keep)], m, n_ref)))
    return out


def mewma_q(xbars, mu, cov, m: int, lam: float) -> float:
    """Q after the sample means `xbars` (oldest first) of one run, started at the target."""
    mu = np.asarray(mu, dtype=float)
    z = np.zeros(mu.size)
    for i, xb in enumerate(xbars, start=1):
        z = lam * (np.asarray(xb, dtype=float) - mu) + (1.0 - lam) * z
    i = len(xbars)
    var = lam / (2.0 - lam) * (1.0 - (1.0 - lam) ** (2 * i))
    return float(z @ np.linalg.solve(cov / m, z) / var)


def _mean_run_length(p: int, lam: float, h: float, runs: int, seed: int, cap: int, exact: bool = True) -> float:
    """Simulated ARL of the MEWMA on standardised data (the in-control run length depends on p and lambda only).
    exact: the covariance of Z at the i-th sample (narrower at the start); else the steady state one that the published tables use."""
    rng = np.random.default_rng(seed)
    z = np.zeros((runs, p))
    alive = np.arange(runs)
    length = np.full(runs, float(cap))
    c = lam / (2.0 - lam)
    for t in range(1, cap + 1):
        e = rng.standard_normal((alive.size, p))
        z[alive] = lam * e + (1.0 - lam) * z[alive]
        q = np.einsum("ij,ij->i", z[alive], z[alive]) / (c * (1.0 - (1.0 - lam) ** (2 * t)) if exact else c)
        hit = q > h
        length[alive[hit]] = t
        alive = alive[~hit]
        if alive.size == 0:
            break
    return float(length.mean())


@lru_cache(maxsize=64)
def mewma_h(p: int, lam: float, arl0: float, runs: int = 4000, seed: int = 20260201) -> float:
    """The limit h that gives the in-control ARL asked for, by simulation (about 2 % uncertain in the ARL)."""
    cap = int(30 * arl0)
    lo, hi = 0.5 * p, chi2.isf(1.0 / arl0, p) * 1.5 + 5.0
    for _ in range(11):  # coarse bisection with few runs
        mid = (lo + hi) / 2.0
        if _mean_run_length(p, lam, mid, 300, seed, cap) < arl0:
            lo = mid
        else:
            hi = mid
    a, b = lo * 0.96, hi * 1.04  # refine with many runs: log(ARL) is nearly linear in h
    ya, yb = np.log(_mean_run_length(p, lam, a, runs, seed, cap)), np.log(_mean_run_length(p, lam, b, runs, seed, cap))
    target = np.log(arl0)
    for _ in range(4):
        h = a + (target - ya) * (b - a) / (yb - ya) if yb != ya else (a + b) / 2.0
        yh = np.log(_mean_run_length(p, lam, h, runs, seed, cap))
        if abs(yh - target) < 0.01:
            break
        if yh < target:
            a, ya = h, yh
        else:
            b, yb = h, yh
    return float(h)


def mcusum_y(xbars, mu, cov, m: int, k: float) -> float:
    """Y after the sample means `xbars` (oldest first) of one run, started at zero."""
    mu = np.asarray(mu, dtype=float)
    s = np.zeros(mu.size)
    cov_m = cov / m
    for xb in xbars:
        v = s + np.asarray(xb, dtype=float) - mu
        c = math.sqrt(float(v @ np.linalg.solve(cov_m, v)))
        s = v * (1.0 - k / c) if c > k else np.zeros_like(v)
    return float(math.sqrt(max(0.0, float(s @ np.linalg.solve(cov_m, s)))))


def _mcusum_run_length(p: int, k: float, h: float, runs: int, seed: int, cap: int) -> float:
    rng = np.random.default_rng(seed)
    s = np.zeros((runs, p))
    alive = np.arange(runs)
    length = np.full(runs, float(cap))
    for t in range(1, cap + 1):
        v = s[alive] + rng.standard_normal((alive.size, p))
        c = np.sqrt(np.einsum("ij,ij->i", v, v))
        f = np.where(c > k, 1.0 - k / np.maximum(c, 1e-300), 0.0)
        s[alive] = v * f[:, None]
        hit = np.sqrt(np.einsum("ij,ij->i", s[alive], s[alive])) > h
        length[alive[hit]] = t
        alive = alive[~hit]
        if alive.size == 0:
            break
    return float(length.mean())


@lru_cache(maxsize=64)
def mcusum_h(p: int, k: float, arl0: float, runs: int = 4000, seed: int = 20260202) -> float:
    """The decision limit h for the in-control ARL asked for, by simulation (about 2 % uncertain in the ARL)."""
    cap = int(30 * arl0)
    lo, hi = 0.3, 4.0 * math.sqrt(p) + 12.0
    for _ in range(11):
        mid = (lo + hi) / 2.0
        if _mcusum_run_length(p, k, mid, 300, seed, cap) < arl0:
            lo = mid
        else:
            hi = mid
    a, b = lo * 0.96, hi * 1.04
    ya, yb = np.log(_mcusum_run_length(p, k, a, runs, seed, cap)), np.log(_mcusum_run_length(p, k, b, runs, seed, cap))
    target = np.log(arl0)
    h = (a + b) / 2.0
    for _ in range(4):
        h = a + (target - ya) * (b - a) / (yb - ya) if yb != ya else (a + b) / 2.0
        yh = np.log(_mcusum_run_length(p, k, h, runs, seed, cap))
        if abs(yh - target) < 0.01:
            break
        if yh < target:
            a, ya = h, yh
        else:
            b, yb = h, yh
    return float(h)

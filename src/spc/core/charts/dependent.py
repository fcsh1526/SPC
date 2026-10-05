"""Autocorrelated data and multi-stream processes (draft 10.3.1 overview, 10.3.2.6, 6.x sampling).

The draft names both problems and gives no formulas: "to monitor data showing autocorrelation, e.g. the autoregressive
model can be used for transformation" and for several streams or heads "a Sources of Variation (SoV) study ... how to
collect and monitor the data (nested data structure)". The standard methods are used here.

Autocorrelation: fit an AR(p) model x_t - mu = sum phi_i (x_(t-i) - mu) + e_t to the reference and chart the residuals e_t
  (a special-cause chart): they are about independent, so an individuals chart with moving range is valid again. The price is
  that a lasting shift is absorbed by the model after a few samples and is seen mostly at the start (Montgomery 2020).

Several streams (spindles, cavities, heads) with k values per sample: two-way analysis of variance of the reference
  (time x stream) separates the level of the process, the fixed difference of each stream and the noise:
    x_tj = mu + d_j + b_t + e_tj      (d_j: stream offset, sum 0; b_t: common level of sample t, sd sigma_time; e_tj: sd sigma_w)
  Chart 1: the mean over the streams (level), limits mu +- u * sd of the means.
  Chart 2: the largest standardised deviation of a stream, z_tj = (x_tj - xbar_t - d_j) / (sigma_w sqrt((k-1)/k)), limit
  h = u of the Sidak risk for k streams. Neither the fixed differences between streams nor the common level disturb it;
  a stream that wanders off does (Boyd 1950 group chart; Mortell and Runger 1995).
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

MIN_AR = 50
MAX_ORDER = 3


def ljung_box(x, lags: int = 10, fitted: int = 0) -> tuple[float, float]:
    """(statistic, p-value) of the Ljung-Box test of independence; `fitted` model parameters reduce the degrees of freedom."""
    x = np.asarray(x, dtype=float)
    n = x.size
    lags = max(1, min(lags, n // 4))
    xc = x - x.mean()
    denom = float(xc @ xc)
    r = np.array([float(xc[k:] @ xc[:-k]) / denom for k in range(1, lags + 1)])
    q = float(n * (n + 2) * np.sum(r ** 2 / (n - np.arange(1, lags + 1))))
    return q, float(stats.chi2.sf(q, max(1, lags - fitted)))


def _ols(x: np.ndarray, p: int):
    """Conditional least squares of x_t = c + sum phi_i x_(t-i): (c, phi, residual variance, number of residuals)."""
    n = x.size
    y = x[p:]
    cols = [np.ones(n - p)] + [x[p - i:n - i] for i in range(1, p + 1)]
    a = np.column_stack(cols)
    beta, *_ = np.linalg.lstsq(a, y, rcond=None)
    res = y - a @ beta
    return float(beta[0]), beta[1:], float(res @ res), n - p


DF_CRITICAL_5 = -2.86  # Dickey-Fuller, with a constant, 5 %: a t value above it cannot rule out a random walk


def unit_root_t(x: np.ndarray, order: int) -> float:
    """t value of gamma in the augmented Dickey-Fuller regression dx_t = c + gamma x_(t-1) + sum d_i dx_(t-i) + e (order - 1 lagged differences)."""
    dx = np.diff(x)
    lags = order - 1
    y = dx[lags:]
    cols = [np.ones(y.size), x[lags:-1]] + [dx[lags - i:dx.size - i] for i in range(1, lags + 1)]
    a = np.column_stack(cols)
    beta, *_ = np.linalg.lstsq(a, y, rcond=None)
    res = y - a @ beta
    s2 = float(res @ res) / (y.size - a.shape[1])
    cov = s2 * np.linalg.inv(a.T @ a)
    return float(beta[1] / math.sqrt(cov[1, 1]))


def stationary(phi) -> bool:
    phi = np.asarray(phi, dtype=float)
    if phi.size == 0:
        return True
    roots = np.roots(np.concatenate([-phi[::-1], [1.0]]))  # 1 - phi_1 z - ... - phi_p z^p = 0
    return bool(np.all(np.abs(roots) > 1.0 + 1e-9))


def fit_ar(values, order: int | None = None, max_order: int = MAX_ORDER) -> dict:
    """AR(p) by least squares. order=None chooses p = 1..max_order by AIC; a reference without autocorrelation is refused."""
    x = np.asarray(values, dtype=float).ravel()
    if not np.all(np.isfinite(x)):
        raise ValueError("the reference contains values that are not numbers")
    if x.size < MIN_AR:
        raise ValueError(f"the reference needs at least {MIN_AR} values in time order")
    if not float(x.std()) > 0:
        raise ValueError("the reference values do not vary")
    raw_q, raw_p = ljung_box(x)
    if order is None:
        best = None
        for p in range(1, max_order + 1):
            c, phi, rss, m = _ols(x, p)
            aic = m * math.log(rss / m) + 2 * (p + 1)
            if best is None or aic < best[0]:
                best = (aic, p)
        order = best[1]
        if raw_p > 0.05:
            raise ValueError(f"the reference shows no autocorrelation (Ljung-Box p = {raw_p:.2f}): an individuals chart is enough")
    elif not 1 <= order <= max_order:
        raise ValueError(f"the order of the model must be 1 to {max_order}")
    c, phi, rss, m = _ols(x, order)
    if not stationary(phi) or unit_root_t(x, order) > DF_CRITICAL_5:  # a random walk has no level to fluctuate around
        raise ValueError("the fitted model is not stationary: the values drift, they do not fluctuate around a level. Remove the trend first")
    mu = c / (1.0 - float(phi.sum()))
    sigma_e = math.sqrt(rss / (m - order - 1))
    resid = residuals(x, mu, phi)
    res_q, res_p = ljung_box(resid[order:], fitted=order)
    return {"order": int(order), "phi": [float(v) for v in phi], "mu": float(mu), "sigma_e": float(sigma_e),
            "diagnostics": {"n": int(x.size), "raw_lag1": float(np.corrcoef(x[:-1], x[1:])[0, 1]), "raw_ljung_box_p": raw_p,
                            "residual_ljung_box_p": res_p, "sigma_raw": float(x.std(ddof=1))}}


def residuals(x, mu: float, phi) -> np.ndarray:
    """e_t = (x_t - mu) - sum phi_i (x_(t-i) - mu); the first p entries use the mean for the missing values."""
    x = np.asarray(x, dtype=float)
    phi = np.asarray(phi, dtype=float)
    d = x - mu
    pad = np.concatenate([np.zeros(phi.size), d])
    e = d.copy()
    for i, f in enumerate(phi, start=1):
        e -= f * pad[phi.size - i:phi.size - i + d.size]
    return e


def next_residual(history, value: float, mu: float, phi) -> float:
    """Residual of a new value. `history`: the latest values, oldest first (fewer than p: the mean stands in)."""
    phi = np.asarray(phi, dtype=float)
    h = list(history)[-phi.size:] if phi.size else []
    lags = [mu] * (phi.size - len(h)) + h  # oldest first
    e = value - mu
    for i, f in enumerate(phi, start=1):
        e -= f * (lags[-i] - mu)
    return float(e)


# ---------------------------------------------------------------------------------------------- several streams

def sidak_h(alpha: float, k: int) -> float:
    """Limit h for the largest |z| of k streams so that the chance that any stream crosses it is alpha."""
    return float(stats.norm.ppf((1.0 + (1.0 - alpha) ** (1.0 / k)) / 2.0))


def estimate_streams(rows) -> dict:
    """Two-way analysis of variance of reference rows (time x stream)."""
    x = np.asarray(rows, dtype=float)
    if x.ndim != 2 or not np.all(np.isfinite(x)):
        raise ValueError("the reference needs rows of finite numbers, one value per stream")
    t, k = x.shape
    if not 2 <= k <= 20:
        raise ValueError("2 to 20 streams are possible")
    if t < max(20, 3 * k):
        raise ValueError(f"the reference needs at least {max(20, 3 * k)} rows for {k} streams")
    m = x.mean(axis=0)
    mu = float(m.mean())
    xbar = x.mean(axis=1)
    resid = x - m - (xbar - mu)[:, None]
    s2_w = float((resid ** 2).sum() / ((t - 1) * (k - 1)))
    s2_mean = float(xbar.var(ddof=1))
    return {"mu": mu, "offsets": (m - mu).tolist(), "sigma_w": math.sqrt(s2_w), "sigma_time": math.sqrt(max(0.0, s2_mean - s2_w / k)),
            "sigma_mean": math.sqrt(s2_mean), "k": k, "n_rows": t}


def stream_scores(values, mu: float, offsets, sigma_w: float) -> tuple[float, np.ndarray]:
    """(mean over the streams, z of each stream)."""
    x = np.asarray(values, dtype=float)
    k = x.size
    xbar = float(x.mean())
    sigma_r = sigma_w * math.sqrt((k - 1) / k)
    return xbar, (x - xbar - np.asarray(offsets, dtype=float)) / sigma_r

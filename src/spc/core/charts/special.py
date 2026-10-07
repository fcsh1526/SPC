"""Charts that figure 10-5 of the draft names beyond the plain Shewhart charts: Laney p' and u' (over- and underdispersion), standardised count charts,
G and T charts (rare events), the percentile chart (distribution free), the uniformly weighted moving average (UWMA), the delta-to-target chart and
Levey-Jennings charts (one per stream). The draft names them and gives no formulas; the standard ones are used, the sources are named in each function.

All of them are analysis charts of one series: the limits come from the series itself, or from its first `reference_n` values when the chart is meant
to judge what comes after (phase II). Every function returns {"charts": [...], "parameters": {...}, "warnings": [...]}; a chart has `values`,
`center`, `lcl` and `ucl` (lists of the same length) and `alarms` (indices outside the limits).
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from scipy import stats

from spc.core.constants import ALPHA_3SIGMA, d2

MIN_POINTS = 20
D2_MR = d2(2)  # 1.128: sigma from the average moving range of two


def _u(alpha: float) -> float:
    if not 0 < alpha < 0.5:
        raise ValueError("alpha must be between 0 and 0.5")
    return float(stats.norm.isf(alpha / 2.0))


def _chart(name: str, values, center, lcl, ucl, labels=None, extra: dict | None = None) -> dict:
    v = np.asarray(values, dtype=float)
    n = v.size
    c = np.broadcast_to(np.asarray(center, dtype=float), (n,))
    lo = np.broadcast_to(np.asarray(lcl, dtype=float), (n,))
    hi = np.broadcast_to(np.asarray(ucl, dtype=float), (n,))
    alarms = [{"index": int(i), "rule": "beyond_limits"} for i in np.flatnonzero((v > hi) | (v < lo))]
    return {"name": name, "labels": labels if labels is not None else [str(i + 1) for i in range(n)], "values": v.tolist(), "center": c.tolist(),
            "lcl": lo.tolist(), "ucl": hi.tolist(), "alarms": alarms, **(extra or {})}


def _reference(x: np.ndarray, reference_n: int | None, minimum: int = MIN_POINTS) -> np.ndarray:
    ref = x if reference_n is None else x[:reference_n]
    if ref.size < minimum:
        raise ValueError(f"the reference needs at least {minimum} values (it has {ref.size})")
    return ref


def _counts(counts, sizes, whole_sizes: bool):
    x, n = np.asarray(counts, dtype=float).ravel(), np.asarray(sizes, dtype=float).ravel()
    if x.shape != n.shape:
        raise ValueError("counts and sizes must have the same length")
    if x.size < MIN_POINTS:
        raise ValueError(f"need at least {MIN_POINTS} samples")
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(n))) or np.any(x < 0) or np.any(x != np.floor(x)):
        raise ValueError("counts must be whole numbers of 0 or more")
    if np.any(n <= 0) or (whole_sizes and np.any(n != np.floor(n))):
        raise ValueError("sizes must be positive" + (" whole numbers" if whole_sizes else ""))
    return x, n


def standardised(kind: str, counts, sizes, alpha: float = ALPHA_3SIGMA) -> dict:
    """The z values of a p or u chart: z_i = (rate_i - rate) / sqrt(variance of one sample of size n_i). Exact for a plain chart only when the counts are
    binomial or Poisson; this is the building block of the Laney charts and a chart of its own with the constant limits +-u (draft 10.3.2.9)."""
    x, n = _counts(counts, sizes, kind == "p")
    if kind == "p":
        if np.any(x > n):
            raise ValueError("a count cannot exceed its sample size")
        centre = float(x.sum() / n.sum())
        if not 0 < centre < 1:
            raise ValueError("the average proportion must be between 0 and 1: a chart needs some nonconforming units")
        sigma = np.sqrt(centre * (1 - centre) / n)
    else:
        centre = float(x.sum() / n.sum())
        if not centre > 0:
            raise ValueError("the average number of nonconformities must be positive")
        sigma = np.sqrt(centre / n)
    rate = x / n
    return {"rate": rate, "centre": centre, "sigma": sigma, "z": (rate - centre) / sigma}


def sigma_z(z: np.ndarray) -> float:
    """Laney's standard deviation of the z values: average moving range / 1.128."""
    return float(np.mean(np.abs(np.diff(z))) / D2_MR)


def laney(kind: str, counts, sizes, alpha: float = ALPHA_3SIGMA, reference_n: int | None = None) -> dict:
    """Laney p' (kind "p") and u' (kind "u") charts (Laney 2002). Limits: centre +- u * sigma_z * sqrt(variance of one sample of size n_i), with sigma_z from the
    moving range of the z values. sigma_z near 1: the plain chart holds. Above 1: overdispersion, the plain limits are too narrow and give false alarms.
    Below 1: underdispersion, they are too wide."""
    base = standardised(kind, counts, sizes, alpha)
    x, n = _counts(counts, sizes, kind == "p")
    ref = _reference(x, reference_n)
    if reference_n is not None:
        base_ref = standardised(kind, x[:reference_n], n[:reference_n], alpha)
        centre = base_ref["centre"]
        sigma = np.sqrt(centre * (1 - centre) / n) if kind == "p" else np.sqrt(centre / n)
        z = (x / n - centre) / sigma
        sz = sigma_z(base_ref["z"])
    else:
        centre, sigma, z = base["centre"], base["sigma"], base["z"]
        sz = sigma_z(z)
    u = _u(alpha)
    lcl = np.maximum(centre - u * sz * sigma, 0.0)
    ucl = centre + u * sz * sigma
    if kind == "p":
        ucl = np.minimum(ucl, 1.0)
    # the limits of the plain chart for comparison: the normal approximation with sigma_z = 1
    plain_lcl, plain_ucl = np.maximum(centre - u * sigma, 0.0), centre + u * sigma
    chart = _chart("laney_" + kind, x / n, centre, lcl, ucl, extra={"plain_lcl": plain_lcl.tolist(), "plain_ucl": plain_ucl.tolist(), "z": z.tolist()})
    warnings = []
    if sz < 1.0 and kind == "p":
        warnings.append({"code": "laney_underdispersed", "sigma_z": sz})
    return {"charts": [chart], "parameters": {"center": float(centre), "sigma_z": sz, "alpha": alpha, "u": u,
                                               "dispersion": "over" if sz > 1.1 else "under" if sz < 0.9 else "none", "n_reference": int(ref.size)}, "warnings": warnings}


def z_chart(kind: str, counts, sizes, alpha: float = ALPHA_3SIGMA) -> dict:
    base = standardised(kind, counts, sizes, alpha)
    u = _u(alpha)
    chart = _chart("z_" + kind, base["z"], 0.0, -u, u)
    return {"charts": [chart], "parameters": {"center": base["centre"], "alpha": alpha, "u": u}, "warnings": []}


def g_chart(gaps, alpha: float = ALPHA_3SIGMA, reference_n: int | None = None) -> dict:
    """G chart for rare events: g_i is the number of opportunities (parts, hours ...) between two events (0 when they follow each other). Geometric
    distribution with p = 1 / (mean + 1); limits at its exact quantiles alpha/2 and 1 - alpha/2, centre line at the median (Kaminsky et al. 1992)."""
    g = np.asarray(gaps, dtype=float).ravel()
    if g.size < MIN_POINTS or not np.all(np.isfinite(g)) or np.any(g < 0) or np.any(g != np.floor(g)):
        raise ValueError(f"the gaps must be at least {MIN_POINTS} whole numbers of 0 or more")
    ref = _reference(g, reference_n)
    mean = float(ref.mean())
    if not mean > 0:
        raise ValueError("every gap is 0: events follow each other without a gap, there is nothing to chart")
    p = 1.0 / (mean + 1.0)
    dist = stats.geom(p, loc=-1)  # support 0, 1, 2, ...
    lcl, cl, ucl = (float(dist.ppf(q)) for q in (alpha / 2.0, 0.5, 1.0 - alpha / 2.0))
    chart = _chart("g", g, cl, lcl, ucl)
    return {"charts": [chart], "parameters": {"mean": mean, "p": p, "alpha": alpha, "n_reference": int(ref.size)},
            "warnings": [{"code": "rare_event_below"}] if any(a["index"] for a in chart["alarms"] if g[a["index"]] < lcl) else []}


def t_chart(times, alpha: float = ALPHA_3SIGMA, reference_n: int | None = None) -> dict:
    """T chart for rare events: t_i is the time between two events. Weibull distribution with the location 0 (the exponential one when the shape is 1), fitted by
    maximum likelihood; limits at the quantiles alpha/2 and 1 - alpha/2, centre line at the median."""
    t = np.asarray(times, dtype=float).ravel()
    if t.size < MIN_POINTS or not np.all(np.isfinite(t)) or np.any(t <= 0):
        raise ValueError(f"the times must be at least {MIN_POINTS} positive numbers")
    ref = _reference(t, reference_n)
    shape, _, scale = stats.weibull_min.fit(ref, floc=0.0)
    dist = stats.weibull_min(shape, 0.0, scale)
    lcl, cl, ucl = (float(dist.ppf(q)) for q in (alpha / 2.0, 0.5, 1.0 - alpha / 2.0))
    chart = _chart("t", t, cl, lcl, ucl)
    return {"charts": [chart], "parameters": {"shape": float(shape), "scale": float(scale), "alpha": alpha, "n_reference": int(ref.size)}, "warnings": []}


def percentile_chart(values, alpha: float = ALPHA_3SIGMA, reference_n: int | None = None) -> dict:
    """Percentile chart (distribution free): the limits are the empirical quantiles alpha/2 and 1 - alpha/2 of the reference, the centre line is its median.
    The draft asks for about 2000 values for the quantiles 0.135 % and 99.865 % (7.8.2.1); the same rule is scaled for other alpha: n >= 5.4 / alpha."""
    x = np.asarray(values, dtype=float).ravel()
    if x.size == 0 or not np.all(np.isfinite(x)):
        raise ValueError("the values must be finite numbers")
    ref = x if reference_n is None else x[:reference_n]
    need = round(5.4 / alpha)
    if ref.size < need:
        raise ValueError(f"empirical quantiles at alpha = {alpha} need a reference of at least {need} values (it has {ref.size}); use a larger alpha or the Pearson chart")
    lcl, cl, ucl = (float(v) for v in np.quantile(ref, [alpha / 2.0, 0.5, 1.0 - alpha / 2.0]))
    chart = _chart("percentile", x, cl, lcl, ucl)
    return {"charts": [chart], "parameters": {"alpha": alpha, "n_reference": int(ref.size), "minimum_reference": need}, "warnings": []}


def transformed_chart(values, method: str, alpha: float = ALPHA_3SIGMA, reference_n: int | None = None) -> dict:
    """Shewhart individuals chart of transformed values (draft 10.3.2.6, figure 10-5: a transformation that makes the data normal, then the plain chart).
    method "box-cox": y = (x^lambda - 1) / lambda, lambda by maximum likelihood (positive data); "johnson": y = a + b asinh((x - loc) / scale), Johnson SU by maximum
    likelihood (any data). The centre line and sigma of y are the mean and the average moving range / 1.128 of the reference; the limits are drawn on the scale
    of the measurement (back-transformed), so they are not symmetric about the centre line."""
    x = np.asarray(values, dtype=float).ravel()
    if x.size < MIN_POINTS or not np.all(np.isfinite(x)):
        raise ValueError(f"need at least {MIN_POINTS} finite values")
    ref = _reference(x, reference_n)
    u = _u(alpha)
    params: dict
    if method == "box-cox":
        if np.any(x <= 0):
            raise ValueError("the Box-Cox transformation needs positive values; use the Johnson transformation")
        _, lam = stats.boxcox(ref)
        lam = float(lam)

        def fwd(v):
            return np.log(v) if abs(lam) < 1e-9 else (v ** lam - 1.0) / lam

        def inv(y):
            if abs(lam) < 1e-9:
                return float(np.exp(y))
            base = lam * y + 1.0
            if base <= 0:
                raise ValueError("a limit lies outside the range of the Box-Cox transformation: use the Johnson transformation or the percentile chart")
            return float(base ** (1.0 / lam))
        params = {"lambda": lam}
    elif method == "johnson":
        a, b, loc, scale = (float(v) for v in stats.johnsonsu.fit(ref))

        def fwd(v):
            return a + b * np.arcsinh((v - loc) / scale)

        def inv(y):
            return float(loc + scale * np.sinh((y - a) / b))
        params = {"j_a": a, "j_b": b, "j_location": loc, "j_scale": scale}
    else:
        raise ValueError("the method is box-cox or johnson")
    y_ref = np.asarray(fwd(ref), dtype=float)
    centre = float(y_ref.mean())
    sigma = float(np.abs(np.diff(y_ref)).mean() / D2_MR)
    if not sigma > 0:
        raise ValueError("the reference does not vary")
    lo, mid, hi = inv(centre - u * sigma), inv(centre), inv(centre + u * sigma)
    chart = _chart("transformed", x, mid, lo, hi, extra={"transformed": np.asarray(fwd(x), dtype=float).tolist()})
    return {"charts": [chart], "parameters": {"method": method, **params, "center_y": centre, "sigma_y": sigma, "alpha": alpha, "n_reference": int(ref.size)}, "warnings": []}


def uwma_chart(values, span: int, alpha: float = ALPHA_3SIGMA, reference_n: int | None = None) -> dict:
    """Uniformly weighted moving average of the last `span` values (Montgomery): limits mean +- u sigma / sqrt(min(i, span)), mean and sigma of the reference
    (sigma from the average moving range / 1.128, so that a shift in the data does not widen the limits)."""
    x = np.asarray(values, dtype=float).ravel()
    if x.size < MIN_POINTS or not np.all(np.isfinite(x)):
        raise ValueError(f"need at least {MIN_POINTS} finite values")
    if isinstance(span, bool) or not isinstance(span, int) or not 2 <= span <= 50 or span >= x.size:
        raise ValueError("the span must be a whole number from 2 to 50 and below the number of values")
    ref = _reference(x, reference_n)
    mu = float(ref.mean())
    sigma = float(np.mean(np.abs(np.diff(ref))) / D2_MR)
    if not sigma > 0:
        raise ValueError("the reference has no variation")
    ma = np.array([x[max(0, i - span + 1): i + 1].mean() for i in range(x.size)])
    width = _u(alpha) * sigma / np.sqrt(np.minimum(np.arange(1, x.size + 1), span))
    chart = _chart("uwma", ma, mu, mu - width, mu + width)
    return {"charts": [chart], "parameters": {"mean": mu, "sigma": sigma, "span": span, "alpha": alpha, "n_reference": int(ref.size)}, "warnings": []}


def delta_target_chart(values, products: Sequence[str], targets: dict[str, float], alpha: float = ALPHA_3SIGMA, reference_n: int | None = None) -> dict:
    """Short runs without scaling: the differences x_i - target(product_i) on one individuals chart with the centre line 0 (the target) and sigma from the average
    moving range of the differences / 1.128. It assumes that all products vary alike; where they do not, use the Z-MR chart."""
    x = np.asarray(values, dtype=float).ravel()
    p = [str(v) for v in products]
    if x.size < MIN_POINTS or len(p) != x.size or not np.all(np.isfinite(x)):
        raise ValueError(f"need at least {MIN_POINTS} values with a product for each")
    missing = sorted(set(p) - set(targets))
    if missing:
        raise ValueError(f"no target for the product(s) {missing}")
    d = x - np.array([float(targets[v]) for v in p])
    ref = _reference(d, reference_n)
    sigma = float(np.mean(np.abs(np.diff(ref))) / D2_MR)
    if not sigma > 0:
        raise ValueError("the differences have no variation")
    u = _u(alpha)
    chart = _chart("delta_target", d, 0.0, -u * sigma, u * sigma, labels=[f"{i + 1}:{v}" for i, v in enumerate(p)])
    return {"charts": [chart], "parameters": {"sigma": sigma, "alpha": alpha, "mean_difference": float(ref.mean()), "n_reference": int(ref.size)}, "warnings": []}


def levey_jennings(values, streams: Sequence[str], alpha: float = ALPHA_3SIGMA) -> dict:
    """One individuals chart for each stream (spindle, cavity, batch line ...): the mean and standard deviation of that stream give its centre line and limits."""
    x = np.asarray(values, dtype=float).ravel()
    s = [str(v) for v in streams]
    if len(s) != x.size or not np.all(np.isfinite(x)):
        raise ValueError("every value needs a stream")
    u = _u(alpha)
    charts = []
    for name in dict.fromkeys(s):
        v = x[[i for i, k in enumerate(s) if k == name]]
        if v.size < MIN_POINTS:
            raise ValueError(f"stream {name!r} has {v.size} values: at least {MIN_POINTS} are needed")
        sd = float(v.std(ddof=1))
        if not sd > 0:
            raise ValueError(f"stream {name!r} has no variation")
        charts.append(_chart(f"levey_jennings:{name}", v, float(v.mean()), float(v.mean()) - u * sd, float(v.mean()) + u * sd,
                             extra={"stream": name, "sd": sd}))
    return {"charts": charts, "parameters": {"alpha": alpha, "streams": len(charts)}, "warnings": []}

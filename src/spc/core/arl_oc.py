"""Operating characteristic and average run length of a Shewhart location chart.

For a shift d (in units of sigma) and subgroup size n, with two-sided risk alpha:

    alarm probability  1 - beta = Phi(-u + d*sqrt(n)) + Phi(-u - d*sqrt(n)),  u = u(1 - alpha/2)
    ARL_1 = 1 / (1 - beta),  ARL_0 = 1 / alpha   (ISO 7870 relations)

The manual's figures use a 99 % non-intervention probability (alpha = 0.01). Pass alpha explicitly.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm

from spc.core.constants import ALPHA_3SIGMA, check_alpha, u_quantile


def alarm_probability(shift, n: int, alpha: float = ALPHA_3SIGMA):
    """Probability that one subgroup mean falls outside the limits after a shift of `shift` sigma."""
    check_alpha(alpha)
    if n < 1:
        raise ValueError("n must be at least 1")
    u = u_quantile(alpha)
    s = np.asarray(shift, dtype=float) * np.sqrt(n)
    return norm.cdf(-u + s) + norm.cdf(-u - s)


def arl(shift, n: int, alpha: float = ALPHA_3SIGMA):
    """Average number of subgroups until the first alarm."""
    return 1.0 / alarm_probability(shift, n, alpha)


def required_subgroup_size(shift: float, max_arl: float, alpha: float = ALPHA_3SIGMA, n_max: int = 1000) -> int:
    """Smallest subgroup size whose ARL for `shift` sigma is at most `max_arl`.

    Use this to turn a requirement such as "a 0.75 sigma shift must be caught within 10 subgroups"
    into a subgroup size.
    """
    if shift <= 0 or max_arl < 1:
        raise ValueError("shift must be positive and max_arl at least 1")
    for n in range(1, n_max + 1):
        if arl(shift, n, alpha) <= max_arl:
            return n
    raise ValueError(f"no subgroup size up to {n_max} reaches ARL <= {max_arl}")


def sampling_interval(shift: float, n: int, max_parts: int, alpha: float = ALPHA_3SIGMA, parts_per_hour: float | None = None) -> dict:
    """How often to sample (draft 10.4: sample size and frequency).

    A subgroup of n parts is taken every h parts. After a shift of `shift` sigma the chart signals after ARL samples on average, so about h x ARL parts are made
    before the signal; at most `max_parts` of them are accepted: h = floor(max_parts / ARL). The interval cannot be shorter than the subgroup (h >= n);
    when it would be, the subgroup is too small for this shift and this limit (feasible = False)."""
    a = float(arl(shift, n, alpha))
    h = int(max_parts // a) if a > 0 else max_parts
    out = {"n": n, "shift": shift, "arl": a, "max_parts": max_parts, "interval_parts": h, "feasible": h >= n, "parts_until_signal": float(h * a)}
    if parts_per_hour:
        out["interval_minutes"] = h / parts_per_hour * 60.0
        out["samples_per_hour"] = parts_per_hour / h if h > 0 else None
    return out

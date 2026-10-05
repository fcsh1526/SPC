"""CUSUM and EWMA charts (draft 10.3.5.4 and 10.3.5.5, ISO 7870-4 and 7870-6).

Both react to small, lasting shifts of the mean. Everything is in units of the standard error of the plotted
mean, sigma_x = sigma / sqrt(n); k, h, the shift and L are dimensionless.

* CUSUM, tabular form: CO_i = max(0, CO_(i-1) + x_i - (T + k sigma_x)) and CU_i = min(0, CU_(i-1) + x_i - (T - k sigma_x)).
  A signal when CO_i > h sigma_x or CU_i < -h sigma_x.
* EWMA: z_i = lambda x_i + (1 - lambda) z_(i-1), z_0 = T. Limits T +- L sigma_x sqrt(lambda/(2-lambda) (1 - (1-lambda)^(2i))).

The average run length (ARL) comes from the integral equation of the run length, solved by Gauss-Legendre quadrature
(Brook and Evans, Goel and Wu for the CUSUM; Crowder for the EWMA), so h and L are exact for the ARL that is asked
for. The draft's ARL table for the CUSUM (ARL 370.4, 163.6, 54.5, ... at shifts 0, 0.2, 0.4 sigma) is reproduced with
k = 0.5 and h = 4.7749.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.optimize import brentq
from scipy.stats import norm

NODES = 96
SHIFTS = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)  # shown in units of sigma_x


def _cusum_arl_one_sided(h: float, k: float, shift: float) -> float:
    """ARL of the upper one-sided CUSUM S_i = max(0, S_(i-1) + z_i - k), z ~ N(shift, 1), signal at S > h."""
    x, w = leggauss(NODES)
    y, w = (x + 1.0) * h / 2.0, w * h / 2.0
    nodes = np.concatenate([[0.0], y])
    a = np.zeros((NODES + 1, NODES + 1))
    for i, u in enumerate(nodes):
        a[i, 0] = norm.cdf(k - u - shift)
        a[i, 1:] = w * norm.pdf(y - u + k - shift)
    return float(np.linalg.solve(np.eye(NODES + 1) - a, np.ones(NODES + 1))[0])


def cusum_arl(h: float, k: float, shift: float) -> float:
    """ARL of the two-sided CUSUM (the two sides are independent enough: 1/ARL = 1/ARL+ + 1/ARL-)."""
    return 1.0 / (1.0 / _cusum_arl_one_sided(h, k, shift) + 1.0 / _cusum_arl_one_sided(h, k, -shift))


def cusum_h(k: float, arl0: float) -> float:
    """The decision interval h that gives the in-control ARL asked for."""
    return float(brentq(lambda h: cusum_arl(h, k, 0.0) - arl0, 0.2, 40.0, xtol=1e-9))


def ewma_arl(l_mult: float, lam: float, shift: float) -> float:
    """ARL of the two-sided EWMA with the asymptotic limits +-L sqrt(lam/(2-lam)), started at the target."""
    half = l_mult * math.sqrt(lam / (2.0 - lam))
    x, w = leggauss(NODES)
    y, w = x * half, w * half
    a = np.zeros((NODES, NODES))
    for i, u in enumerate(y):
        mean = (1.0 - lam) * u + lam * shift
        a[i] = w * norm.pdf(y, loc=mean, scale=lam)
    sol = np.linalg.solve(np.eye(NODES) - a, np.ones(NODES))
    # value at the start (u = 0) by the equation itself
    mean0 = lam * shift
    return float(1.0 + np.sum(w * norm.pdf(y, loc=mean0, scale=lam) * sol))


def ewma_l(lam: float, arl0: float) -> float:
    return float(brentq(lambda l: ewma_arl(l, lam, 0.0) - arl0, 0.5, 6.0, xtol=1e-9))


def arl_table(kind: str, design: dict) -> list[dict]:
    """ARL by shift of the mean, in units of sigma_x."""
    out = []
    for d in SHIFTS:
        v = cusum_arl(design["h"], design["k"], d) if kind == "cusum" else ewma_arl(design["L"], design["lambda"], d)
        out.append({"shift": d, "arl": float(v)})
    return out


def cusum_step(co: float, cu: float, x: float, target: float, k_abs: float) -> tuple[float, float]:
    return max(0.0, co + x - (target + k_abs)), min(0.0, cu + x - (target - k_abs))


def ewma_half_width(sigma_x: float, l_mult: float, lam: float, i: int) -> float:
    """Half width of the limits at the i-th point after the start (i = 1, 2, ...)."""
    return l_mult * sigma_x * math.sqrt(lam / (2.0 - lam) * (1.0 - (1.0 - lam) ** (2 * i)))

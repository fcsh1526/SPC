"""Geometric tolerancing with the maximum or least material requirement (AIAG-VDA SPC draft 8.5.3; ISO 22514-6 and -9).

A size feature (bore or pin) with a position tolerance TP under MMR/LMR gives the part one requirement that joins two measured values:
the measured size x_D and the position deviation x_P (the diameter of the circle, d = 2 sqrt(dx^2 + dy^2)). The requirement is that the
assembly clearance C is not negative. The distribution of C is evaluated like any one-sided characteristic with the lower limit L_C = 0:
location C50 %, spread quantiles C0.135 % and C99.865 %, indices Ppk.G or Ppk.Z.

Virtual sizes (limits L, U of the size, mm in the draft):
    bore, MMC:  MMS = L,  MMVS = L - TP,  C = x_D - x_P - MMVS
    bore, LMC:  LMS = U,  LMVS = U + TP,  C = LMVS - x_D - x_P
    pin,  MMC:  MMS = U,  MMVS = U + TP,  C = MMVS - x_D - x_P
    pin,  LMC:  LMS = L,  LMVS = L - TP,  C = x_D - x_P - LMVS
Each says: x_P may reach TP plus the departure of the size from the material limit. The draft prints C = x_D + x_P - MMVS for the bore, with a plus before
x_P; with it a bigger position error would improve the clearance (the draft's own example: x_D = 20.0, x_P = 0.2 is just assemblable, C = 0, but the printed
formula gives 0.4). The program uses the sign that follows from MMVS = L - TP and says so in the result.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from spc.core.capability.indices import CapabilityIndices, geometric_indices, ppm_out_of_spec, zscore_indices
from spc.core.distributions import (
    EXPLICIT_ONLY, FAMILIES, FitError, GaussianMixture, bootstrap_interval, choose_automatically, fit, fit_candidates, quantiles,
)

KINDS = ("bore", "pin")
REQUIREMENTS = ("mmc", "lmc")
SIGN_NOTE = "draft_prints_plus_for_position"


def position_deviation(dx, dy) -> np.ndarray:
    """The diameter of the circle around the target position: d = 2 sqrt(dx^2 + dy^2)."""
    return 2.0 * np.hypot(np.asarray(dx, dtype=float), np.asarray(dy, dtype=float))


def virtual_size(kind: str, requirement: str, lower: float, upper: float, tp: float) -> tuple[float, float]:
    """(material limit size MMS or LMS, virtual size MMVS or LMVS)."""
    if kind not in KINDS or requirement not in REQUIREMENTS:
        raise ValueError(f"kind must be one of {KINDS} and requirement one of {REQUIREMENTS}")
    if not lower < upper:
        raise ValueError("the lower size limit must be below the upper one")
    if not tp > 0:
        raise ValueError("the position tolerance must be above 0")
    if requirement == "mmc":
        return (lower, lower - tp) if kind == "bore" else (upper, upper + tp)
    return (upper, upper + tp) if kind == "bore" else (lower, lower - tp)


def clearance(xd, xp, kind: str, requirement: str, lower: float, upper: float, tp: float) -> np.ndarray:
    xd, xp = np.asarray(xd, dtype=float), np.asarray(xp, dtype=float)
    _, v = virtual_size(kind, requirement, lower, upper, tp)
    if kind == "bore" and requirement == "mmc" or kind == "pin" and requirement == "lmc":
        return xd - xp - v
    return v - xd - xp


def _f(x):
    return None if x is None or not math.isfinite(float(x)) else float(x)


def analyse_clearance(xd, xp=None, *, dx=None, dy=None, kind: str = "bore", requirement: str = "mmc", lower: float, upper: float, position_tolerance: float,
                      distribution: str = "auto", method: str = "G", bootstrap_n: int = 200, confidence: float = 0.95, seed: int = 20260701) -> dict:
    """Assembly clearance of each part and its capability against L_C = 0."""
    xd = np.asarray(xd, dtype=float).ravel()
    if (xp is None) == (dx is None or dy is None):
        raise ValueError("give either the position deviations xp, or both dx and dy")
    if xp is None:
        dx, dy = np.asarray(dx, dtype=float).ravel(), np.asarray(dy, dtype=float).ravel()
        if dx.shape != dy.shape:
            raise ValueError("dx and dy must have the same length")
        xp = position_deviation(dx, dy)
    xp = np.asarray(xp, dtype=float).ravel()
    if xd.shape != xp.shape:
        raise ValueError("sizes and position deviations must have the same length")
    if not (np.all(np.isfinite(xd)) and np.all(np.isfinite(xp))):
        raise ValueError("values contain NaN or inf")
    if np.any(xp < 0):
        raise ValueError("a position deviation is a diameter and cannot be negative")
    if distribution not in ("auto", *FAMILIES, *EXPLICIT_ONLY):
        raise ValueError(f"distribution must be 'auto' or one of {FAMILIES + EXPLICIT_ONLY}")
    if method not in ("G", "Z"):
        raise ValueError("method must be 'G' or 'Z'")
    if distribution == "empirical" and method == "Z":
        raise ValueError("the empirical distribution supports only method G")
    if not 0 <= bootstrap_n <= 2000:
        raise ValueError("bootstrap_n must be between 0 and 2000")
    limit, vs = virtual_size(kind, requirement, lower, upper, position_tolerance)
    c = clearance(xd, xp, kind, requirement, lower, upper, position_tolerance)
    n = int(c.size)
    if n < 5:
        raise ValueError("need at least 5 parts")
    warnings: list[dict] = []
    if n < 50:
        warnings.append({"code": "gdt_small_sample", "n": n})
    out: dict = {
        "kind": kind, "requirement": requirement, "lower": lower, "upper": upper, "position_tolerance": position_tolerance,
        "material_limit": limit, "virtual_size": vs, "sign_note": SIGN_NOTE if (kind, requirement) == ("bore", "mmc") else None,
        "n": n, "clearance": [float(v) for v in c], "position_deviation": [float(v) for v in xp],
        "observed": {"negative": int(np.sum(c < 0)), "size_out": int(np.sum((xd < lower) | (xd > upper))),
                     "position_out": int(np.sum(xp > position_tolerance)),
                     "mean_size": float(xd.mean()), "mean_position": float(xp.mean())},
    }
    if not np.ptp(c) > 0:
        raise ValueError("all clearances are equal: no spread to evaluate")
    try:
        cands = fit_candidates(c, tuple(f for f in FAMILIES if f != "empirical"))
        if distribution == "auto":
            chosen = choose_automatically(cands)
            family = chosen.family
            comps = len(chosen.dist.w) if isinstance(chosen.dist, GaussianMixture) else None
        else:
            family, comps = distribution, None
            hit = next((x for x in cands if x.family == family), None)
            if hit is not None and not hit.ok:
                raise FitError(f"{family}: {hit.reason}")
        dist = fit(c, family, components=comps)
        q = quantiles(dist)
    except FitError as exc:
        raise ValueError(f"the distribution could not be fitted: {exc}") from None
    index_fn = (lambda d: geometric_indices(d, 0.0, None)) if method == "G" else (lambda d: zscore_indices(d, 0.0, None))
    idx: CapabilityIndices = replace(index_fn(dist), n=n)
    ci = None
    if bootstrap_n:
        bi = bootstrap_interval(c, dist, index_fn, bootstrap_n, confidence, seed)
        ci = bi.ci_pk
        out["bootstrap"] = {"requested": bi.requested, "succeeded": bi.succeeded, "seed": seed, "confidence": confidence}
        if ci is None:
            warnings.append({"code": "bootstrap_failed", "succeeded": bi.succeeded, "requested": bi.requested})
    out["distribution"] = {"requested": distribution, "name": family, "method": method,
                           "candidates": [{"family": x.family, "ok": x.ok, "aic": _f(x.aic), "delta_aic": _f(x.delta_aic), "ad": _f(x.ad), "reason": x.reason} for x in cands]}
    out["quantiles"] = {"c_0135": q[0], "c_50": q[1], "c_99865": q[2]}
    out["indices"] = {"pk": _f(idx.pk), "pl": _f(idx.pl), "ci_pk": None if ci is None else [_f(ci[0]), _f(ci[1])], "ci_confidence": confidence,
                      "method": ("General Geometric (.G)" if method == "G" else "z-score (.Z)") + f", {family}", "ppm": _f(ppm_out_of_spec(dist, 0.0, None))}
    out["warnings"] = warnings
    return out

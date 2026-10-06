"""The estimators of location and dispersion of ISO 22514-2:2017, formulas (11) to (19), and the sample statistics of ISO 7870-2:2013, 3.2.

location l = 1  mean of all values            l = 2  median of all values
         l = 3  mean of the subgroup means    l = 4  mean of the subgroup medians
dispersion d = 2  sqrt(mean of the subgroup variances)      d = 3  s-bar / c4
           d = 4  R-bar / d2                                d = 5  standard deviation of all values
           d = 1  the width of the 99,73 % interval of the distribution (6 sigma for the normal distribution, see `spread`)
"""

from __future__ import annotations

import numpy as np

from spc.core.constants import c4, d2, u_quantile


def location(matrix) -> dict[str, float]:
    m = np.asarray(matrix, dtype=float)
    x = m.ravel()
    return {"l1": float(x.mean()), "l2": float(np.median(x)), "l3": float(m.mean(axis=1).mean()), "l4": float(np.median(m, axis=1).mean())}


def sample_statistics(matrix, values=None) -> dict[str, float]:
    """s-bar, R-bar (subgroups) and the range and the mean moving range of the individual values in the order of the data.
    `values` defaults to the matrix read row by row."""
    m = np.asarray(matrix, dtype=float)
    x = m.ravel() if values is None else np.asarray(values, dtype=float)
    return {"sbar": float(m.std(axis=1, ddof=1).mean()), "Rbar": float(np.ptp(m, axis=1).mean()), "Rtotal": float(np.ptp(x)),
            "Rm": float(np.abs(np.diff(x)).mean())}


def dispersion(matrix) -> dict[str, float]:
    m = np.asarray(matrix, dtype=float)
    n = m.shape[1]
    s = m.std(axis=1, ddof=1)
    return {"d2": float(np.sqrt((s ** 2).mean())), "d3": float(s.mean() / c4(n)), "d4": float(np.ptp(m, axis=1).mean() / d2(n)),
            "d5": float(m.ravel().std(ddof=1))}


def spread(sigma: float, alpha: float = 0.0027) -> float:
    """Method d = 1 for a normal distribution: the interval that holds 1 - alpha of the values (6 sigma at alpha = 0,27 %)."""
    return 2.0 * u_quantile(alpha) * sigma


def estimators(matrix, values=None) -> dict[str, float]:
    """All of them for subgroups in a (k, n) matrix. d1 is the normal-distribution value with d = 5."""
    out = {**location(matrix), **dispersion(matrix), **sample_statistics(matrix, values)}
    out["d1"] = spread(out["d5"])
    return out

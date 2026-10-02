"""Hints for values that may be outliers.

A hint is not a reason. The manual (draft 7.6) says outlier tests only check whether values fit an
assumed distribution (usually the normal). They cannot identify an outlier. A person must give a
reason why the value cannot come from the investigated process, and then calls
`Dataset.mark_invalid`. These functions never change the data.

The specification limits play no role here. A value far outside tolerance is only a suspect, and
a value inside tolerance can still be an outlier.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import t as student_t

from spc.data.dataset import Dataset


@dataclass(frozen=True)
class Suspect:
    position: int  # position in the dataset (0-based). For an array input: index in the array.
    value: float
    score: float
    method: str


def suspects(data, method: str = "mad", threshold: float | None = None) -> list[Suspect]:
    """Return values worth a look, ordered by position. Values already invalid are not examined.

    method "mad"    : robust z = 0.6745 * (x - median) / MAD. Default threshold 3.5 (Iglewicz-Hoaglin).
                      Fails when MAD is 0, because the scale is then undefined.
    method "tukey"  : outside the quartile fences, Q1 - k*IQR and Q3 + k*IQR. Default k = 3 (far out).
    method "grubbs" : single Grubbs test for the most extreme value. threshold = two-sided significance
                      level, default 0.05. Assumes a normal distribution. Returns at most one value.
    """
    if isinstance(data, Dataset):
        x, positions = data.individuals()
    else:
        x = np.asarray(data, dtype=float).ravel()
        positions = np.arange(x.size)
    if x.size < 3:
        raise ValueError("need at least 3 values")
    if not np.all(np.isfinite(x)):
        raise ValueError("values contain NaN or inf")

    if method == "mad":
        limit = 3.5 if threshold is None else threshold
        med = np.median(x)
        mad = np.median(np.abs(x - med))
        if mad == 0:
            raise ValueError("MAD is 0 (more than half of the values are equal): use another method")
        score = 0.6745 * (x - med) / mad
        hit = np.abs(score) > limit
    elif method == "tukey":
        k = 3.0 if threshold is None else threshold
        q1, q3 = np.percentile(x, [25, 75])
        iqr = q3 - q1
        if iqr == 0:
            raise ValueError("IQR is 0: use another method")
        lo, hi = q1 - k * iqr, q3 + k * iqr
        score = np.where(x > hi, (x - hi) / iqr, np.where(x < lo, (x - lo) / iqr, 0.0))
        hit = (x > hi) | (x < lo)
    elif method == "grubbs":
        alpha = 0.05 if threshold is None else threshold
        n = x.size
        if n < 4:
            raise ValueError("Grubbs test needs at least 4 values")
        sd = x.std(ddof=1)
        if sd == 0:
            raise ValueError("all values are equal")
        z = (x - x.mean()) / sd
        g_max = np.max(np.abs(z))
        tc = student_t.ppf(1.0 - alpha / (2.0 * n), n - 2)
        g_crit = (n - 1) / np.sqrt(n) * np.sqrt(tc**2 / (n - 2 + tc**2))
        score = z
        hit = np.zeros(n, dtype=bool)
        if g_max > g_crit:
            hit[int(np.argmax(np.abs(z)))] = True
    else:
        raise ValueError(f"unknown method {method!r}")

    return [Suspect(int(positions[i]), float(x[i]), float(score[i]), method) for i in np.flatnonzero(hit)]

"""Sources of variation in nested data: a complete nested analysis of variance (AIAG-VDA SPC draft 10.3.1, 10.3.2.1, 10.4 "Multiple Stream or
Multiple heads Process: Sources of Variation (SoV) study ... batch process with nested data structure").

Levels run from the outermost to the innermost (for example lot > stream > part). A group of one level lies inside exactly one group of the level above:
the program builds the groups from the path of labels, so "1" inside lot A and "1" inside lot B are different groups. The model is
    x = mu + e_1 + ... + e_k + error,   e_j ~ N(0, s_j^2) random effects, error ~ N(0, s_e^2).
Variance components come from the nested analysis of variance (method of moments) for balanced and unbalanced data: with g_i groups at level i (g_0 = 1, g_k+1 = n),
n_a the size of group a, SS_i = T_i - T_(i-1) with T_i = sum over groups of n_a * mean_a^2, and the expected mean squares
    E[SS_i] = s_e^2 (g_i - g_(i-1)) + sum_(j >= i) s_j^2 (Q(i, j) - Q(i-1, j)),   Q(i, j) = sum over groups a of level i of (sum of n_b^2 over groups b of level j inside a) / n_a,
with Q(i, i) read as the sum of n_a over level i groups (= n) for the first coefficient. The estimates are solved from the innermost level outwards. A negative estimate is set to 0 and marked:
the shares then add up to 100 % of the truncated components. For balanced data the coefficients are the products of the numbers below each level, the textbook ones (tested).

F is MS_i / MS_(i+1) (the next inner level, error for the last): exact for balanced data and for the innermost level; for unbalanced data it is an approximation (marked).
Intervals are not given: the draft gives none, and the approximate ones for variance components are unreliable with few groups.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

MAX_LEVELS = 5


def _f(x):
    return None if x is None or not math.isfinite(float(x)) else float(x)


def analyse(values, levels: dict[str, list], alpha: float = 0.05) -> dict:
    x = np.asarray(values, dtype=float).ravel()
    n = x.size
    names = list(levels)
    k = len(names)
    if not 1 <= k <= MAX_LEVELS:
        raise ValueError(f"give 1 to {MAX_LEVELS} nested levels, outermost first")
    if n < 4 or not np.all(np.isfinite(x)):
        raise ValueError("need at least 4 finite values")
    cols = []
    for nm in names:
        c = [str(v) for v in levels[nm]]
        if len(c) != n:
            raise ValueError(f"level {nm!r} must have one label per value ({n})")
        cols.append(c)
    # group id of each value at each level: the path of labels from the outermost level
    gid = np.zeros((k + 2, n), dtype=int)  # row 0: one group; row k+1: every value alone
    for i in range(1, k + 1):
        paths = {}
        for r in range(n):
            paths.setdefault(tuple(cols[j][r] for j in range(i)), len(paths))
        gid[i] = [paths[tuple(cols[j][r] for j in range(i))] for r in range(n)]
    gid[k + 1] = np.arange(n)
    g = [int(gid[i].max()) + 1 for i in range(k + 2)]  # g[0] = 1, g[k+1] = n
    sizes = [np.bincount(gid[i], minlength=g[i]).astype(float) for i in range(k + 2)]
    means = [np.bincount(gid[i], weights=x, minlength=g[i]) / sizes[i] for i in range(k + 2)]
    T = [float(np.sum(sizes[i] * means[i] ** 2)) for i in range(k + 2)]  # T[k+1] = sum x^2
    for i in range(1, k + 1):  # nesting: the parent of a group is unique
        parent = {}
        for r in range(n):
            if parent.setdefault(gid[i][r], gid[i - 1][r]) != gid[i - 1][r]:
                raise ValueError("a group lies in more than one group of the level above")
    if g[k] >= n:
        raise ValueError("the innermost level has a group for every value: there is no variation left within groups to estimate the error")
    if g[1] < 2:
        raise ValueError("the outermost level needs at least 2 groups")

    def Q(i: int, j: int) -> float:
        """Sum over level-i groups a of (sum of n_b^2 over level-j groups b inside a) / n_a, i < j (j = k+1: one value per group)."""
        per = np.bincount(gid[i][_first_index(gid[j], g[j])], weights=sizes[j] ** 2, minlength=g[i])
        return float(np.sum(per / sizes[i]))

    ss = [None] * (k + 2)
    df = [None] * (k + 2)
    for i in range(1, k + 1):
        ss[i] = max(T[i] - T[i - 1], 0.0)
        df[i] = g[i] - g[i - 1]
    ss_e = max(T[k + 1] - T[k], 0.0)
    df_e = n - g[k]
    ms = [None] + [ss[i] / df[i] for i in range(1, k + 1)] + [ss_e / df_e]
    comp = [0.0] * (k + 2)  # comp[k+1] = error variance
    comp[k + 1] = ms[k + 1]
    raw = [0.0] * (k + 2)
    raw[k + 1] = comp[k + 1]
    coef = {}
    for i in range(k, 0, -1):
        c_ii = (n - Q(i - 1, i)) / df[i]
        rest = comp[k + 1] * (g[i] - g[i - 1]) / df[i]
        for j in range(i + 1, k + 2):
            c = (Q(i, j) - Q(i - 1, j)) / df[i] if j <= k else (g[i] - g[i - 1]) / df[i]
            if j <= k:
                rest += c * comp[j]
                coef[(i, j)] = c
        coef[(i, i)] = c_ii
        raw[i] = (ms[i] - rest) / c_ii
        comp[i] = max(raw[i], 0.0)
    total = float(sum(comp[1:]))
    balanced = all(len(set(sizes[i].tolist())) == 1 for i in range(1, k + 1))
    rows = []
    for i in range(1, k + 1):
        den = ms[i + 1]
        f = ms[i] / den if den > 0 else None
        d2 = df[i + 1] if i < k else df_e
        p = _f(stats.f.sf(f, df[i], d2)) if f is not None else None
        rows.append({"level": names[i - 1], "groups": g[i], "df": int(df[i]), "ss": float(ss[i]), "ms": float(ms[i]), "f": _f(f), "df_den": int(d2), "p_value": p,
                     "significant": bool(p is not None and p < alpha), "variance": float(comp[i]), "variance_raw": float(raw[i]), "truncated": bool(raw[i] < 0),
                     "sd": float(math.sqrt(comp[i])), "share": float(comp[i] / total) if total > 0 else None,
                     "coefficients": {names[j - 1]: _f(coef[(i, j)]) for j in range(i, k + 1)} | {"error": 1.0}})
    err = {"level": "error", "groups": n, "df": int(df_e), "ss": float(ss_e), "ms": float(ms[k + 1]), "variance": float(comp[k + 1]), "sd": float(math.sqrt(comp[k + 1])),
           "share": float(comp[k + 1] / total) if total > 0 else None}
    largest = max(range(1, k + 2), key=lambda i: comp[i])
    return {
        "n": int(n), "levels": names, "alpha": alpha, "mean": float(x.mean()), "balanced": bool(balanced),
        "f_exact": bool(balanced),  # for unbalanced data the F ratios are approximate
        "table": rows, "error": err, "total_variance": total, "total_sd": float(math.sqrt(total)),
        "sd_all": float(x.std(ddof=1)), "largest": names[largest - 1] if largest <= k else "error",
        "any_truncated": any(r["truncated"] for r in rows),
    }


def _first_index(ids: np.ndarray, count: int) -> np.ndarray:
    """One value position for each group id (to read the parent of a group)."""
    first = np.full(count, -1, dtype=int)
    for pos in range(ids.size - 1, -1, -1):
        first[ids[pos]] = pos
    return first

"""D-optimal designs and mixture designs (AIAG-VDA SPC draft 6.4, the kinds of experimental designs; VDA 5, 4.4.2 names the D-optimal design for the uncertainty study).

D-optimal design: the program chooses n runs from a set of candidate points so that det(X'X) of the model matrix is as large as possible, which makes the estimates of the model
terms as precise as the number of runs allows. It is for what the standard designs cannot do: a number of runs that no standard design has, points that cannot be run (linear
limits on the factors), or both. The factors are coded -1 to +1. The search is Fedorov's exchange algorithm from several random starts; it finds a local optimum, so the result
reports how many starts reached the best value. It is not a proof of global optimality.

Mixture designs: the components are proportions that add up to 1, so they cannot be set independently and the usual models do not fit. The designs are the simplex lattice {q, m}
and the simplex centroid (with axial check points on request), with lower limits of the components handled by pseudo-components, and a D-optimal choice from a lattice
of the allowed region (lower and upper limits). The analysis is the Scheffé polynomial (linear, quadratic, special cubic) without intercept.
"""

from __future__ import annotations

import math
from itertools import combinations, product
from typing import Mapping, Sequence

import numpy as np
from scipy import stats

LETTERS = "ABCDEFGHIJK"
MODELS = ("main", "interaction", "quadratic")
MIXTURE_MODELS = ("linear", "quadratic", "special_cubic")
MAX_FACTORS = 8
MAX_RUNS = 200
MAX_CANDIDATES = 20000
MAX_COMPONENTS = 8


def _f(x):
    return float(x)


# ------------------------------------------------------------------ D-optimal designs

def model_matrix(points: np.ndarray, model: str) -> tuple[np.ndarray, list[str]]:
    """Columns: the constant, the factors, then the interactions of two factors (model interaction, quadratic) and the squares (quadratic)."""
    k = points.shape[1]
    cols, names = [np.ones(len(points))], ["1"]
    for i in range(k):
        cols.append(points[:, i]); names.append(LETTERS[i])
    if model in ("interaction", "quadratic"):
        for i, j in combinations(range(k), 2):
            cols.append(points[:, i] * points[:, j]); names.append(f"{LETTERS[i]}:{LETTERS[j]}")
    if model == "quadratic":
        for i in range(k):
            cols.append(points[:, i] ** 2); names.append(f"{LETTERS[i]}²")
    return np.column_stack(cols), names


def candidate_grid(k: int, levels: int) -> np.ndarray:
    return np.array(list(product(np.linspace(-1.0, 1.0, levels), repeat=k)))


def _clean(v: float) -> float:
    v = round(float(v), 10)
    return 0.0 if v == 0 else v


def _exchange(F: np.ndarray, n: int, rng: np.random.Generator, max_pass: int = 500) -> np.ndarray:
    """Fedorov exchange on the rows of the candidate model matrix F: swap the design point and candidate that raise det(M) most, until no swap helps."""
    c, p = F.shape
    idx = rng.choice(c, size=n, replace=n > c)
    ridge = 1e-9
    for _ in range(max_pass):
        Fd = F[idx]
        m = Fd.T @ Fd
        minv = np.linalg.inv(m + ridge * np.trace(m) / p * np.eye(p))
        dc = np.einsum("ij,jk,ik->i", F, minv, F)
        a = Fd @ minv @ F.T
        di = a[np.arange(n), idx]
        delta = (1.0 - di)[:, None] * (1.0 + dc)[None, :] + a ** 2
        i, j = np.unravel_index(int(np.argmax(delta)), delta.shape)
        if delta[i, j] <= 1.0 + 1e-9:
            break
        idx[i] = j
    return idx


def _constraint_mask(grid: np.ndarray, constraints: Sequence[Mapping] | None) -> np.ndarray:
    keep = np.ones(len(grid), dtype=bool)
    k = grid.shape[1]
    for c in constraints or []:
        coef = np.asarray(c.get("coefficients"), dtype=float)
        if coef.shape != (k,) or not np.all(np.isfinite(coef)):
            raise ValueError(f"a limit needs one coefficient for each of the {k} factors")
        limit = _f(c.get("limit"))
        op = c.get("op", "<=")
        if op not in ("<=", ">="):
            raise ValueError("the operator of a limit is <= or >=")
        value = grid @ coef
        keep &= (value <= limit + 1e-9) if op == "<=" else (value >= limit - 1e-9)
    return keep


def _criteria(X: np.ndarray, Fc: np.ndarray, names: list[str]) -> dict:
    n, p = X.shape
    m = X.T @ X
    sign, logdet = np.linalg.slogdet(m)
    if sign <= 0:
        raise ValueError("the chosen runs cannot estimate the model")
    minv = np.linalg.inv(m)
    var_c = np.einsum("ij,jk,ik->i", Fc, minv, Fc)
    d_eff = math.exp((logdet - p * math.log(n)) / p)
    corr = None
    if p > 1:
        z = X[:, 1:]
        sd = z.std(axis=0)
        ok = sd > 1e-12
        if ok.sum() > 1:
            cm = np.corrcoef(z[:, ok], rowvar=False)
            np.fill_diagonal(cm, 0.0)
            corr = float(np.abs(cm).max())
    return {"d_efficiency": float(d_eff), "log_det": float(logdet), "g_efficiency": float(min(1.0, p / (n * var_c.max()))), "max_prediction_variance": float(var_c.max()),
            "average_prediction_variance": float(var_c.mean()), "max_abs_correlation": corr,
            "term_variances": [{"term": names[j], "variance": float(minv[j, j])} for j in range(p)]}


def d_optimal(k: int, model: str = "main", n_runs: int | None = None, levels: int | None = None, constraints: Sequence[Mapping] | None = None,
              seed: int | None = None, starts: int = 30) -> dict:
    """The D-optimal design of `n_runs` runs for `model` ('main', 'interaction' or 'quadratic') in `k` coded factors, chosen from the grid of `levels` levels per factor
    (default 2 for the first two models, 3 for the quadratic one; 5 gives finer positions). `constraints` are linear limits in coded units:
    {"coefficients": [one per factor], "op": "<=" or ">=", "limit": number}. A seed makes the search repeatable and the run order random (without it: standard order)."""
    if not isinstance(k, int) or isinstance(k, bool) or not 2 <= k <= MAX_FACTORS:
        raise ValueError(f"give 2 to {MAX_FACTORS} factors")
    if model not in MODELS:
        raise ValueError(f"the model is one of {list(MODELS)}")
    levels = (3 if model == "quadratic" else 2) if levels is None else levels
    if levels not in (2, 3, 5) or (model == "quadratic" and levels == 2):
        raise ValueError("levels is 2 (not for the quadratic model), 3 or 5")
    if levels ** k > MAX_CANDIDATES:
        raise ValueError(f"{levels} levels of {k} factors are {levels ** k} candidate points: use fewer levels or factors")
    grid = candidate_grid(k, levels)
    grid = grid[_constraint_mask(grid, constraints)]
    F, names = model_matrix(grid, model)
    p = F.shape[1]
    if np.linalg.matrix_rank(F) < p:
        raise ValueError("the allowed points cannot estimate the model: relax the limits or use more levels")
    n = p + 4 if n_runs is None else n_runs
    if not isinstance(n, int) or isinstance(n, bool) or not p <= n <= MAX_RUNS:
        raise ValueError(f"the model has {p} terms: give {p} to {MAX_RUNS} runs")
    if not 1 <= starts <= 200:
        raise ValueError("give 1 to 200 starts")
    rng = np.random.default_rng(0 if seed is None else seed)
    best_idx, best_ld, hits, found = None, -math.inf, 0, 0
    for _ in range(starts):
        idx = _exchange(F, n, rng)
        sign, ld = np.linalg.slogdet(F[idx].T @ F[idx])
        if sign <= 0:
            continue
        found += 1
        if ld > best_ld + 1e-7:
            best_idx, best_ld, hits = idx, ld, 1
        elif abs(ld - best_ld) <= 1e-7:
            hits += 1
    if best_idx is None:
        raise ValueError("no start gave a design that can estimate the model: use more runs")
    pts = grid[np.sort(best_idx)] if False else grid[best_idx]
    order_key = np.lexsort(pts.T[::-1])
    pts = pts[order_key]
    X, _ = model_matrix(pts, model)
    out = {"kind": "d_optimal", "k": k, "factors": list(LETTERS[:k]), "model": model, "terms": names, "n_terms": p, "n_runs": n, "levels": levels, "n_candidates": int(len(grid)),
           "starts": starts, "starts_best": hits, "starts_valid": found, "seed": seed, "constraints": [dict(c) for c in constraints or []]}
    out.update(_criteria(X, F, names))
    ordered = np.array(pts)
    order = list(range(1, n + 1))
    if seed is not None:
        perm = [int(i) for i in np.random.default_rng(seed + 1).permutation(n)]
        ordered, order = pts[perm], [i + 1 for i in perm]
    out["runs"] = [[_clean(v) for v in row] for row in ordered]
    out["run_order"] = order
    reps: dict[tuple, int] = {}
    for row in pts:
        reps[tuple(_clean(v) for v in row)] = reps.get(tuple(_clean(v) for v in row), 0) + 1
    out["distinct_points"] = len(reps)
    out["replicated_points"] = sum(1 for v in reps.values() if v > 1)
    return out


# ------------------------------------------------------------------ mixture designs

def scheffe_matrix(x: np.ndarray, model: str) -> tuple[np.ndarray, list[str]]:
    """Scheffé polynomial without intercept: x_i, then x_i x_j (quadratic, special cubic), then x_i x_j x_k (special cubic)."""
    q = x.shape[1]
    cols, names = [x[:, i] for i in range(q)], [LETTERS[i] for i in range(q)]
    if model in ("quadratic", "special_cubic"):
        for i, j in combinations(range(q), 2):
            cols.append(x[:, i] * x[:, j]); names.append(f"{LETTERS[i]}:{LETTERS[j]}")
    if model == "special_cubic":
        for i, j, l in combinations(range(q), 3):
            cols.append(x[:, i] * x[:, j] * x[:, l]); names.append(f"{LETTERS[i]}:{LETTERS[j]}:{LETTERS[l]}")
    return np.column_stack(cols), names


def _check_q(q: int) -> None:
    if not isinstance(q, int) or isinstance(q, bool) or not 2 <= q <= MAX_COMPONENTS:
        raise ValueError(f"give 2 to {MAX_COMPONENTS} components")


def _compositions(q: int, m: int) -> np.ndarray:
    """All (a_1, ..., a_q) of non-negative whole numbers that add up to m."""
    out = []

    def rec(prefix, left, slots):
        if slots == 1:
            out.append(prefix + [left]); return
        for a in range(left + 1):
            rec(prefix + [a], left - a, slots - 1)

    rec([], m, q)
    return np.array(out, dtype=float)


def _bounds(q: int, lower, upper) -> tuple[np.ndarray, np.ndarray]:
    lo = np.zeros(q) if lower is None else np.asarray(lower, dtype=float)
    hi = np.ones(q) if upper is None else np.asarray(upper, dtype=float)
    if lo.shape != (q,) or hi.shape != (q,) or not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi))):
        raise ValueError(f"give one lower and one upper limit for each of the {q} components")
    if np.any(lo < 0) or np.any(hi > 1) or np.any(lo > hi):
        raise ValueError("the limits of a component are between 0 and 1, the lower below the upper")
    if lo.sum() >= 1 - 1e-12:
        raise ValueError("the lower limits add up to 1 or more: there is no room to blend")
    if hi.sum() < 1 - 1e-12:
        raise ValueError("the upper limits add up to less than 1: no blend is possible")
    return lo, hi


def mixture_design(q: int, kind: str = "lattice", degree: int = 2, lower: Sequence[float] | None = None, center: int = 0, axial: bool = False, seed: int | None = None) -> dict:
    """The simplex lattice {q, degree} (proportions 0, 1/m, ..., 1 in every combination that adds up to 1; (q+m-1 choose m) runs) or the simplex centroid (every blend of
    1, 2, ..., q components in equal proportions; 2^q - 1 runs). `axial` adds the points half way from the centroid to each vertex (check points inside the region),
    `center` adds repeated runs of the overall centroid (for the pure error). With `lower` limits the design is made in pseudo-components x' = (x - L) / (1 - sum L)
    and the actual proportions are x = L + (1 - sum L) x'."""
    _check_q(q)
    if kind not in ("lattice", "centroid"):
        raise ValueError("kind is lattice or centroid")
    lo, _ = _bounds(q, lower, None)
    if not isinstance(center, int) or isinstance(center, bool) or not 0 <= center <= 30:
        raise ValueError("give 0 to 30 centroid runs")
    if kind == "lattice":
        if not isinstance(degree, int) or isinstance(degree, bool) or not 1 <= degree <= 6:
            raise ValueError("the degree of a lattice is 1 to 6")
        pts = _compositions(q, degree) / degree
    else:
        rows = []
        for r in range(1, q + 1):
            for sub in combinations(range(q), r):
                row = np.zeros(q)
                row[list(sub)] = 1.0 / r
                rows.append(row)
        pts = np.array(rows)
    n_design = len(pts)
    extra = []
    if axial:
        c0 = np.full(q, 1.0 / q)
        for i in range(q):
            v = np.zeros(q); v[i] = 1.0
            extra.append((c0 + v) / 2.0)
    if axial and extra:
        pts = np.vstack([pts, np.array(extra)])
    if center:
        pts = np.vstack([pts, np.tile(np.full(q, 1.0 / q), (center, 1))])
    actual = lo + (1.0 - lo.sum()) * pts
    if seed is not None:
        perm = [int(i) for i in np.random.default_rng(seed).permutation(len(pts))]
    else:
        perm = list(range(len(pts)))
    out = {"kind": "mixture_" + kind, "q": q, "components": list(LETTERS[:q]), "degree": degree if kind == "lattice" else None, "n_runs": int(len(pts)), "n_design_points": int(n_design),
           "n_axial": len(extra) if axial else 0, "n_center": center, "lower": [float(v) for v in lo], "pseudo": [[_clean(v) for v in pts[i]] for i in perm],
           "runs": [[_clean(v) for v in actual[i]] for i in perm], "run_order": [i + 1 for i in perm], "seed": seed}
    out["max_terms"] = {"linear": q, "quadratic": q + q * (q - 1) // 2, "special_cubic": q + q * (q - 1) // 2 + q * (q - 1) * (q - 2) // 6}
    return out


def mixture_d_optimal(q: int, model: str = "quadratic", n_runs: int | None = None, lower: Sequence[float] | None = None, upper: Sequence[float] | None = None,
                      m: int | None = None, seed: int | None = None, starts: int = 30) -> dict:
    """The D-optimal blends for a Scheffé model, chosen from the lattice of proportions with step 1/m inside the limits of the components."""
    _check_q(q)
    if model not in MIXTURE_MODELS:
        raise ValueError(f"the model is one of {list(MIXTURE_MODELS)}")
    lo, hi = _bounds(q, lower, upper)
    if m is None:
        m = {2: 20, 3: 12, 4: 10, 5: 8, 6: 6, 7: 5, 8: 4}[q]
    if not isinstance(m, int) or isinstance(m, bool) or not 2 <= m <= 40:
        raise ValueError("the lattice step is 1/m with m from 2 to 40")
    if math.comb(q + m - 1, m) > 200000:
        raise ValueError("this lattice has too many points: use a smaller m")
    grid = _compositions(q, m) / m
    grid = grid[np.all(grid >= lo - 1e-9, axis=1) & np.all(grid <= hi + 1e-9, axis=1)]
    if len(grid) > MAX_CANDIDATES:
        raise ValueError(f"{len(grid)} candidate blends: use a smaller m")
    F, names = scheffe_matrix(grid, model)
    p = F.shape[1]
    if len(grid) < p or np.linalg.matrix_rank(F) < p:
        raise ValueError("the allowed blends cannot estimate the model: widen the limits or use a finer lattice (larger m)")
    n = p + 2 if n_runs is None else n_runs
    if not isinstance(n, int) or isinstance(n, bool) or not p <= n <= MAX_RUNS:
        raise ValueError(f"the model has {p} terms: give {p} to {MAX_RUNS} runs")
    if not 1 <= starts <= 200:
        raise ValueError("give 1 to 200 starts")
    rng = np.random.default_rng(0 if seed is None else seed)
    best_idx, best_ld, hits, found = None, -math.inf, 0, 0
    for _ in range(starts):
        idx = _exchange(F, n, rng)
        sign, ld = np.linalg.slogdet(F[idx].T @ F[idx])
        if sign <= 0:
            continue
        found += 1
        if ld > best_ld + 1e-7:
            best_idx, best_ld, hits = idx, ld, 1
        elif abs(ld - best_ld) <= 1e-7:
            hits += 1
    if best_idx is None:
        raise ValueError("no start gave a design that can estimate the model: use more runs")
    pts = grid[best_idx]
    pts = pts[np.lexsort(pts.T[::-1])]
    X, _ = scheffe_matrix(pts, model)
    crit = _criteria(X, F, names)
    perm = list(range(n)) if seed is None else [int(i) for i in np.random.default_rng(seed + 1).permutation(n)]
    reps: dict[tuple, int] = {}
    for row in pts:
        reps[tuple(_clean(v) for v in row)] = reps.get(tuple(_clean(v) for v in row), 0) + 1
    return {"kind": "mixture_d_optimal", "q": q, "components": list(LETTERS[:q]), "model": model, "terms": names, "n_terms": p, "n_runs": n, "lattice_m": m, "n_candidates": int(len(grid)),
            "lower": [float(v) for v in lo], "upper": [float(v) for v in hi], "starts": starts, "starts_best": hits, "starts_valid": found, "seed": seed,
            "runs": [[_clean(v) for v in pts[i]] for i in perm], "run_order": [i + 1 for i in perm], "distinct_points": len(reps), "replicated_points": sum(1 for v in reps.values() if v > 1), **crit}


# ------------------------------------------------------------------ analysis of a mixture experiment

def _fit(X: np.ndarray, y: np.ndarray):
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    r = y - X @ b
    return b, float(r @ r)


def mixture(y: Sequence[float], components: Mapping[str, Sequence[float]], model: str = "quadratic", alpha: float = 0.05) -> dict:
    """Scheffé polynomial fitted by least squares. Proportions add up to 1 (or to 100: they are divided by 100). The total sum of squares is about the mean and the model has p - 1
    degrees of freedom, because the components add up to a constant. Returns the coefficients with t and p, the analysis of variance (the sequential gain of the quadratic and
    special cubic terms), lack of fit against repeated blends, R², adjusted and predicted R², and the best blends found on a fine lattice inside the observed range of every component
    (no extrapolation beyond the blends that were run)."""
    if model not in MIXTURE_MODELS:
        raise ValueError(f"the model is one of {list(MIXTURE_MODELS)}")
    if not 0 < alpha < 0.5:
        raise ValueError("alpha must be between 0 and 0.5")
    yv = np.asarray(y, dtype=float).ravel()
    names = list(components)
    q = len(names)
    _check_q(q)
    cols = [np.asarray(components[nm], dtype=float).ravel() for nm in names]
    if any(c.shape != yv.shape for c in cols):
        raise ValueError(f"every component needs one value per run ({yv.size})")
    x = np.column_stack(cols)
    if not (np.all(np.isfinite(yv)) and np.all(np.isfinite(x))):
        raise ValueError("values must be finite numbers")
    total = x.sum(axis=1)
    if np.all(np.abs(total - 100.0) <= 1e-4 * 100.0):
        x = x / 100.0
    elif not np.all(np.abs(total - 1.0) <= 1e-6):
        raise ValueError("the components of every run must add up to 1 (or to 100)")
    if np.any(x < -1e-12):
        raise ValueError("a proportion cannot be negative")
    n = yv.size
    X, labels = scheffe_matrix(x, model)
    p = X.shape[1]
    dfe = n - p
    if dfe < 1:
        raise ValueError(f"{n} runs are too few for the {p} terms of the {model.replace('_', ' ')} model in {q} components: at least {p + 1} are needed")
    if np.linalg.matrix_rank(X) < p:
        raise ValueError("the blends cannot estimate this model (some terms cannot be told apart): use a simpler model or more different blends")
    sst = float(((yv - yv.mean()) ** 2).sum())
    if not sst > 0:
        raise ValueError("the response has no variation")
    beta, sse = _fit(X, yv)
    mse = sse / dfe
    if mse < 1e-24 * sst:
        raise ValueError("the model fits the data exactly: there is no scatter to test the terms with")
    xtx_inv = np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(mse * xtx_inv))
    t = beta / se
    pv = 2 * stats.t.sf(np.abs(t), dfe)
    tq = float(stats.t.isf(alpha / 2, dfe))
    terms = []
    for j in range(p):
        order = labels[j].count(":") + 1
        d = {"term": labels[j], "order": order, "coefficient": float(beta[j]), "se": float(se[j]), "t": float(t[j]), "p_value": float(pv[j]), "significant": bool(pv[j] < alpha),
             "ci": [float(beta[j] - tq * se[j]), float(beta[j] + tq * se[j])]}
        if order == 2:
            d["blending"] = "synergistic" if beta[j] > 0 else "antagonistic"  # raises or lowers the response above the average of the pure components
        terms.append(d)
    f_model = ((sst - sse) / (p - 1)) / mse
    anova = [{"source": "model", "df": p - 1, "ss": float(sst - sse), "ms": float((sst - sse) / (p - 1)), "f": float(f_model), "p_value": float(stats.f.sf(f_model, p - 1, dfe))}]
    order_models = list(MIXTURE_MODELS[: MIXTURE_MODELS.index(model) + 1])
    prev_sse, prev_p = sst, 1
    for mdl in order_models:
        Xm, _ = scheffe_matrix(x, mdl)
        pm = Xm.shape[1]
        if np.linalg.matrix_rank(Xm) < pm:
            break
        _, sse_m = _fit(Xm, yv)
        gain, df_gain = prev_sse - sse_m, pm - prev_p
        if df_gain > 0:
            fr = (gain / df_gain) / mse
            anova.append({"source": mdl, "df": df_gain, "ss": float(gain), "ms": float(gain / df_gain), "f": float(fr), "p_value": float(stats.f.sf(fr, df_gain, dfe))})
        prev_sse, prev_p = sse_m, pm
    anova.append({"source": "residual", "df": dfe, "ss": float(sse), "ms": float(mse), "f": None, "p_value": None})
    pts: dict[tuple, list[int]] = {}
    for i in range(n):
        pts.setdefault(tuple(round(float(v), 9) for v in x[i]), []).append(i)
    df_pe, lack = n - len(pts), None
    if df_pe >= 1 and len(pts) > p:
        sspe = float(sum(((yv[idx] - yv[idx].mean()) ** 2).sum() for idx in pts.values() if len(idx) > 1))
        sslof, df_lof = sse - sspe, dfe - df_pe
        if sspe > 0 and df_lof >= 1:
            f_lof = (sslof / df_lof) / (sspe / df_pe)
            lack = {"ss_lack": float(sslof), "df_lack": df_lof, "ss_pure": sspe, "df_pure": df_pe, "f": float(f_lof), "p_value": float(stats.f.sf(f_lof, df_lof, df_pe))}
    h = np.einsum("ij,jk,ik->i", X, xtx_inv, X)
    res = yv - X @ beta
    press = float(((res / (1 - h)) ** 2).sum()) if np.all(h < 1 - 1e-9) else None
    # the best blends inside the observed range of every component
    lo_obs, hi_obs = x.min(axis=0), x.max(axis=0)
    m = {2: 100, 3: 40, 4: 20, 5: 12, 6: 8, 7: 6, 8: 5}[q]
    grid = _compositions(q, m) / m
    grid = grid[np.all(grid >= lo_obs - 1e-9, axis=1) & np.all(grid <= hi_obs + 1e-9, axis=1)]
    best = None
    if len(grid):
        pred = scheffe_matrix(grid, model)[0] @ beta
        imax, imin = int(np.argmax(pred)), int(np.argmin(pred))
        best = {"lattice_m": m, "maximum": {"blend": {names[i]: float(grid[imax][i]) for i in range(q)}, "response": float(pred[imax])},
                "minimum": {"blend": {names[i]: float(grid[imin][i]) for i in range(q)}, "response": float(pred[imin])}}
    centroid = np.full((1, q), 1.0 / q)
    warnings = []
    if lack is not None and lack["p_value"] < alpha:
        warnings.append({"code": "doe_mixture_lack_of_fit", "p": lack["p_value"]})
    if dfe < 5:
        warnings.append({"code": "doe_mixture_few_df", "df": dfe})
    return {"n": n, "q": q, "components": names, "model": model, "alpha": alpha, "terms": terms, "anova": anova, "lack_of_fit": lack, "df_error": dfe, "sigma": float(math.sqrt(mse)),
            "r2": float(1 - sse / sst), "r2_adjusted": float(1 - (sse / dfe) / (sst / (n - 1))), "r2_predicted": float(1 - press / sst) if press is not None else None, "press": press,
            "centroid_response": float((scheffe_matrix(centroid, model)[0] @ beta)[0]), "best": best, "warnings": warnings}

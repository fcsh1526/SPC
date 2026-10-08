"""Process characterization (AIAG-VDA SPC draft 6.4): the relationship between process parameters and product characteristics by regression or by designed experiments,
to find the primary and secondary control factors that the out-of-control action plan adjusts (6.6). The draft names DoE and regression and gives no method; the standard
ones are used here: multiple linear regression by least squares, the analysis of a full two-level factorial design (effects, analysis of variance, Lenth's method when
there is no replication), of a regular two-level fraction (the effects are confounded in aliases; the defining relation and the resolution are read from the data), and of a
second-order response surface (quadratic model, analysis of variance with lack of fit, stationary point and its nature).

The plans of the standard designs are made here too: full and fractional two-level designs (generators, defining relation, resolution, aliases), the central composite
design and the Box-Behnken design, with the run order randomised on request. The program does not decide which design suits the problem.
"""

from __future__ import annotations

import math
from itertools import combinations, product
from typing import Mapping, Sequence

import numpy as np
from scipy import stats

MAX_FACTORS_REGRESSION = 15
MAX_FACTORS_FACTORIAL = 5
MAX_FACTORS_FRACTION = 8
MAX_FACTORS_SURFACE = 6


def _f(x):
    return None if x is None or not math.isfinite(float(x)) else float(x)


def regression(y: Sequence[float], factors: Mapping[str, Sequence[float]], alpha: float = 0.05) -> dict:
    """Multiple linear regression y = b0 + b1 x1 + ... with an intercept. For each factor: the coefficient, its standard error, t, p, the interval, the standardised
    coefficient (the effect of one standard deviation of the factor in standard deviations of y), the partial R-squared and the variance inflation factor (VIF)."""
    yv = np.asarray(y, dtype=float).ravel()
    names = list(factors)
    k = len(names)
    if not 1 <= k <= MAX_FACTORS_REGRESSION:
        raise ValueError(f"give 1 to {MAX_FACTORS_REGRESSION} factors")
    if not 0 < alpha < 0.5:
        raise ValueError("alpha must be between 0 and 0.5")
    cols = []
    for nm in names:
        c = np.asarray(factors[nm], dtype=float).ravel()
        if c.shape != yv.shape:
            raise ValueError(f"factor {nm!r} must have one value per run ({yv.size})")
        cols.append(c)
    n = yv.size
    if not (np.all(np.isfinite(yv)) and all(np.all(np.isfinite(c)) for c in cols)):
        raise ValueError("values must be finite numbers")
    dfe = n - k - 1
    if dfe < 2:
        raise ValueError(f"{n} runs are too few for {k} factors: at least {k + 3} are needed")
    X = np.column_stack([np.ones(n), *cols])
    if np.linalg.matrix_rank(X) < k + 1:
        raise ValueError("a factor is constant or a linear function of the others")
    beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
    resid = yv - X @ beta
    sse = float(resid @ resid)
    sst = float(((yv - yv.mean()) ** 2).sum())
    if not sst > 0:
        raise ValueError("the response has no variation")
    mse = sse / dfe
    cov = mse * np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    t = beta / se
    p = 2 * stats.t.sf(np.abs(t), dfe)
    tq = float(stats.t.isf(alpha / 2, dfe))
    sy = float(yv.std(ddof=1))
    terms = []
    for j, nm in enumerate(names, start=1):
        others = np.column_stack([np.ones(n), *[cols[i] for i in range(k) if i != j - 1]])
        r2j = 0.0
        if k > 1:
            fit = np.linalg.lstsq(others, cols[j - 1], rcond=None)[0]
            res = cols[j - 1] - others @ fit
            tot = float(((cols[j - 1] - cols[j - 1].mean()) ** 2).sum())
            r2j = 1.0 - float(res @ res) / tot if tot > 0 else 0.0
        vif = 1.0 / (1.0 - r2j) if r2j < 1 - 1e-12 else float("inf")
        terms.append({"factor": nm, "coefficient": float(beta[j]), "se": float(se[j]), "t": float(t[j]), "p_value": float(p[j]), "significant": bool(p[j] < alpha),
                      "ci": [float(beta[j] - tq * se[j]), float(beta[j] + tq * se[j])], "standardised": float(beta[j] * cols[j - 1].std(ddof=1) / sy),
                      "partial_r2": float(t[j] ** 2 / (t[j] ** 2 + dfe)), "vif": _f(vif)})
    ranked = sorted(range(k), key=lambda i: -abs(terms[i]["t"]))
    for rank, i in enumerate(ranked, start=1):
        terms[i]["rank"] = rank
    r2 = 1.0 - sse / sst
    f_stat = ((sst - sse) / k) / mse
    warnings = []
    if any(tm["vif"] is None or tm["vif"] > 10 for tm in terms):
        vifs = [tm["vif"] for tm in terms if tm["vif"] is not None]
        warnings.append({"code": "doe_collinear", "vif": max(vifs) if vifs and len(vifs) == len(terms) else None})
    if n < 5 * k:
        warnings.append({"code": "doe_few_runs", "n": n, "k": k})
    return {"n": n, "k": k, "alpha": alpha, "intercept": {"coefficient": float(beta[0]), "se": float(se[0]), "t": float(t[0]), "p_value": float(p[0]),
                                                           "ci": [float(beta[0] - tq * se[0]), float(beta[0] + tq * se[0])]}, "terms": terms,
            "r2": float(r2), "r2_adjusted": float(1.0 - (sse / dfe) / (sst / (n - 1))), "f": float(f_stat), "df_model": k, "df_error": dfe,
            "p_model": float(stats.f.sf(f_stat, k, dfe)), "sigma": float(math.sqrt(mse)), "warnings": warnings}


def _code(levels: Sequence) -> tuple[np.ndarray, list]:
    """The two levels of a factor as -1 (the lower or the first in order) and +1."""
    vals = list(levels)
    try:
        uniq = sorted({float(v) for v in vals})
        key = float
    except (TypeError, ValueError):
        uniq = sorted({str(v) for v in vals})
        key = str
    if len(uniq) != 2:
        raise ValueError(f"a two-level factor has exactly 2 levels (it has {len(uniq)})")
    return np.array([-1.0 if key(v) == uniq[0] else 1.0 for v in vals]), uniq


def _judge_effects(terms: list, yv: np.ndarray, cells: list, r: int, alpha: float, sst: float, out: dict) -> None:
    """Judge the effects of a two-level design: with replicates by the pure error (F test), without by Lenth's pseudo standard error."""
    n = yv.size
    if r >= 2:
        sse = float(sum(((yv[idx] - yv[idx].mean()) ** 2).sum() for idx in cells))
        dfe = n - len(cells)
        mse = sse / dfe
        if not mse > 0:
            raise ValueError("the replicates do not vary: there is no pure error to test against")
        for tm in terms:
            f_stat = tm["ss"] / mse
            tm.update(f=float(f_stat), p_value=float(stats.f.sf(f_stat, 1, dfe)), contribution=float(tm["ss"] / sst) if sst > 0 else None)
            tm["significant"] = bool(tm["p_value"] < alpha)
        out.update(sse=sse, df_error=dfe, mse=mse, method="anova")
    else:
        eff = np.array([tm["effect"] for tm in terms])
        m = eff.size
        s0 = 1.5 * float(np.median(np.abs(eff)))
        trimmed = np.abs(eff)[np.abs(eff) < 2.5 * s0] if s0 > 0 else np.abs(eff)
        pse = 1.5 * float(np.median(trimmed)) if trimmed.size else 0.0
        if not pse > 0:
            raise ValueError("the effects do not vary enough for Lenth's method (most are exactly 0)")
        d = m / 3.0
        me = float(stats.t.isf(alpha / 2, d) * pse)
        gamma = (1 + (1 - alpha) ** (1.0 / m)) / 2
        sme = float(stats.t.ppf(gamma, d) * pse)
        for tm in terms:
            tm.update(contribution=float(tm["ss"] / sst) if sst > 0 else None, significant=bool(abs(tm["effect"]) > me), strongly_significant=bool(abs(tm["effect"]) > sme))
        out.update(pse=pse, me=me, sme=sme, method="lenth", df_lenth=d, warnings=[{"code": "doe_unreplicated"}])


def factorial(y: Sequence[float], factors: Mapping[str, Sequence], alpha: float = 0.05, max_order: int | None = None) -> dict:
    """Full two-level factorial 2^k with the same number r of runs for each combination (r = 1: unreplicated). Effects are the mean at the high level minus the mean at the low level.
    With replication each effect is tested against the pure error (analysis of variance, F with 1 and N - 2^k degrees of freedom). Without it Lenth's pseudo standard error
    gives the margin of error ME and the simultaneous margin SME (Lenth 1989): effects beyond them are active."""
    yv = np.asarray(y, dtype=float).ravel()
    names = list(factors)
    k = len(names)
    if not 2 <= k <= MAX_FACTORS_FACTORIAL:
        raise ValueError(f"a factorial design here has 2 to {MAX_FACTORS_FACTORIAL} factors")
    if not np.all(np.isfinite(yv)):
        raise ValueError("values must be finite numbers")
    codes, levels = {}, {}
    for nm in names:
        if len(factors[nm]) != yv.size:
            raise ValueError(f"factor {nm!r} must have one level per run ({yv.size})")
        codes[nm], levels[nm] = _code(factors[nm])
    n = yv.size
    cells: dict[tuple, list[int]] = {}
    for i in range(n):
        cells.setdefault(tuple(codes[nm][i] for nm in names), []).append(i)
    if len(cells) != 2 ** k:
        raise ValueError(f"the design is not a full factorial: {len(cells)} of the {2 ** k} combinations are present")
    reps = {len(v) for v in cells.values()}
    if len(reps) != 1:
        raise ValueError("every combination needs the same number of runs")
    r = reps.pop()
    order = k if max_order is None else max_order
    terms = []
    for size in range(1, order + 1):
        for combo in combinations(names, size):
            sign = np.prod([codes[nm] for nm in combo], axis=0)
            contrast = float(sign @ yv)
            effect = contrast / (n / 2.0)
            terms.append({"term": ":".join(combo), "order": size, "effect": effect, "coefficient": effect / 2.0, "ss": contrast ** 2 / n})
    sst = float(((yv - yv.mean()) ** 2).sum())
    out: dict = {"n": n, "k": k, "replicates": r, "levels": levels, "alpha": alpha, "mean": float(yv.mean()), "sst": sst}
    _judge_effects(terms, yv, list(cells.values()), r, alpha, sst, out)
    out["terms"] = sorted(terms, key=lambda tm: -abs(tm["effect"]))
    out.setdefault("warnings", [])
    return out


# ------------------------------------------------------------------ plans of designs

LETTERS = "ABCDEFGHIJK"
ROMAN = {2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X", 11: "XI"}
# Generators of the usual fractions of highest resolution (Box, Hunter and Hunter; Montgomery table 8.14). The resolution is not taken from a table: it is worked out from them.
DEFAULT_GENERATORS: dict[tuple[int, int], list[str]] = {
    (3, 1): ["C=AB"], (4, 1): ["D=ABC"], (5, 1): ["E=ABCD"], (5, 2): ["D=AB", "E=AC"], (6, 1): ["F=ABCDE"], (6, 2): ["E=ABC", "F=BCD"],
    (6, 3): ["D=AB", "E=AC", "F=BC"], (7, 1): ["G=ABCDEF"], (7, 2): ["F=ABCD", "G=ABDE"], (7, 3): ["E=ABC", "F=BCD", "G=ACD"],
    (7, 4): ["D=AB", "E=AC", "F=BC", "G=ABC"], (8, 4): ["E=BCD", "F=ACD", "G=ABC", "H=ABD"],
}


def _parse_generators(k: int, generators: Sequence[str]) -> list[tuple[str, frozenset, int]]:
    """'D=ABC' or 'D=-ABC': the letter, the base letters it is the product of, and the sign."""
    p = len(generators)
    base = LETTERS[: k - p]
    out, seen = [], set()
    for g in generators:
        text = str(g).replace(" ", "").upper()
        if text.count("=") != 1:
            raise ValueError(f"a generator is written like D=ABC (got {g!r})")
        left, right = text.split("=")
        sign = -1 if right.startswith("-") else 1
        right = right.lstrip("+-")
        if len(left) != 1 or left not in LETTERS[:k] or left in base or left in seen:
            raise ValueError(f"generator {g!r}: the letter on the left must be one of the {p} added factors {LETTERS[k - p:k]}, each once")
        if len(right) < 2 or len(set(right)) != len(right) or any(c not in base for c in right):
            raise ValueError(f"generator {g!r}: the right side is a product of two or more different base factors ({base})")
        seen.add(left)
        out.append((left, frozenset(right), sign))
    return out


def _words(gens: list[tuple[str, frozenset, int]]) -> list[tuple[frozenset, int]]:
    """All words of the defining relation (I = word): every product of the generator words, with its sign."""
    basic = [(frozenset(g[1] | {g[0]}), g[2]) for g in gens]  # D=sABC  gives  I = sABCD (multiply both sides by D)
    words: dict[frozenset, int] = {}
    for mask in range(1, 2 ** len(basic)):
        w, sign = frozenset(), 1
        for i, (bw, bs) in enumerate(basic):
            if mask >> i & 1:
                w, sign = w ^ bw, sign * bs
        words[w] = sign
    return sorted(words.items(), key=lambda kv: (len(kv[0]), "".join(sorted(kv[0]))))


def resolution_of(words: list[tuple[frozenset, int]]) -> int | None:
    return min((len(w) for w, _ in words), default=None)


def _yates(m: int) -> np.ndarray:
    """The 2^m runs in standard order (the first factor changes fastest) as -1/+1."""
    return np.array([[(-1.0 if (i >> j) & 1 == 0 else 1.0) for j in range(m)] for i in range(2 ** m)])


def _order(runs: np.ndarray, seed: int | None) -> tuple[np.ndarray, list[int]]:
    idx = list(range(len(runs)))
    if seed is not None:
        idx = [int(i) for i in np.random.default_rng(seed).permutation(len(runs))]
    return runs[idx], [i + 1 for i in idx]


def fractional_design(k: int, generators: Sequence[str] | None = None, p: int | None = None, max_order: int = 3, seed: int | None = None) -> dict:
    """A regular two-level design 2^(k-p) (p = 0: the full factorial). Factors are the letters A, B, ...; the first k-p are the base factors (all combinations), the others are
    products of base factors (the generators). Returns the runs (-1/+1), the defining relation, the resolution and the aliases (terms up to `max_order` that cannot be told apart)."""
    if not 2 <= k <= MAX_FACTORS_FRACTION:
        raise ValueError(f"a design here has 2 to {MAX_FACTORS_FRACTION} factors")
    if generators:
        gens = _parse_generators(k, list(generators))
    else:
        pp = 0 if p is None and generators is not None else (1 if p is None else p)
        if pp == 0:
            gens = []
        elif (k, pp) in DEFAULT_GENERATORS:
            gens = _parse_generators(k, DEFAULT_GENERATORS[(k, pp)])
        else:
            raise ValueError(f"no standard fraction 2^({k}-{pp}) is known: give the generators")
    p = len(gens)
    if k - p < 2:
        raise ValueError("a design needs at least 2 base factors")
    base = _yates(k - p)
    cols = {LETTERS[i]: base[:, i] for i in range(k - p)}
    for letter, word, sign in gens:
        cols[letter] = sign * np.prod([cols[c] for c in sorted(word)], axis=0)
    names = list(LETTERS[:k])
    runs = np.column_stack([cols[c] for c in names])
    words = _words(gens)
    res = resolution_of(words)
    # alias classes: terms are equal when their product is a word of the defining relation
    classes, done = [], set()
    for size in range(1, k + 1):
        for combo in combinations(names, size):
            t = frozenset(combo)
            if t in done:
                continue
            members = [(t, 1)] + [(t ^ w, sg) for w, sg in words]
            for m, _ in members:
                done.add(m)
            if any(not m for m, _ in members):  # the term is a word of the defining relation: it is the identity, not a contrast
                continue
            shown = [(m, sg) for m, sg in sorted(members, key=lambda x: (len(x[0]), "".join(sorted(x[0])))) if len(m) <= max_order]
            classes.append({"terms": [("-" if sg < 0 else "") + "".join(sorted(m)) for m, sg in shown] if shown else ["".join(sorted(t))]})
    ordered, order = _order(runs, seed)
    return {"kind": "fractional" if p else "full", "k": k, "p": p, "n_runs": int(len(runs)), "factors": names, "generators": [f"{g[0]}={'-' if g[2] < 0 else ''}{''.join(sorted(g[1]))}" for g in gens],
            "defining_relation": [("-" if sg < 0 else "") + "".join(sorted(w)) for w, sg in words], "resolution": ROMAN.get(res, str(res)) if res else None,
            "resolution_number": res, "aliases": classes, "runs": ordered.astype(int).tolist(), "run_order": order, "seed": seed}


def central_composite(k: int, axial: str = "rotatable", center: int = 5, seed: int | None = None) -> dict:
    """Central composite design: the corners of the cube (full 2^k, or a half fraction of resolution V for 5 and 6 factors), 2k axial points at distance alpha, and centre runs.
    alpha = F^(1/4) for the rotatable design (F = number of cube runs), 1 for the face-centred one, sqrt(k) for the spherical one."""
    if not 2 <= k <= MAX_FACTORS_SURFACE:
        raise ValueError(f"a central composite design here has 2 to {MAX_FACTORS_SURFACE} factors")
    if axial not in ("rotatable", "face", "spherical"):
        raise ValueError("axial must be rotatable, face or spherical")
    if not 1 <= center <= 30:
        raise ValueError("give 1 to 30 centre runs")
    cube = fractional_design(k, p=1 if k >= 5 else 0)
    cube_runs = np.array(cube["runs"], dtype=float)
    f = len(cube_runs)
    alpha = {"rotatable": f ** 0.25, "face": 1.0, "spherical": math.sqrt(k)}[axial]
    ax = []
    for i in range(k):
        for sgn in (-1.0, 1.0):
            row = np.zeros(k)
            row[i] = sgn * alpha
            ax.append(row)
    runs = np.vstack([cube_runs, np.array(ax), np.zeros((center, k))])
    ordered, order = _order(runs, seed)
    return {"kind": "central_composite", "k": k, "factors": list(LETTERS[:k]), "axial": axial, "alpha": float(alpha), "n_cube": f, "n_axial": 2 * k, "n_center": center,
            "n_runs": int(len(runs)), "runs": ordered.tolist(), "run_order": order, "seed": seed}


def box_behnken(k: int, center: int = 3, seed: int | None = None) -> dict:
    """Box-Behnken design for 3 or 4 factors: every pair of factors in a 2^2 design with the others at the middle level, plus centre runs. No run is at a corner of the cube."""
    if k not in (3, 4):
        raise ValueError("a Box-Behnken design here has 3 or 4 factors")
    if not 1 <= center <= 30:
        raise ValueError("give 1 to 30 centre runs")
    rows = []
    for i, j in combinations(range(k), 2):
        for a, b in product((-1.0, 1.0), repeat=2):
            row = [0.0] * k
            row[i], row[j] = a, b
            rows.append(row)
    runs = np.array(rows + [[0.0] * k] * center)
    ordered, order = _order(runs, seed)
    return {"kind": "box_behnken", "k": k, "factors": list(LETTERS[:k]), "n_center": center, "n_runs": int(len(runs)), "runs": ordered.astype(int).tolist(), "run_order": order, "seed": seed}


# ------------------------------------------------------------------ analysis of a fraction

def fractional(y: Sequence[float], factors: Mapping[str, Sequence], alpha: float = 0.05, max_order: int = 3) -> dict:
    """Analyse a regular two-level fraction (also a full factorial), with the same number of runs for each combination. The effects that cannot be told apart are one contrast: it is
    named by its lowest-order term and lists its aliases up to `max_order`. The defining relation and the resolution are found from the data. Judged like `factorial`: with replicates by
    the pure error, without by Lenth's method (which has little power with fewer than about 15 contrasts)."""
    yv = np.asarray(y, dtype=float).ravel()
    names = list(factors)
    k = len(names)
    if not 3 <= k <= MAX_FACTORS_FRACTION:
        raise ValueError(f"a fraction here has 3 to {MAX_FACTORS_FRACTION} factors")
    if not np.all(np.isfinite(yv)):
        raise ValueError("values must be finite numbers")
    codes, levels = {}, {}
    for nm in names:
        if len(factors[nm]) != yv.size:
            raise ValueError(f"factor {nm!r} must have one level per run ({yv.size})")
        codes[nm], levels[nm] = _code(factors[nm])
    n = yv.size
    cells: dict[tuple, list[int]] = {}
    for i in range(n):
        cells.setdefault(tuple(codes[nm][i] for nm in names), []).append(i)
    m = len(cells)
    q = int(round(math.log2(m))) if m > 1 else 0
    if 2 ** q != m or q < 2 or q > k:
        raise ValueError(f"{m} different combinations are not a regular two-level fraction (a power of two, at least 4, is needed)")
    reps = {len(v) for v in cells.values()}
    if len(reps) != 1:
        raise ValueError("every combination needs the same number of runs")
    r = reps.pop()
    keys = list(cells)
    mat = np.array(keys)  # m x k, the distinct runs
    if not np.allclose(mat.sum(axis=0), 0):
        raise ValueError("a factor is not balanced over the combinations: this is not a regular fraction")
    # contrast columns of all terms; terms with the same column (up to sign) are aliases
    groups: dict[tuple, list[tuple[int, str, int]]] = {}
    identity: list[tuple[int, str, int]] = []
    for size in range(1, k + 1):
        for combo in combinations(range(k), size):
            col = np.prod(mat[:, list(combo)], axis=1)
            label = ":".join(names[i] for i in combo)
            if np.all(col == col[0]):  # constant: a word of the defining relation
                identity.append((size, label, int(col[0])))
                continue
            sgn = int(col[0])
            groups.setdefault(tuple((col * sgn).astype(int)), []).append((size, label, sgn))
    if len(groups) != m - 1:
        raise ValueError("the combinations are not a regular two-level fraction (the contrasts are not orthogonal)")
    terms = []
    for key, members in groups.items():
        members.sort(key=lambda t: (t[0], t[1]))
        lead = members[0]
        col_all = np.array([codes[nm] for nm in names])  # k x n
        idx = [names.index(x) for x in lead[1].split(":")]
        sign = np.prod(col_all[idx, :], axis=0)
        contrast = float(sign @ yv)
        effect = contrast / (n / 2.0)
        shown = [t for t in members[1:] if t[0] <= max_order]
        terms.append({"term": lead[1], "order": lead[0], "effect": effect, "coefficient": effect / 2.0, "ss": contrast ** 2 / n,
                      "aliases": [("-" if t[2] * lead[2] < 0 else "") + t[1] for t in shown]})
    sst = float(((yv - yv.mean()) ** 2).sum())
    words = sorted(identity, key=lambda t: (t[0], t[1]))
    res = min((w[0] for w in words), default=None)
    out: dict = {"n": n, "k": k, "replicates": r, "n_combinations": m, "levels": levels, "alpha": alpha, "mean": float(yv.mean()), "sst": sst, "fraction": f"2^({k}-{k - q})" if q < k else f"2^{k}",
                 "defining_relation": [("-" if w[2] < 0 else "") + w[1] for w in words], "resolution": ROMAN.get(res, str(res)) if res else None, "resolution_number": res}
    warns = []
    if r == 1 and len(terms) < 15:  # Lenth's method is made for about 15 contrasts (a 2^4); with 7 it has little power
        warns.append({"code": "doe_few_contrasts", "n": len(terms)})
    _judge_effects(terms, yv, list(cells.values()), r, alpha, sst, out)
    out["warnings"] = warns + out.get("warnings", [])
    out["terms"] = sorted(terms, key=lambda tm: -abs(tm["effect"]))
    return out


# ------------------------------------------------------------------ response surface

def _quadratic_columns(cols: list[np.ndarray], names: list[str]) -> tuple[np.ndarray, list[str], list[str]]:
    """Intercept, linear, squared and cross-product columns, with their names and kinds."""
    k = len(cols)
    parts, labels, kinds = [np.ones_like(cols[0])], ["1"], ["intercept"]
    for i in range(k):
        parts.append(cols[i]); labels.append(names[i]); kinds.append("linear")
    for i in range(k):
        parts.append(cols[i] ** 2); labels.append(f"{names[i]}²"); kinds.append("square")
    for i, j in combinations(range(k), 2):
        parts.append(cols[i] * cols[j]); labels.append(f"{names[i]}:{names[j]}"); kinds.append("cross")
    return np.column_stack(parts), labels, kinds


def response_surface(y: Sequence[float], factors: Mapping[str, Sequence[float]], alpha: float = 0.05) -> dict:
    """Second-order model y = b0 + sum bi xi + sum bii xi^2 + sum bij xi xj by least squares. Give the factors in coded units (for example -1, 0, +1): the stationary point is in the units given.
    Returns the coefficients with t and p, the analysis of variance with the sequential sums of squares of the linear, square and cross terms, lack of fit against the pure error
    (when points are repeated), R², adjusted and predicted R² (PRESS), and the canonical analysis: the stationary point, the fitted response there and the eigenvalues of the
    matrix of second-order coefficients (all negative: maximum, all positive: minimum, mixed: saddle)."""
    yv = np.asarray(y, dtype=float).ravel()
    names = list(factors)
    k = len(names)
    if not 1 <= k <= MAX_FACTORS_SURFACE:
        raise ValueError(f"give 1 to {MAX_FACTORS_SURFACE} factors")
    cols = []
    for nm in names:
        c = np.asarray(factors[nm], dtype=float).ravel()
        if c.shape != yv.shape:
            raise ValueError(f"factor {nm!r} must have one value per run ({yv.size})")
        cols.append(c)
    if not (np.all(np.isfinite(yv)) and all(np.all(np.isfinite(c)) for c in cols)):
        raise ValueError("values must be finite numbers")
    if not 0 < alpha < 0.5:
        raise ValueError("alpha must be between 0 and 0.5")
    n = yv.size
    X, labels, kinds = _quadratic_columns(cols, names)
    pn = X.shape[1]
    dfe = n - pn
    if dfe < 1:
        raise ValueError(f"{n} runs are too few for the {pn} terms of a second-order model in {k} factors: at least {pn + 1} are needed")
    if np.linalg.matrix_rank(X) < pn:
        raise ValueError("the design cannot estimate the second-order model (some terms cannot be told apart: use at least 3 levels of each factor)")
    sst = float(((yv - yv.mean()) ** 2).sum())
    if not sst > 0:
        raise ValueError("the response has no variation")

    def fit(Xm):
        b, *_ = np.linalg.lstsq(Xm, yv, rcond=None)
        res = yv - Xm @ b
        return b, float(res @ res)

    beta, sse = fit(X)
    mse = sse / dfe
    if mse < 1e-24 * sst:
        raise ValueError("the model fits the data exactly: there is no scatter to test the terms with")
    xtx_inv = np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(mse * xtx_inv))
    t = beta / se
    p = 2 * stats.t.sf(np.abs(t), dfe)
    tq = float(stats.t.isf(alpha / 2, dfe))
    coef = [{"term": labels[j], "kind": kinds[j], "coefficient": float(beta[j]), "se": float(se[j]), "t": float(t[j]), "p_value": float(p[j]), "significant": bool(p[j] < alpha),
             "ci": [float(beta[j] - tq * se[j]), float(beta[j] + tq * se[j])]} for j in range(pn)]
    # sequential sums of squares: linear, then squares, then cross products
    n_lin, n_sq = 1 + k, 1 + 2 * k
    sse_const = sst
    _, sse_lin = fit(X[:, :n_lin])
    _, sse_sq = fit(X[:, :n_sq])
    ss_lin, ss_sq, ss_cross = sse_const - sse_lin, sse_lin - sse_sq, sse_sq - sse
    df_cross = k * (k - 1) // 2
    anova = []

    def row(name, ss, df, with_f=True):
        d = {"source": name, "ss": float(ss), "df": df, "ms": float(ss / df) if df else None}
        if with_f and df and mse > 0:
            f_stat = (ss / df) / mse
            d.update(f=float(f_stat), p_value=float(stats.f.sf(f_stat, df, dfe)))
        anova.append(d)

    row("model", sst - sse, pn - 1)
    row("linear", ss_lin, k)
    row("square", ss_sq, k)
    if df_cross:
        row("cross", ss_cross, df_cross)
    row("residual", sse, dfe, with_f=False)
    # lack of fit against the pure error of repeated points
    pts: dict[tuple, list[int]] = {}
    for i in range(n):
        pts.setdefault(tuple(round(float(c[i]), 9) for c in cols), []).append(i)
    df_pe = n - len(pts)
    lack = None
    if df_pe >= 1 and len(pts) > pn:
        sspe = float(sum(((yv[idx] - yv[idx].mean()) ** 2).sum() for idx in pts.values() if len(idx) > 1))
        sslof, df_lof = sse - sspe, dfe - df_pe
        if sspe > 0 and df_lof >= 1:
            f_lof = (sslof / df_lof) / (sspe / df_pe)
            lack = {"ss_lack": float(sslof), "df_lack": df_lof, "ss_pure": sspe, "df_pure": df_pe, "f": float(f_lof), "p_value": float(stats.f.sf(f_lof, df_lof, df_pe))}
    h = np.einsum("ij,jk,ik->i", X, xtx_inv, X)
    res = yv - X @ beta
    press = float(((res / (1 - h)) ** 2).sum()) if np.all(h < 1 - 1e-9) else None
    out: dict = {"n": n, "k": k, "alpha": alpha, "terms": coef, "anova": anova, "lack_of_fit": lack, "df_error": dfe, "sigma": float(math.sqrt(mse)),
                 "r2": float(1 - sse / sst), "r2_adjusted": float(1 - (sse / dfe) / (sst / (n - 1))), "r2_predicted": float(1 - press / sst) if press is not None else None, "press": press}
    # canonical analysis
    b1 = beta[1 : 1 + k]
    B = np.zeros((k, k))
    for i in range(k):
        B[i, i] = beta[1 + k + i]
    pos = 1 + 2 * k
    for i, j in combinations(range(k), 2):
        B[i, j] = B[j, i] = beta[pos] / 2.0
        pos += 1
    eig = np.linalg.eigvalsh(B)
    scale = float(np.max(np.abs(beta[1:]))) or 1.0  # curvature that is zero beside the other coefficients: no stationary point (a rising ridge or a plane)
    warnings = []
    canon: dict = {"eigenvalues": [float(v) for v in eig]}
    if np.min(np.abs(eig)) < 1e-6 * scale:
        canon.update(stationary=None, nature="ridge")
        warnings.append({"code": "doe_surface_ridge"})
    else:
        xs = -0.5 * np.linalg.solve(B, b1)
        ys = float(beta[0] + 0.5 * b1 @ xs)
        nature = "maximum" if np.all(eig < 0) else "minimum" if np.all(eig > 0) else "saddle"
        lo = [float(c.min()) for c in cols]
        hi = [float(c.max()) for c in cols]
        inside = all(lo[i] - 1e-9 <= xs[i] <= hi[i] + 1e-9 for i in range(k))
        canon.update(stationary={nm: float(xs[i]) for i, nm in enumerate(names)}, response=ys, nature=nature, inside=bool(inside))
        if nature == "saddle":
            warnings.append({"code": "doe_surface_saddle"})
        if not inside:
            warnings.append({"code": "doe_surface_outside"})
    out["canonical"] = canon
    if lack and lack["p_value"] < alpha:
        warnings.append({"code": "doe_lack_of_fit", "p": lack["p_value"]})
    if dfe < 5:
        warnings.append({"code": "doe_surface_few_df", "df": dfe})
    out["warnings"] = warnings
    return out

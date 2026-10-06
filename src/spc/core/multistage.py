"""Multi-stage machining (AIAG-VDA SPC draft 8.5.1): pallets, workpiece carriers, spindles and machines.

The draft gives a simplified acceptance: the number of combinations of a workpiece on its way through the system is
    components per carrier x carriers x spindles per machine x machines,
at least 5 parts are inspected per combination and at least 50 parts per operation. Combinations proven "geometrically
identical" may be left out. The values of all combinations are evaluated together; a combination that deviates is found
from the allocation of the values to the combinations (mean and variation per combination).

The draft names no test for "deviates". The program gives, as aids and not as decisions:
  - Welch's t test of each combination against all other values, with the Bonferroni level alpha / m,
  - one-way analysis of variance of the means and the Brown-Forsythe test of the variances over the combinations,
  - the share of the total variance that lies between the combinations (eta squared) and the pooled standard deviation within them.
"""

from __future__ import annotations

import math
from itertools import product

import numpy as np
from scipy import stats

from spc.core.capability.indices import overall_indices

PER_COMBINATION = 5
MINIMUM_TOTAL = 50
MAX_COMBINATIONS = 2000


def inspection_scope(components_per_carrier: int = 1, carriers: int = 1, spindles: int = 1, machines: int = 1, *, geometrically_identical: int = 0,
                     measured_carriers: int | None = None, per_combination: int = PER_COMBINATION, minimum_total: int = MINIMUM_TOTAL) -> dict:
    """The scope of inspection of draft 8.5.1.2, and what the complete acceptance (50 parts per combination) would cost.

    `geometrically_identical` is the number of combinations that are proven identical to another and left out.
    `measured_carriers` (draft "further potentials"): only that many carriers, spread evenly over the tolerance of their fixing points, are part of the
    study; each other carrier then gets `per_combination` parts for each of its positions (our reading of "for each pallet/workpiece carrier and clamping point").
    """
    for name, v in (("components_per_carrier", components_per_carrier), ("carriers", carriers), ("spindles", spindles), ("machines", machines),
                    ("per_combination", per_combination), ("minimum_total", minimum_total)):
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise ValueError(f"{name} must be a whole number of at least 1")
    if measured_carriers is not None and not 1 <= measured_carriers <= carriers:
        raise ValueError("measured_carriers must be between 1 and the number of carriers")
    if geometrically_identical < 0:
        raise ValueError("geometrically_identical must not be negative")
    study_carriers = carriers if measured_carriers is None else measured_carriers
    combinations = components_per_carrier * carriers * spindles * machines
    in_study = components_per_carrier * study_carriers * spindles * machines
    if geometrically_identical >= in_study:
        raise ValueError("at least one combination must remain after the identical ones are left out")
    inspected = in_study - geometrically_identical
    per = max(per_combination, math.ceil(minimum_total / inspected))
    study_parts = per * inspected
    other = (carriers - study_carriers) * components_per_carrier * per_combination
    full = combinations * minimum_total
    total = study_parts + other
    return {
        "factors": {"components_per_carrier": components_per_carrier, "carriers": carriers, "spindles": spindles, "machines": machines},
        "combinations": combinations, "combinations_in_study": in_study, "geometrically_identical": geometrically_identical, "inspected_combinations": inspected,
        "parts_per_combination": per, "parts_in_study": study_parts,
        "measured_carriers": study_carriers, "parts_other_carriers": other,
        "total_parts": total, "full_acceptance_parts": full, "saving": full - total,
        "minimum_total_binding": per * inspected > per_combination * inspected,
        "per_combination": per_combination, "minimum_total": minimum_total,
    }


def spindle_distribution(total: int = MINIMUM_TOTAL, spindles: int = 1) -> dict:
    """Form, position and surface characteristics (draft 8.5.1.3): 50 parts spread evenly over the spindles."""
    if total < 1 or spindles < 1:
        raise ValueError("total and spindles must be at least 1")
    base, extra = divmod(total, spindles)
    return {"total": total, "spindles": spindles, "per_spindle": [base + (1 if i < extra else 0) for i in range(spindles)]}


def _stats(v: np.ndarray) -> dict:
    n = int(v.size)
    return {"n": n, "mean": float(v.mean()), "sd": float(v.std(ddof=1)) if n > 1 else None}


def _flt(x) -> float | None:
    return None if x is None or not math.isfinite(float(x)) else float(x)


def analyse_combinations(values, factors: dict[str, list], lsl: float | None = None, usl: float | None = None, *, alpha: float = 0.05,
                         per_combination: int = PER_COMBINATION, minimum_total: int = MINIMUM_TOTAL, expected: dict[str, list[str]] | None = None) -> dict:
    """All values together (performance indices of the whole), then each combination of the factors against the rest."""
    x = np.asarray(values, dtype=float).ravel()
    if x.size < 3 or not np.all(np.isfinite(x)):
        raise ValueError("need at least 3 finite values")
    if not factors:
        raise ValueError("name at least one factor (pallet, spindle, machine, position ...)")
    names = list(factors)
    cols = {}
    for k in names:
        c = np.asarray([str(v) for v in factors[k]])
        if c.shape != x.shape:
            raise ValueError(f"factor {k!r} must have one label per value ({x.size})")
        cols[k] = c
    if lsl is not None and usl is not None and not lsl < usl:
        raise ValueError("lsl must be below usl")
    if not 0 < alpha < 0.5:
        raise ValueError("alpha must be between 0 and 0.5")
    keys = list(zip(*(cols[k].tolist() for k in names)))
    index: dict[tuple, list[int]] = {}
    for i, key in enumerate(keys):
        index.setdefault(key, []).append(i)
    if len(index) > MAX_COMBINATIONS:
        raise ValueError(f"{len(index)} combinations: more than {MAX_COMBINATIONS}; use fewer or coarser factors")
    levels = {k: sorted({*cols[k].tolist(), *((expected or {}).get(k, []))}) for k in names}
    grand = float(x.mean())
    s_all = float(x.std(ddof=1))
    out: dict = {"n": int(x.size), "factors": names, "alpha": alpha, "mean": grand, "sd": s_all}
    if (lsl is not None or usl is not None) and s_all > 0:
        idx = overall_indices(x, lsl, usl)
        out["indices"] = {"p": _flt(idx.p), "pk": _flt(idx.pk), "pu": _flt(idx.pu), "pl": _flt(idx.pl)}
    else:
        out["indices"] = None

    m = len(index)
    level_alpha = alpha / m if m > 1 else alpha
    combos = []
    for key in sorted(index):
        ix = np.asarray(index[key])
        v = x[ix]
        st = _stats(v)
        rest = np.delete(x, ix)
        p_value = None
        if st["n"] >= 2 and rest.size >= 2 and (np.ptp(v) > 0 or np.ptp(rest) > 0):
            p_value = _flt(stats.ttest_ind(v, rest, equal_var=False).pvalue)
        ppk = None
        if (lsl is not None or usl is not None) and st["n"] >= 2 and st["sd"]:
            ppk = _flt(overall_indices(v, lsl, usl).pk)
        combos.append({
            "labels": dict(zip(names, key)), **st, "delta": st["mean"] - grand, "delta_sd": (st["mean"] - grand) / s_all if s_all > 0 else None,
            "p_value": p_value, "different": bool(p_value is not None and p_value < level_alpha), "ppk": ppk,
            "short": st["n"] < per_combination,
        })
    out["combinations"] = combos
    out["bonferroni_alpha"] = level_alpha

    groups = [x[np.asarray(i)] for i in index.values()]
    big = [g for g in groups if g.size >= 2]
    sst = float(((x - grand) ** 2).sum())
    ssb = float(sum(g.size * (g.mean() - grand) ** 2 for g in groups))
    dfw = int(x.size - m)
    ssw = max(sst - ssb, 0.0)
    anova = None
    if m >= 2 and dfw >= 1 and ssw > 0:
        f = (ssb / (m - 1)) / (ssw / dfw)
        anova = {"f": _flt(f), "df1": m - 1, "df2": dfw, "p_value": _flt(stats.f.sf(f, m - 1, dfw))}
        anova["different"] = bool(anova["p_value"] < alpha)
    bf = None
    if len(big) >= 2 and any(np.ptp(g) > 0 for g in big):
        res = stats.levene(*big, center="median")
        bf = {"statistic": _flt(res.statistic), "p_value": _flt(res.pvalue), "different": bool(res.pvalue < alpha)}
    out["between"] = {"anova": anova, "variances": bf, "eta_squared": _flt(ssb / sst) if sst > 0 else None,
                      "pooled_sd": _flt(math.sqrt(ssw / dfw)) if dfw >= 1 else None}

    per_factor = []
    for k in names:
        rows, gs = [], []
        for lv in levels[k]:
            sel = x[cols[k] == lv]
            rows.append({"level": lv, **(_stats(sel) if sel.size else {"n": 0, "mean": None, "sd": None})})
            if sel.size >= 1:
                gs.append(sel)
        counts = [r["n"] for r in rows]
        p = None
        if len(gs) >= 2 and sum(g.size for g in gs) - len(gs) >= 1 and any(np.ptp(g) > 0 for g in gs):
            p = _flt(stats.f_oneway(*gs).pvalue)
        per_factor.append({"factor": k, "levels": rows, "p_value": p, "different": bool(p is not None and p < alpha),
                           "balanced": bool(counts) and max(counts) - min(counts) <= 1})
    out["by_factor"] = per_factor

    full = [tuple(c) for c in product(*(levels[k] for k in names))]
    missing = [dict(zip(names, c)) for c in full if c not in index]
    short = [c["labels"] for c in combos if c["short"]]
    out["coverage"] = {
        "expected_combinations": len(full), "combinations": m, "missing": missing[:100], "missing_count": len(missing),
        "short": short[:100], "short_count": len(short), "per_combination": per_combination,
        "total": int(x.size), "minimum_total": minimum_total, "enough_total": bool(x.size >= minimum_total),
        "complete": not missing and not short and x.size >= minimum_total,
    }
    return out

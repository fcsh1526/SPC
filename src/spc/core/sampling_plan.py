"""Random sampling and the check that a sample stands for the process (draft 9.2: random samples, for example 25 subgroups of 5, that represent tools, batches and shifts).

Random plan: the sampling period is cut into k equal windows, one per subgroup, and the subgroup is taken at a random place inside its window, or in the middle of it (the draft's example: "collected at the middle of a shift") (so the subgroups are spread
over the whole period and no pattern of the operator can show up in them). For every factor (tool, shift, lot ...) the levels are dealt out to the subgroups as evenly as
possible (the numbers of two levels differ by at most 1) and in a random order, a new order for each factor. The seed is part of the result: the same seed gives the same plan,
so a plan can be written to the study record and shown again.

Coverage: for each factor of a data set, how many values and how many subgroups each level has. A level is MISSING when it is expected and absent, and THIN when its share of the values
is below `thin_share` times an equal share (default 0.5) or when it appears in a single subgroup only (when there are 5 subgroups or more). The rule is ours: the draft only says
that the sample must represent the tools, batches and shifts.
"""

from __future__ import annotations

import secrets
from typing import Mapping, Sequence

import numpy as np

MAX_SUBGROUPS = 1000
MAX_FACTORS = 6
MAX_LEVELS = 50


def _levels(factors: Mapping[str, Sequence]) -> dict[str, list[str]]:
    if not isinstance(factors, Mapping) or len(factors) > MAX_FACTORS:
        raise ValueError(f"give at most {MAX_FACTORS} factors")
    out = {}
    for name, levels in factors.items():
        lv = [str(x).strip() for x in levels]
        if not str(name).strip() or any(not x for x in lv) or len(set(lv)) != len(lv) or not 1 <= len(lv) <= MAX_LEVELS:
            raise ValueError(f"factor {name!r} needs 1 to {MAX_LEVELS} different, non-empty levels")
        out[str(name).strip()] = lv
    return out


def random_plan(subgroups: int, size: int, factors: Mapping[str, Sequence] | None = None, seed: int | None = None, position: str = "random") -> dict:
    if isinstance(subgroups, bool) or not isinstance(subgroups, int) or not 1 <= subgroups <= MAX_SUBGROUPS:
        raise ValueError(f"the number of subgroups must be a whole number from 1 to {MAX_SUBGROUPS}")
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 1000:
        raise ValueError("the subgroup size must be a whole number from 1 to 1000")
    fac = _levels(factors or {})
    if seed is None:
        seed = secrets.randbits(32)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("the seed must be a whole number from 0 to 4294967295")
    if position not in ("random", "middle"):
        raise ValueError("the position is random or middle")
    rng = np.random.default_rng(seed)
    assignment = {}
    for name, levels in fac.items():
        cycle = [levels[i % len(levels)] for i in range(subgroups)]  # the levels as evenly as possible ...
        assignment[name] = [cycle[i] for i in rng.permutation(subgroups)]  # ... in a random order
    offsets = rng.random(subgroups) if position == "random" else np.full(subgroups, 0.5)  # "collected at the middle of a shift" (draft 9.2): the middle of the window
    rows = [{"subgroup": i + 1, "window": [i / subgroups, (i + 1) / subgroups], "position": float((i + offsets[i]) / subgroups), "size": size,
             "levels": {name: assignment[name][i] for name in fac}} for i in range(subgroups)]
    counts = {name: {lv: assignment[name].count(lv) for lv in levels} for name, levels in fac.items()}
    return {"seed": seed, "position_mode": position, "subgroups": subgroups, "size": size, "parts": subgroups * size, "plan": rows, "counts": counts,
            "balanced": all(max(c.values()) - min(c.values()) <= 1 for c in counts.values())}


def coverage(columns: Mapping[str, Sequence[str]], subgroups: Sequence[str] | None = None, expected: Mapping[str, Sequence[str]] | None = None,
             thin_share: float = 0.5) -> dict:
    """`columns` is {factor: level of every value}; `subgroups` the subgroup label of every value (or None)."""
    if not 0 < thin_share <= 1:
        raise ValueError("thin_share must be above 0 and at most 1")
    if not columns:
        raise ValueError("give at least one factor")
    n = len(next(iter(columns.values())))
    if n == 0 or any(len(c) != n for c in columns.values()) or (subgroups is not None and len(subgroups) != n):
        raise ValueError("every factor needs one level per value")
    n_sub = len(set(subgroups)) if subgroups is not None else None
    out = {}
    for name, col in columns.items():
        levels = list(dict.fromkeys(col))
        equal = 1.0 / len(levels)
        rows, thin = [], []
        for lv in levels:
            idx = [i for i, v in enumerate(col) if v == lv]
            share = len(idx) / n
            groups = len({subgroups[i] for i in idx}) if subgroups is not None else None
            is_thin = share < thin_share * equal or (n_sub is not None and n_sub >= 5 and groups == 1)
            rows.append({"level": lv, "values": len(idx), "share": share, "subgroups": groups, "thin": bool(is_thin)})
            if is_thin:
                thin.append(lv)
        missing = [x for x in (expected or {}).get(name, []) if str(x) not in levels]
        out[name] = {"levels": rows, "missing": [str(x) for x in missing], "thin": thin, "representative": not missing and not thin}
    return {"values": n, "subgroups": n_sub, "thin_share": thin_share, "factors": out, "representative": all(f["representative"] for f in out.values())}

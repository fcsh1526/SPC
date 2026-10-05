"""The suggestion of the time-dependent model for a stored data set (see `spc.core.time_model`)."""

from __future__ import annotations

from spc.core import time_model as tm
from spc.data import Dataset

DEFAULT_BLOCK = 5  # data without subgroups are cut into blocks of this many consecutive values


def suggest_for_dataset(ds: Dataset, subgroup_size: int | None = None, hints: dict[str, bool] | None = None) -> dict:
    """Subgroups as the data have them; without a subgroup column, blocks of consecutive values in the order of the data."""
    unknown = sorted(set(hints or {}) - set(tm.HINT_MODELS))
    if unknown:
        raise ValueError(f"unknown hint(s): {unknown}; the known ones are {sorted(tm.HINT_MODELS)}")
    blocks = ds.subgroup is None
    size = (subgroup_size or DEFAULT_BLOCK) if blocks else None
    try:
        sg = ds.subgroups(size=size, incomplete="drop")
    except ValueError:
        return {"model": None, "reason": "not_enough_data", "groups": {"k": 0, "n": size or 0, "blocks": blocks}, "min_groups": tm.MIN_GROUPS,
                "min_values": tm.MIN_VALUES}
    out = tm.suggest(sg.matrix, blocks=blocks, hints=hints)
    out["groups"]["dropped"] = len(sg.dropped)
    return out

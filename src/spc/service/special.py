"""Special cases of draft 8.5 on stored data: the combinations of a multi-stage machine (8.5.1)."""

from __future__ import annotations

from spc.core import multistage as mst
from spc.data import Dataset


def multistage_for_dataset(ds: Dataset, factors: list[str], lsl: float | None, usl: float | None, **options) -> dict:
    """The factors are tag names (pallet, spindle, machine, position ...) or `subgroup`. Values marked invalid are left out."""
    mask = ds.valid_mask
    cols = {}
    for name in factors:
        if name == "subgroup":
            col = ds.subgroup
            if col is None:
                raise ValueError("the data has no subgroup labels")
        elif name in ds.tags:
            col = ds.tags[name]
        else:
            raise ValueError(f"the data has no tag {name!r}; the tags are {sorted(ds.tags)}")
        cols[name] = [str(v) for v, ok in zip(col.tolist(), mask.tolist()) if ok]
    values = [v for v, ok in zip(ds.values.tolist(), mask.tolist()) if ok]
    return mst.analyse_combinations(values, cols, lsl, usl, **options)

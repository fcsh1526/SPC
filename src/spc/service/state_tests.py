"""The tests for a multi-state process on a stored data set: the states are the subgroup labels, or the values of one tag."""

from __future__ import annotations

import numpy as np

from spc.core import multistate as ms
from spc.data import Dataset


def state_tests_for_dataset(ds: Dataset, alpha: float = 0.05, by: str | None = None) -> dict:
    if by:
        if by not in ds.tags:
            raise ValueError(f"the data has no tag {by!r}; the tags are {sorted(ds.tags)}")
        labels = ds.tags[by]
    elif ds.subgroup is not None:
        labels = ds.subgroup
    else:
        raise ValueError("the data has no subgroup labels and no tag: say which tag holds the state")
    mask = ds.valid_mask
    states: dict[str, list[float]] = {}
    for label, v, ok in zip(labels.tolist(), ds.values.tolist(), mask.tolist()):
        if ok:
            states.setdefault(str(label), []).append(v)
    out = ms.state_tests(states, alpha)
    out["by"] = by or "subgroup"
    return out

"""The tests for a multi-state process on a stored data set: the states are the subgroup labels, or the values of one tag."""

from __future__ import annotations

import numpy as np

from spc.core import multistate as ms
from spc.data import Dataset


def states_of(ds: Dataset, by: str | None = None) -> dict[str, list[float]]:
    """The valid values of each state, in the order of the data."""
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
    return states


def state_tests_for_dataset(ds: Dataset, alpha: float = 0.05, by: str | None = None) -> dict:
    out = ms.state_tests(states_of(ds, by), alpha)
    out["by"] = by or "subgroup"
    return out


def multistate_for_dataset(ds: Dataset, lsl: float, usl: float, by: str | None = None, **options) -> dict:
    """The procedure of ISO 22514-8 (spc.core.multistate.analyse) on the states of a stored data set."""
    out = ms.analyse(states_of(ds, by), lsl, usl, **options)
    out["by"] = by or "subgroup"
    return out

"""Measurement data with traceable outlier marks.

Rules from the AIAG-VDA SPC draft (7.6, 8.3.1, 13):

* An outlier is a value that does not belong to the investigated population (wrong measurement,
  mix-up, calibration part). A large value alone is not an outlier.
* Outliers are not deleted. They are marked invalid and left out of every calculation.
* Marking needs a clear reason. Statistical tests can only give hints.
* Data must stay traceable. Every mark and every restore is kept in a log with who, when and why.

A Dataset never changes. `mark_invalid` and `restore` return a new Dataset that shares the data
and carries a longer log.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Mapping, Sequence

import numpy as np

from spc.core.notes import Note


@dataclass(frozen=True)
class SourceInfo:
    """Where the data came from. Stored with results for traceability."""

    name: str
    sha256: str
    encoding: str
    delimiter: str
    n_data_rows: int


@dataclass(frozen=True)
class LogEntry:
    action: str  # "mark_invalid" or "restore"
    positions: tuple[int, ...]
    reason: str
    by: str
    at: str  # ISO 8601, UTC


@dataclass(frozen=True)
class DroppedSubgroup:
    label: str
    valid_count: int
    nominal_size: int
    reason: str


@dataclass(frozen=True)
class SubgroupData:
    """Complete subgroups ready for the chart functions.

    matrix[i, j] is the j-th valid value of subgroup labels[i]. positions has the dataset
    position of every value, so a chart point can be traced back to its source row.
    """

    matrix: np.ndarray
    labels: tuple[str, ...]
    positions: np.ndarray
    nominal_size: int
    dropped: tuple[DroppedSubgroup, ...] = ()


class IncompleteSubgroupsError(ValueError):
    """Some subgroups lost values to invalid marks, or are shorter than the nominal size."""

    def __init__(self, details: Sequence[DroppedSubgroup]):
        self.details = tuple(details)
        shown = ", ".join(f"{d.label} ({d.valid_count}/{d.nominal_size})" for d in self.details[:5])
        more = "" if len(self.details) <= 5 else f" and {len(self.details) - 5} more"
        super().__init__(
            f"{len(self.details)} subgroup(s) do not have the nominal size: {shown}{more}. "
            "Use incomplete='drop' to leave them out, or restore the marked values."
        )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True, eq=False)
class Dataset:
    values: np.ndarray
    source_rows: np.ndarray
    subgroup: np.ndarray | None = None
    timestamp: np.ndarray | None = None
    tags: Mapping[str, np.ndarray] = field(default_factory=dict)
    source: SourceInfo | None = None
    log: tuple[LogEntry, ...] = ()
    warnings: tuple[Note, ...] = ()

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_values(
        cls,
        values,
        subgroup=None,
        timestamp=None,
        tags: Mapping[str, Sequence] | None = None,
    ) -> "Dataset":
        v = np.asarray(values, dtype=float).ravel()
        if v.size == 0:
            raise ValueError("no values")
        if not np.all(np.isfinite(v)):
            raise ValueError("values contain NaN or inf")
        n = v.size

        def column(x, name):
            if x is None:
                return None
            a = np.asarray(x)
            if a.shape != (n,):
                raise ValueError(f"{name} must have one entry per value ({n})")
            return a

        sub = column(subgroup, "subgroup")
        if sub is not None:
            sub = sub.astype(str)
        tag_cols = {k: column(x, f"tag {k!r}").astype(str) for k, x in (tags or {}).items()}
        return cls(
            values=v,
            source_rows=np.arange(1, n + 1),
            subgroup=sub,
            timestamp=column(timestamp, "timestamp"),
            tags=tag_cols,
        )

    # ------------------------------------------------------------------ validity

    @property
    def n_total(self) -> int:
        return int(self.values.size)

    def _state(self) -> dict[int, LogEntry]:
        """Current invalid positions mapped to the log entry that marked them."""
        state: dict[int, LogEntry] = {}
        for entry in self.log:
            for p in entry.positions:
                if entry.action == "mark_invalid":
                    state[p] = entry
                else:
                    state.pop(p, None)
        return state

    @property
    def valid_mask(self) -> np.ndarray:
        mask = np.ones(self.n_total, dtype=bool)
        for p in self._state():
            mask[p] = False
        return mask

    @property
    def n_valid(self) -> int:
        return int(self.valid_mask.sum())

    @property
    def n_invalid(self) -> int:
        return self.n_total - self.n_valid

    def invalid_info(self) -> dict[int, tuple[str, str, str]]:
        """position -> (reason, by, at) for every value that is invalid now."""
        return {p: (e.reason, e.by, e.at) for p, e in sorted(self._state().items())}

    def positions_for_source_rows(self, rows: Sequence[int]) -> tuple[int, ...]:
        """Translate source (CSV line) numbers into dataset positions."""
        lookup = {int(r): i for i, r in enumerate(self.source_rows)}
        missing = [r for r in rows if int(r) not in lookup]
        if missing:
            raise ValueError(f"source rows not in the data: {missing}")
        return tuple(lookup[int(r)] for r in rows)

    # ------------------------------------------------------------------ marking

    def _check_positions(self, positions: Sequence[int]) -> tuple[int, ...]:
        pos = tuple(int(p) for p in positions)
        if not pos:
            raise ValueError("no positions given")
        if len(set(pos)) != len(pos):
            raise ValueError("duplicate positions")
        bad = [p for p in pos if not 0 <= p < self.n_total]
        if bad:
            raise ValueError(f"positions out of range: {bad}")
        return pos

    def mark_invalid(self, positions: Sequence[int], reason: str, by: str, at: str | None = None) -> "Dataset":
        """Mark values as outliers: kept in the data, left out of calculations.

        `reason` must say why the value cannot come from the investigated process.
        A test result or "looks too high" is not a reason.
        """
        reason, by = (reason or "").strip(), (by or "").strip()
        if not reason:
            raise ValueError("a reason is required to mark a value as invalid")
        if not by:
            raise ValueError("the person who marks the value is required")
        pos = self._check_positions(positions)
        already = [p for p in pos if p in self._state()]
        if already:
            raise ValueError(f"already invalid: {already}. Restore first if the reason changes.")
        entry = LogEntry("mark_invalid", pos, reason, by, at or _now())
        return replace(self, log=self.log + (entry,))

    def restore(self, positions: Sequence[int], reason: str, by: str, at: str | None = None) -> "Dataset":
        """Take the invalid mark away again. The earlier mark stays in the log."""
        reason, by = (reason or "").strip(), (by or "").strip()
        if not reason or not by:
            raise ValueError("a reason and the person are required to restore a value")
        pos = self._check_positions(positions)
        not_invalid = [p for p in pos if p not in self._state()]
        if not_invalid:
            raise ValueError(f"not invalid: {not_invalid}")
        entry = LogEntry("restore", pos, reason, by, at or _now())
        return replace(self, log=self.log + (entry,))

    # ------------------------------------------------------------------ use in calculations

    def individuals(self) -> tuple[np.ndarray, np.ndarray]:
        """Valid values in file order and their positions."""
        mask = self.valid_mask
        return self.values[mask], np.flatnonzero(mask)

    def summary(self) -> dict:
        """Counts for the report: ntot, neff and the number of subgroups if known."""
        out = {"n_total": self.n_total, "n_effective": self.n_valid, "n_invalid": self.n_invalid}
        if self.subgroup is not None:
            out["k_subgroups"] = len(dict.fromkeys(self.subgroup.tolist()))
        return out

    def _labels(self, size: int | None) -> np.ndarray:
        if self.subgroup is not None:
            if size is not None:
                raise ValueError("the data already has subgroup labels; do not give a size")
            return self.subgroup
        if size is None or size < 1:
            raise ValueError("no subgroup labels in the data: give the subgroup size")
        return (np.arange(self.n_total) // size + 1).astype(str)

    def subgroups(self, size: int | None = None, incomplete: str = "error") -> SubgroupData:
        """Build the (k, n) matrix for X̄-s and X̄-R charts from the valid values.

        The nominal size is the largest subgroup in the raw data (or `size`). A subgroup with fewer
        valid values than that is incomplete, because a chart needs equal subgroup sizes.

        incomplete="error" : raise IncompleteSubgroupsError (default)
        incomplete="drop"  : leave incomplete subgroups out and list them in `dropped`
        """
        if incomplete not in ("error", "drop"):
            raise ValueError("incomplete must be 'error' or 'drop'")
        labels = self._labels(size)
        order = list(dict.fromkeys(labels.tolist()))
        valid = self.valid_mask
        raw_sizes = {lab: int(np.sum(labels == lab)) for lab in order}
        nominal = size if size is not None else max(raw_sizes.values())

        rows, kept, pos_rows, dropped = [], [], [], []
        for lab in order:
            idx = np.flatnonzero((labels == lab) & valid)
            if len(idx) != nominal:
                why = "values marked invalid" if raw_sizes[lab] >= nominal else "subgroup shorter than nominal size"
                dropped.append(DroppedSubgroup(lab, len(idx), nominal, why))
                continue
            rows.append(self.values[idx])
            pos_rows.append(idx)
            kept.append(lab)
        if dropped and incomplete == "error":
            raise IncompleteSubgroupsError(dropped)
        if not rows:
            raise ValueError("no complete subgroups")
        return SubgroupData(np.vstack(rows), tuple(kept), np.vstack(pos_rows), nominal, tuple(dropped))

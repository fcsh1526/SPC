"""Dataset <-> plain JSON data. Used by the archive and by the database."""

from __future__ import annotations

from dataclasses import asdict

import numpy as np

from spc.core.notes import Note
from spc.data.dataset import Dataset, LogEntry, SourceInfo


def dataset_to_dict(ds: Dataset) -> dict:
    return {
        "values": ds.values.tolist(),
        "source_rows": [int(r) for r in ds.source_rows],
        "subgroup": None if ds.subgroup is None else [str(s) for s in ds.subgroup],
        "timestamp": None if ds.timestamp is None else [str(t) for t in ds.timestamp],
        "tags": {k: [str(x) for x in v] for k, v in ds.tags.items()},
        "source": None if ds.source is None else asdict(ds.source),
        "log": [
            {"action": e.action, "positions": [int(p) for p in e.positions], "reason": e.reason, "by": e.by, "at": e.at}
            for e in ds.log
        ],
        "notes": [{"code": n.code, "params": dict(n.params)} for n in ds.warnings],
    }


def dataset_from_dict(d: dict) -> Dataset:
    return Dataset(
        values=np.array(d["values"], dtype=float),
        source_rows=np.array(d["source_rows"], dtype=int),
        subgroup=None if d["subgroup"] is None else np.array(d["subgroup"], dtype=str),
        timestamp=None if d["timestamp"] is None else np.array(d["timestamp"], dtype="datetime64[s]"),
        tags={k: np.array(v, dtype=str) for k, v in d["tags"].items()},
        source=None if d["source"] is None else SourceInfo(**d["source"]),
        log=tuple(LogEntry(e["action"], tuple(e["positions"]), e["reason"], e["by"], e["at"]) for e in d["log"]),
        warnings=tuple(Note(n["code"], n["params"]) for n in d["notes"]),
    )

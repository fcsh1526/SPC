"""Archive record: data, marks, parameters, results and report inputs in one JSON document.

Draft chapter 13 asks to keep not only the result but also the parameters of the calculation, so
that it can be reproduced later, and to keep documents change-proof. Here:

* `build_archive` writes everything that went into the report and everything that came out.
* `verify_archive` recomputes the SHA-256 over the content. A changed value, mark or parameter breaks it.
  This shows a change. It does not stop one. To prove who wrote the record, sign it outside this program.
* `reproduce` runs the analysis again from the stored data and parameters and compares the numbers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from spc import __version__
from spc.core.notes import Note
from spc.data.dataset import Dataset, LogEntry, SourceInfo
from spc.report.meta import ReportMeta
from spc.service import AnalysisRequest, analyze

FORMAT = "spc-archive"
VERSION = 1
REL_TOL = 1e-6


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


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


def build_archive(
    dataset: Dataset,
    request: AnalysisRequest,
    meta: ReportMeta,
    result: dict,
    *,
    created_at: str,
    report_id: str,
    language: str,
) -> dict:
    body = {
        "format": FORMAT,
        "version": VERSION,
        "engine_version": __version__,
        "created_at": created_at,
        "report_id": report_id,
        "language": language,
        "request": asdict(request),
        "report_meta": asdict(meta),
        "result": result,
        "dataset": dataset_to_dict(dataset),
    }
    digest = hashlib.sha256(canonical(body)).hexdigest()
    return {
        **body,
        "integrity": {
            "algorithm": "sha256",
            "scope": "all other fields as canonical JSON (sorted keys, no spaces, UTF-8)",
            "digest": digest,
        },
    }


def verify_archive(archive: dict) -> bool:
    """True when the content still matches the stored digest."""
    try:
        integrity = archive["integrity"]
        body = {k: v for k, v in archive.items() if k != "integrity"}
        if archive.get("format") != FORMAT or integrity["algorithm"] != "sha256":
            return False
        return hashlib.sha256(canonical(body)).hexdigest() == integrity["digest"]
    except (KeyError, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Reproduction:
    integrity_ok: bool
    reproduced: bool
    same_engine_version: bool
    differences: tuple[str, ...] = field(default_factory=tuple)


def _compare(a: Any, b: Any, path: str, out: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append(f"{path}.{key}: present in only one of them")
            else:
                _compare(a[key], b[key], f"{path}.{key}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                _compare(x, y, f"{path}[{i}]", out)
    elif isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            if a is not b:
                out.append(f"{path}: {a} != {b}")
        elif abs(a - b) > REL_TOL * max(1.0, abs(a), abs(b)):
            out.append(f"{path}: {a} != {b}")
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")


def reproduce(archive: dict) -> Reproduction:
    """Run the analysis again from the stored data and parameters and compare with the stored result."""
    ok = verify_archive(archive)
    dataset = dataset_from_dict(archive["dataset"])
    request = AnalysisRequest(**archive["request"])
    again = analyze(dataset, request)
    diffs: list[str] = []
    _compare(archive["result"], json.loads(json.dumps(again)), "result", diffs)
    return Reproduction(ok, not diffs, archive.get("engine_version") == __version__, tuple(diffs[:20]))

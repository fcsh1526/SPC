"""Read one file into the series of its characteristics.

Rules (the same as for the import of the program, spc.data.csv_io):

* The user names the columns. Nothing is guessed from the content.
* All problems in the file are collected and reported together, with line numbers.
* CSV (UTF-8 or Big5, any of the usual delimiters, decimal point or comma) or one sheet of an *.xlsx workbook.

Two layouts:

  long   one row per measured value: `value` names one column. A `characteristic` column splits the file into characteristics,
         a `subgroup` column gives the subgroups (without it the values are individuals).
  wide   one row per subgroup: `value` names several columns, the values of the subgroup (the classic control chart sheet).

Limits (specification and the control limits in use) come from columns or from numbers given by the user. Within a characteristic
the last value of a limit column counts; how many different values a column held is kept, because limits that were changed
are worth a line in the report.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

from spc.data.csv_io import DEFAULT_TIMESTAMP_FORMATS, FALSE_TOKENS, TRUE_TOKENS, DataImportError, ImportIssue, _decode, _parse_number, _parse_time, _read_header, _sniff_delimiter
from spc.data.xlsx_io import _table, is_xlsx

LIMITS = ("lsl", "usl", "lcl", "cl", "ucl")
MAX_ISSUES = 200


@dataclass(frozen=True)
class Columns:
    """Which column holds what. Only `value` is required."""

    value: tuple[str, ...]                 # one column (long layout) or the columns of one subgroup (wide layout)
    characteristic: str | None = None
    subgroup: str | None = None
    time: str | None = None
    lsl: str | None = None
    usl: str | None = None
    lcl: str | None = None
    cl: str | None = None
    ucl: str | None = None
    alarm: str | None = None               # the alarm flag of the system the data come from (yes/no)
    action: str | None = None              # what was done about a point: any text counts as a record


@dataclass
class Series:
    """The data of one characteristic, in file order."""

    name: str
    values: np.ndarray                     # every value
    subgroup: np.ndarray | None            # label of the subgroup of every value (None: individuals)
    time: np.ndarray | None                # datetime64[s] of every value, NaT where the cell was empty
    lines: np.ndarray                      # line of the file of every value
    alarm: np.ndarray | None               # the system's alarm flag of every value
    action: np.ndarray | None              # True where an action is recorded
    limits: dict[str, float | None] = field(default_factory=dict)       # lsl, usl, lcl, cl, ucl
    limit_changes: dict[str, int] = field(default_factory=dict)        # number of different values a limit column held


@dataclass(frozen=True)
class FileInfo:
    name: str
    sha256: str
    encoding: str
    delimiter: str
    rows: int
    header: tuple[str, ...]


def _flag(text: str) -> bool | None:
    t = text.strip().lower()
    if t in TRUE_TOKENS or t in {"alarm", "ooc", "警報", "異常"}:
        return True
    if t in FALSE_TOKENS or t == "":
        return False
    return None


def read_header(source, *, sheet: str | None = None, header_row: int = 1, delimiter: str | None = None, encoding: str = "auto") -> list[str]:
    """The column names of a file (to help the user name the columns)."""
    text, delim, _, _ = _text(source, sheet, ".", encoding, delimiter, header_row)
    return _read_header(csv.reader(io.StringIO(text), delimiter=delim), header_row)


def _text(source, sheet, decimal, encoding, delimiter, header_row) -> tuple[str, str, str, tuple[str, bytes]]:
    if isinstance(source, (bytes, bytearray)):
        raw, name = bytes(source), "<bytes>"
    else:
        path = Path(source)
        raw, name = path.read_bytes(), path.name
    if is_xlsx(raw):
        text, _, chosen = _table(raw, sheet, decimal)
        return text, ",", f"xlsx [{chosen}]", (name, raw)
    text, used = _decode(raw, encoding)
    return text, delimiter or _sniff_delimiter(text, header_row), used, (name, raw)


def read_file(
    source,
    columns: Columns,
    *,
    constants: dict[str, float] | None = None,
    sheet: str | None = None,
    header_row: int = 1,
    delimiter: str | None = None,
    decimal: str = ".",
    encoding: str = "auto",
    timestamp_formats: Sequence[str] = DEFAULT_TIMESTAMP_FORMATS,
    default_name: str | None = None,
) -> tuple[list[Series], FileInfo]:
    """Read a file (path or bytes) into one Series per characteristic, in the order of their first row.

    `constants`: limits given as numbers (keys of LIMITS). They apply to every characteristic and a limit must not be given both ways.
    Rows with an empty value cell are left out (an export of a running system has them) and counted in the report.
    """
    if decimal not in (".", ","):
        raise ValueError("decimal must be '.' or ','")
    constants = dict(constants or {})
    if not columns.value:
        raise ValueError("name at least one value column")
    wide = len(columns.value) > 1
    for key in constants:
        if key not in LIMITS:
            raise ValueError(f"unknown limit {key!r}")
        if getattr(columns, key) is not None:
            raise ValueError(f"{key} is given as a column and as a number: choose one")
    if wide and columns.subgroup is not None:
        raise ValueError("with several value columns a row is a subgroup: do not name a subgroup column")

    text, delim, used_encoding, (name, raw) = _text(source, sheet, decimal, encoding, delimiter, header_row)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    header = _read_header(reader, header_row)
    issues: list[ImportIssue] = []
    if len(set(header)) != len(header):
        issues.append(ImportIssue(header_row, None, "duplicate_header", f"duplicate column names: {sorted({h for h in header if header.count(h) > 1})}"))
    wanted = {f"value {i + 1}": c for i, c in enumerate(columns.value)}
    wanted.update({role: getattr(columns, role) for role in ("characteristic", "subgroup", "time", *LIMITS, "alarm", "action")})
    for role, col in wanted.items():
        if col is not None and col not in header:
            issues.append(ImportIssue(header_row, col, "missing_column", f"column {col!r} ({role}) not found. Columns in the file: {header}"))
    if issues:
        raise DataImportError(issues)
    idx = {role: header.index(col) for role, col in wanted.items() if col is not None}
    value_idx = [header.index(c) for c in columns.value]

    def cell(row, role):
        i = idx.get(role)
        return row[i].strip() if i is not None and i < len(row) else ""

    fallback = default_name or Path(name).stem or "value"
    data: dict[str, dict] = {}
    n_rows = 0
    for row in reader:
        line = reader.line_num
        if not any(c.strip() for c in row):
            continue
        n_rows += 1
        cname = cell(row, "characteristic") if columns.characteristic else fallback
        if columns.characteristic and not cname:
            issues.append(ImportIssue(line, columns.characteristic, "empty_characteristic", "the characteristic cell is empty"))
            continue
        d = data.setdefault(cname, {"values": [], "subgroup": [], "time": [], "lines": [], "alarm": [], "action": [],
                                    "limits": {k: [] for k in LIMITS}, "skipped": 0})
        values = []
        for i in value_idx:
            t = row[i] if i < len(row) else ""
            if not t.strip():
                continue
            x = _parse_number(t, decimal)
            if x is None:
                issues.append(ImportIssue(line, header[i], "not_a_number", f"{t.strip()!r} is not a number"))
            else:
                values.append(x)
        if not values:
            d["skipped"] += 1
            continue
        when = np.datetime64("NaT", "s")
        if columns.time:
            t = cell(row, "time")
            if t:
                parsed = _parse_time(t, timestamp_formats)
                if parsed is None:
                    issues.append(ImportIssue(line, columns.time, "bad_time", f"{t!r} is not a date and time"))
                else:
                    when = parsed
        alarm = False
        if columns.alarm:
            a = _flag(cell(row, "alarm"))
            if a is None:
                issues.append(ImportIssue(line, columns.alarm, "bad_flag", f"{cell(row, 'alarm')!r} is not yes or no"))
            else:
                alarm = a
        for key in LIMITS:
            if getattr(columns, key):
                t = cell(row, key)
                if t:
                    x = _parse_number(t, decimal)
                    if x is None:
                        issues.append(ImportIssue(line, getattr(columns, key), "not_a_number", f"{t!r} is not a number"))
                    else:
                        d["limits"][key].append(x)
        label = f"row {line}" if wide else (cell(row, "subgroup") if columns.subgroup else "")
        if columns.subgroup and not label:
            issues.append(ImportIssue(line, columns.subgroup, "empty_subgroup", "the subgroup cell is empty"))
            continue
        for x in values:
            d["values"].append(x)
            d["subgroup"].append(label)
            d["time"].append(when)
            d["lines"].append(line)
            d["alarm"].append(alarm)
            d["action"].append(bool(cell(row, "action")))
        if len(issues) > MAX_ISSUES:
            break
    if issues:
        raise DataImportError(issues)
    if not data or not any(d["values"] for d in data.values()):
        raise DataImportError([ImportIssue(None, None, "no_data", "the file holds no value")])

    series = []
    for cname, d in data.items():
        if not d["values"]:
            continue
        limits, changes = {}, {}
        for key in LIMITS:
            seen = d["limits"][key]
            limits[key] = constants.get(key, seen[-1] if seen else None)
            changes[key] = len(set(seen))
        s = Series(
            name=cname,
            values=np.array(d["values"], dtype=float),
            subgroup=np.array(d["subgroup"], dtype=object) if (wide or columns.subgroup) else None,
            time=np.array(d["time"], dtype="datetime64[s]") if columns.time else None,
            lines=np.array(d["lines"], dtype=int),
            alarm=np.array(d["alarm"], dtype=bool) if columns.alarm else None,
            action=np.array(d["action"], dtype=bool) if columns.action else None,
            limits=limits,
            limit_changes=changes,
        )
        s.skipped_rows = d["skipped"]  # rows without a value
        series.append(s)
    info = FileInfo(name, hashlib.sha256(raw).hexdigest(), used_encoding, delim, n_rows, tuple(header))
    return series, info

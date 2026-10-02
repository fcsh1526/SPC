"""CSV import and export.

Import rules:

* The user names the columns. Nothing is guessed from the content.
* The file's SHA-256, encoding and delimiter are stored with the data (traceability, draft chapter 13).
* All problems in the file are collected and reported together, with line numbers.
* Excel files saved as CSV in Taiwan are often Big5 (cp950). `encoding="auto"` tries UTF-8 first, then cp950.
* Export keeps the invalid marks, so a file can be imported again without loss.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

from spc.core.notes import Note
from spc.data.dataset import Dataset, LogEntry, SourceInfo

DELIMITERS = ",;\t|"
TRUE_TOKENS = frozenset({"1", "true", "yes", "y", "valid", "ok", "是", "有效"})
FALSE_TOKENS = frozenset({"0", "false", "no", "n", "invalid", "ng", "否", "無效", "无效"})
DEFAULT_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
)


@dataclass(frozen=True)
class ImportIssue:
    line: int | None  # 1-based line in the file, None for file-level problems
    column: str | None
    code: str
    message: str


class DataImportError(ValueError):
    def __init__(self, issues: Sequence[ImportIssue]):
        self.issues = tuple(issues)
        head = "; ".join(
            (f"line {i.line}: " if i.line else "") + i.message for i in self.issues[:5]
        )
        more = "" if len(self.issues) <= 5 else f" (+{len(self.issues) - 5} more)"
        super().__init__(f"{len(self.issues)} problem(s) in the file: {head}{more}")


@dataclass(frozen=True)
class ColumnMap:
    """Which CSV column holds what. Only `value` is required."""

    value: str
    subgroup: str | None = None
    timestamp: str | None = None
    tags: tuple[str, ...] = ()
    source_row: str | None = None  # keeps the original row numbers of an exported file
    valid: str | None = None
    invalid_reason: str | None = None
    invalid_by: str | None = None
    invalid_at: str | None = None


def _decode(raw: bytes, encoding: str) -> tuple[str, str]:
    if encoding != "auto":
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            raise DataImportError([ImportIssue(None, None, "encoding", f"cannot decode the file as {encoding}: {exc}")])
    for enc in ("utf-8-sig", "cp950"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise DataImportError([ImportIssue(None, None, "encoding", "cannot decode the file as UTF-8 or Big5 (cp950)")])


def _sniff_delimiter(text: str) -> str:
    """Pick the delimiter that occurs most often in the header line. Data rows are not used,
    because a decimal comma would mislead the choice."""
    first = text.splitlines()[0] if text else ""
    counts = {d: first.count(d) for d in DELIMITERS}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def _parse_number(text: str, decimal: str) -> float | None:
    t = text.strip().replace(" ", "")
    if decimal == ",":
        if "." in t:
            return None
        t = t.replace(",", ".")
    elif "," in t:
        return None
    try:
        x = float(t)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def _parse_time(text: str, formats: Sequence[str]) -> np.datetime64 | None:
    t = text.strip()
    for fmt in formats:
        try:
            return np.datetime64(datetime.strptime(t, fmt), "s")
        except ValueError:
            continue
    try:
        return np.datetime64(datetime.fromisoformat(t).replace(tzinfo=None), "s")
    except ValueError:
        return None


def load_csv(
    source,
    columns: ColumnMap,
    *,
    delimiter: str | None = None,
    decimal: str = ".",
    encoding: str = "auto",
    missing: str = "error",
    timestamp_formats: Sequence[str] = DEFAULT_TIMESTAMP_FORMATS,
) -> Dataset:
    """Read a CSV file (path or bytes) into a Dataset.

    missing="error" : an empty value cell is an error (default)
    missing="skip"  : rows with an empty value cell are left out and noted in `warnings`

    A `valid` column (1/0, yes/no, 是/否 ...) re-creates invalid marks. An invalid row needs a
    reason in `invalid_reason`. The mark's author and time come from `invalid_by` and
    `invalid_at` if present, otherwise "import" and the import time.
    """
    if decimal not in (".", ","):
        raise ValueError("decimal must be '.' or ','")
    if missing not in ("error", "skip"):
        raise ValueError("missing must be 'error' or 'skip'")
    if isinstance(source, (bytes, bytearray)):
        raw, name = bytes(source), "<bytes>"
    else:
        path = Path(source)
        raw, name = path.read_bytes(), path.name
    text, used_encoding = _decode(raw, encoding)
    delim = delimiter or _sniff_delimiter(text)

    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise DataImportError([ImportIssue(None, None, "empty", "the file is empty")])
    issues: list[ImportIssue] = []
    if len(set(header)) != len(header):
        dup = sorted({h for h in header if header.count(h) > 1})
        issues.append(ImportIssue(1, None, "duplicate_header", f"duplicate column names: {dup}"))

    wanted = {
        "value": columns.value,
        "subgroup": columns.subgroup,
        "timestamp": columns.timestamp,
        "source_row": columns.source_row,
        "valid": columns.valid,
        "invalid_reason": columns.invalid_reason,
        "invalid_by": columns.invalid_by,
        "invalid_at": columns.invalid_at,
    }
    for tag in columns.tags:
        wanted[f"tag:{tag}"] = tag
    for role, col in wanted.items():
        if col is not None and col not in header:
            issues.append(
                ImportIssue(1, col, "missing_column", f"column {col!r} ({role}) not found. Columns in the file: {header}")
            )
    if issues:
        raise DataImportError(issues)
    index = {role: header.index(col) for role, col in wanted.items() if col is not None}

    def cell(row, role):
        i = index.get(role)
        return row[i].strip() if i is not None and i < len(row) else ""

    values, lines, subs, times = [], [], [], []
    tag_cols: dict[str, list[str]] = {t: [] for t in columns.tags}
    marks: list[tuple[int, str, str, str]] = []  # position, reason, by, at
    notes: list[Note] = []
    skipped = 0
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    for row in reader:
        line = reader.line_num
        if not any(c.strip() for c in row):
            continue
        raw_value = cell(row, "value")
        if raw_value == "":
            if missing == "skip":
                skipped += 1
                continue
            issues.append(ImportIssue(line, columns.value, "missing_value", "value is empty"))
            continue
        x = _parse_number(raw_value, decimal)
        if x is None:
            issues.append(ImportIssue(line, columns.value, "bad_number", f"{raw_value!r} is not a number (decimal={decimal!r})"))
            continue
        pos = len(values)
        values.append(x)
        if columns.source_row:
            try:
                lines.append(int(cell(row, "source_row")))
            except ValueError:
                issues.append(
                    ImportIssue(line, columns.source_row, "bad_source_row", f"{cell(row, 'source_row')!r} is not a row number")
                )
                lines.append(line)
        else:
            lines.append(line)
        if columns.subgroup:
            label = cell(row, "subgroup")
            if label == "":
                issues.append(ImportIssue(line, columns.subgroup, "missing_subgroup", "subgroup label is empty"))
            subs.append(unsafe_to_plain(label))
        if columns.timestamp:
            t = _parse_time(cell(row, "timestamp"), timestamp_formats)
            if t is None:
                issues.append(
                    ImportIssue(line, columns.timestamp, "bad_timestamp", f"{cell(row, 'timestamp')!r} is not a known date-time")
                )
                t = np.datetime64("NaT")
            times.append(t)
        for tag in columns.tags:
            tag_cols[tag].append(unsafe_to_plain(cell(row, f"tag:{tag}")))
        if columns.valid:
            flag = cell(row, "valid").lower()
            if flag in FALSE_TOKENS:
                reason = unsafe_to_plain(cell(row, "invalid_reason"))
                if not reason:
                    issues.append(ImportIssue(line, columns.valid, "invalid_without_reason", "an invalid value needs a reason"))
                else:
                    marks.append((pos, reason, unsafe_to_plain(cell(row, "invalid_by")) or "import", cell(row, "invalid_at") or now))
            elif flag not in TRUE_TOKENS:
                issues.append(ImportIssue(line, columns.valid, "bad_flag", f"{cell(row, 'valid')!r} is not a valid/invalid flag"))
    if not values and not issues:
        issues.append(ImportIssue(None, None, "no_data", "the file has no data rows"))
    if issues:
        raise DataImportError(issues)

    if skipped:
        notes.append(Note("rows_skipped", {"count": skipped}))
    if columns.subgroup:
        seen, closed, last = set(), set(), None
        for lab in subs:
            if lab != last:
                if last is not None:
                    closed.add(last)
                if lab in closed:
                    notes.append(Note("subgroup_not_contiguous", {"label": lab}))
                    break
                last = lab
            seen.add(lab)
    if columns.timestamp:
        t_arr = np.array(times, dtype="datetime64[s]")
        if np.any(np.diff(t_arr).astype("int64") < 0):
            notes.append(Note("time_not_ordered"))

    data = Dataset(
        values=np.array(values, dtype=float),
        source_rows=np.array(lines, dtype=int),
        subgroup=np.array(subs, dtype=str) if columns.subgroup else None,
        timestamp=np.array(times, dtype="datetime64[s]") if columns.timestamp else None,
        tags={t: np.array(v, dtype=str) for t, v in tag_cols.items()},
        source=SourceInfo(name, hashlib.sha256(raw).hexdigest(), used_encoding, delim, len(values)),
        warnings=tuple(notes),
    )
    grouped: dict[tuple[str, str, str], list[int]] = {}
    for pos, reason, by, at in marks:
        grouped.setdefault((reason, by, at), []).append(pos)
    log = tuple(LogEntry("mark_invalid", tuple(p), r, b, a) for (r, b, a), p in grouped.items())
    return replace(data, log=log)


def preview_csv(
    source,
    *,
    delimiter: str | None = None,
    encoding: str = "auto",
    n_rows: int = 8,
) -> dict:
    """Header and first rows of a file, so the user can choose the columns before importing."""
    raw = bytes(source) if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    text, used_encoding = _decode(raw, encoding)
    delim = delimiter or _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise DataImportError([ImportIssue(None, None, "empty", "the file is empty")])
    rows = []
    for row in reader:
        if not any(c.strip() for c in row):
            continue
        rows.append([c.strip() for c in row])
        if len(rows) >= n_rows:
            break
    return {"encoding": used_encoding, "delimiter": delim, "header": header, "rows": rows}


def export_columns(data: Dataset) -> ColumnMap:
    """Column map that reads back a file written by `to_csv` for this dataset."""
    return ColumnMap(
        value="value",
        subgroup="subgroup" if data.subgroup is not None else None,
        timestamp="timestamp" if data.timestamp is not None else None,
        tags=tuple(data.tags),
        source_row="source_row",
        valid="valid",
        invalid_reason="invalid_reason",
        invalid_by="invalid_by",
        invalid_at="invalid_at",
    )


FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def spreadsheet_safe(text: str) -> str:
    """A cell that starts with = + - @ is run as a formula by Excel. Text from people (reasons, names,
    labels) gets a leading apostrophe so it stays text. `load_csv` takes that apostrophe off again."""
    return "'" + text if text.startswith(FORMULA_STARTS) and not _is_plain_number(text) else text


def _is_plain_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def unsafe_to_plain(text: str) -> str:
    return text[1:] if text.startswith("'") and text[1:].startswith(FORMULA_STARTS) else text


def to_csv(data: Dataset, path=None, delimiter: str = ",", encoding: str = "utf-8-sig") -> str:
    """Write the dataset with its invalid marks. Returns the text, and writes the file if `path` is given.

    Invalid rows stay in the file with valid=0 and their reason. The mark history in the log is not
    written. Keep the dataset's log in the archive if the full history is needed.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    head = ["source_row", "value"]
    if data.subgroup is not None:
        head.append("subgroup")
    if data.timestamp is not None:
        head.append("timestamp")
    head += list(data.tags) + ["valid", "invalid_reason", "invalid_by", "invalid_at"]
    writer.writerow(head)
    info = data.invalid_info()
    for i in range(data.n_total):
        row = [int(data.source_rows[i]), repr(float(data.values[i]))]
        if data.subgroup is not None:
            row.append(spreadsheet_safe(str(data.subgroup[i])))
        if data.timestamp is not None:
            row.append(str(data.timestamp[i]).replace("T", " "))
        row += [spreadsheet_safe(str(data.tags[t][i])) for t in data.tags]
        reason, by, at = info.get(i, ("", "", ""))
        row += [0 if i in info else 1, spreadsheet_safe(reason), spreadsheet_safe(by), at]
        writer.writerow(row)
    text = buffer.getvalue()
    if path is not None:
        Path(path).write_bytes(text.encode(encoding))
    return text

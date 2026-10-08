"""Excel (*.xlsx) import.

An Excel sheet is turned into the same table a CSV file gives, then the CSV importer does the rest. So the
rules are the same (the user names the columns, all problems come back together, row numbers are kept) and
a file imports the same way in either format.

* Only the stored values are read, never formulas: a formula cell that was never calculated by Excel is empty.
* Row numbers are the Excel row numbers (the lines above the header row are kept as empty lines).
* The SHA-256 is that of the *.xlsx file, not of the converted table.
* Numbers keep full precision. Dates and times are written as `YYYY-MM-DD HH:MM:SS`.
* A file that is not a real workbook, or is too large, is refused with a reason.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Sequence

from spc.data.csv_io import (
    DEFAULT_TIMESTAMP_FORMATS,
    ColumnMap,
    DataImportError,
    ImportIssue,
    load_csv,
    preview_csv,
)
from spc.data.dataset import Dataset

MAX_ROWS = 500_000
MAX_COLUMNS = 200
ZIP_MAGIC = b"PK\x03\x04"


def is_xlsx(raw: bytes) -> bool:
    """An *.xlsx is a zip file. A CSV never starts with these four bytes."""
    return raw[:4] == ZIP_MAGIC


def _fail(code: str, message: str) -> DataImportError:
    return DataImportError([ImportIssue(None, None, code, message)])


def _cell(v, decimal: str) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        text = repr(v)
        return text.replace(".", ",") if decimal == "," else text
    if isinstance(v, datetime):
        return v.replace(microsecond=0, tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, time):
        return v.replace(microsecond=0, tzinfo=None).strftime("%H:%M:%S")
    return " ".join(str(v).split())  # a line break in a cell would shift the row numbers


def _workbook(raw: bytes):
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - openpyxl is part of the web extra
        raise _fail("xlsx_unavailable", "reading Excel files needs the 'openpyxl' package (pip install spc[excel])") from None
    try:
        return load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises several kinds (zip, xml, key) for a damaged file
        raise _fail("xlsx_unreadable", f"the file is not a readable Excel workbook ({type(exc).__name__})") from None


def sheet_names(raw: bytes) -> list[str]:
    wb = _workbook(raw)
    try:
        return [ws.title for ws in wb.worksheets]
    finally:
        wb.close()


def _table(raw: bytes, sheet: str | None, decimal: str, stop_after: int | None = None) -> tuple[str, list[str], str]:
    """The sheet as CSV text, the names of all sheets, and the name of the sheet that was read."""
    wb = _workbook(raw)
    try:
        names = [ws.title for ws in wb.worksheets]
        if not names:
            raise _fail("xlsx_unreadable", "the workbook has no sheet")
        chosen = sheet or names[0]
        if chosen not in names:
            raise _fail("sheet_not_found", f"sheet {chosen!r} not found. Sheets in the file: {names}")
        out = io.StringIO()
        writer = csv.writer(out, delimiter=",", lineterminator="\n")
        n = 0
        for row in wb[chosen].iter_rows(values_only=True):
            n += 1
            if n > MAX_ROWS:
                raise _fail("sheet_too_large", f"the sheet has more than {MAX_ROWS} rows")
            if len(row) > MAX_COLUMNS:
                row = row[:MAX_COLUMNS]
            writer.writerow([_cell(v, decimal) for v in row])
            if stop_after is not None and n >= stop_after:
                break
        return out.getvalue(), names, chosen
    finally:
        wb.close()


def preview_xlsx(source, *, sheet: str | None = None, header_row: int = 1, n_rows: int = 8) -> dict:
    """Same shape as `preview_csv`, plus the sheets of the workbook."""
    raw = bytes(source) if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    text, names, chosen = _table(raw, sheet, ".", stop_after=header_row + 400)
    out = preview_csv(text.encode("utf-8"), delimiter=",", encoding="utf-8", n_rows=n_rows, header_row=header_row)
    return {**out, "encoding": "xlsx", "delimiter": "", "sheets": names, "sheet": chosen}


def load_xlsx(
    source,
    columns: ColumnMap,
    *,
    sheet: str | None = None,
    header_row: int = 1,
    decimal: str = ".",
    missing: str = "error",
    timestamp_formats: Sequence[str] = DEFAULT_TIMESTAMP_FORMATS,
) -> Dataset:
    """Read one sheet of an *.xlsx workbook into a Dataset (see `load_csv` for the rules).

    `decimal` is for numbers written as text in the sheet (for example "1,25"). Real numeric cells need no setting.
    """
    if isinstance(source, (bytes, bytearray)):
        raw, name = bytes(source), "<bytes>"
    else:
        path = Path(source)
        raw, name = path.read_bytes(), path.name
    text, _, chosen = _table(raw, sheet, decimal)
    ds = load_csv(
        text.encode("utf-8"), columns, delimiter=",", decimal=decimal, encoding="utf-8",
        missing=missing, timestamp_formats=timestamp_formats, header_row=header_row,
    )
    info = replace(ds.source, name=name, sha256=hashlib.sha256(raw).hexdigest(), encoding="xlsx", delimiter=f"[{chosen}]")
    return replace(ds, source=info)

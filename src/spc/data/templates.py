"""Import templates: the column map of a customer's file, saved once and used again.

A template holds what the user would otherwise choose by hand at every import: which sheet and header line, the
delimiter, decimal sign and encoding, and which column is the value, the subgroup, the time and so on. Columns are
named by the header text, never by position, so a file with the columns in another order still fits.

A template fits a file when every column it names is in the header of the file. Nothing else is guessed: the
program offers the fitting templates, the person chooses. Saving, changing and deleting are in the audit chain.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from spc.auth.audit import Audit
from spc.data.csv_io import ColumnMap
from spc.db.database import Database
from spc.db.stores import now_iso

ROLES = ("value", "subgroup", "timestamp", "valid", "invalid_reason", "invalid_by", "invalid_at", "source_row")
FORMATS = ("any", "csv", "xlsx")
ENCODINGS = ("auto", "utf-8", "utf-8-sig", "cp950", "big5", "gbk", "shift_jis", "latin-1", "utf-16")
DELIMITERS = ("", ",", ";", "\t", "|")
MAX_TEMPLATES = 500


class TemplateProblem(ValueError):
    def __init__(self, code: str, message: str = "", status: int = 400, **params):
        super().__init__(message or code)
        self.code, self.status, self.params = code, status, params


class TemplateNotFound(KeyError):
    """No import template with this id."""


class TemplateNameTaken(ValueError):
    """Another template has this name."""


def _dump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def _text(v, name: str, limit: int, required: bool = False) -> str:
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise ValueError(f"{name} must be text")
    v = v.strip()
    if required and not v:
        raise ValueError(f"{name} is required")
    if len(v) > limit:
        raise ValueError(f"{name} is longer than {limit} characters")
    return v


def validate_template(data: dict) -> dict:
    """Check and normalise a template. Raises ValueError with the reason."""
    allowed = {"name", "format", "sheet", "header_row", "delimiter", "decimal", "encoding", "missing", "columns", "tags", "note"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError(f"unknown field(s): {sorted(set(data) - allowed) if isinstance(data, dict) else 'the template must be an object'}")
    fmt = data.get("format", "any")
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {list(FORMATS)}")
    header_row = data.get("header_row", 1)
    if isinstance(header_row, bool) or not isinstance(header_row, int) or not 1 <= header_row <= 1000:
        raise ValueError("header_row must be a whole number from 1 to 1000")
    delimiter = data.get("delimiter", "")
    if delimiter not in DELIMITERS:
        raise ValueError("delimiter must be empty (detect it), a comma, a semicolon, a tab or a bar")
    decimal = data.get("decimal", ".")
    if decimal not in (".", ","):
        raise ValueError("decimal must be '.' or ','")
    encoding = data.get("encoding", "auto")
    if encoding not in ENCODINGS:
        raise ValueError(f"encoding must be one of {list(ENCODINGS)}")
    missing = data.get("missing", "error")
    if missing not in ("error", "skip"):
        raise ValueError("missing must be 'error' or 'skip'")
    cols = data.get("columns")
    if not isinstance(cols, dict) or set(cols) - set(ROLES):
        raise ValueError(f"columns must be an object with the keys {list(ROLES)}")
    columns = {r: _text(cols.get(r), f"column {r}", 200) for r in ROLES}
    if not columns["value"]:
        raise ValueError("the column of the values is required")
    tags = data.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 50:
        raise ValueError("tags must be a list of up to 50 column names")
    tags = [_text(t, "tag column", 200, required=True) for t in tags]
    named = [c for c in columns.values() if c] + tags
    if len(set(named)) != len(named):
        raise ValueError("a column can have one role only")
    return {
        "name": _text(data.get("name"), "name", 80, required=True),
        "format": fmt,
        "sheet": _text(data.get("sheet"), "sheet", 200),
        "header_row": header_row,
        "delimiter": delimiter,
        "decimal": decimal,
        "encoding": encoding,
        "missing": missing,
        "columns": columns,
        "tags": tags,
        "note": _text(data.get("note"), "note", 500),
    }


def column_map(t: dict) -> ColumnMap:
    c = t["columns"]
    return ColumnMap(
        value=c["value"], subgroup=c["subgroup"] or None, timestamp=c["timestamp"] or None, tags=tuple(t["tags"]),
        source_row=c["source_row"] or None, valid=c["valid"] or None, invalid_reason=c["invalid_reason"] or None,
        invalid_by=c["invalid_by"] or None, invalid_at=c["invalid_at"] or None,
    )


def named_columns(t: dict) -> list[str]:
    return [c for c in t["columns"].values() if c] + list(t["tags"])


def fits(t: dict, header: list[str], kind: str) -> bool:
    """The file is of the format the template is for, and every column the template names is in its header."""
    if t["format"] not in ("any", kind):
        return False
    have = set(header)
    return all(c in have for c in named_columns(t))


class ImportTemplates:
    def __init__(self, db: Database, audit: Audit):
        self.db, self.audit = db, audit

    @staticmethod
    def _row(r) -> dict:
        return {"id": r["id"], **json.loads(r["data"]), "updated_at": r["updated_at"]}

    def list(self) -> list[dict]:
        return [self._row(r) for r in self.db.all("SELECT * FROM import_templates ORDER BY name COLLATE NOCASE LIMIT ?", (MAX_TEMPLATES,))]

    def get(self, template_id: int) -> dict:
        r = self.db.one("SELECT * FROM import_templates WHERE id = ?", (template_id,))
        if r is None:
            raise TemplateNotFound(template_id)
        return self._row(r)

    def matching(self, header: list[str], kind: str) -> list[dict]:
        """The templates that fit this file. The ones that name more columns come first (they are the more specific)."""
        found = [t for t in self.list() if fits(t, header, kind)]
        return sorted(found, key=lambda t: (-len(named_columns(t)), t["name"].lower()))

    def save(self, data: dict, user, template_id: int | None = None) -> dict:
        """Create a template, or replace the one with `template_id`."""
        try:
            t = validate_template(data)
        except ValueError as exc:
            raise TemplateProblem("invalid_input", str(exc)) from None
        now = now_iso()
        with self.db.tx():
            if template_id is not None:
                self.get(template_id)
            elif self.db.one("SELECT COUNT(*) AS n FROM import_templates")["n"] >= MAX_TEMPLATES:
                raise TemplateProblem("invalid_input", f"there are {MAX_TEMPLATES} templates already")
            try:
                if template_id is None:
                    cur = self.db.execute(
                        "INSERT INTO import_templates (name, data, created_at, updated_at, created_by) VALUES (?, ?, ?, ?, ?)",
                        (t["name"], _dump(t), now, now, user.id),
                    )
                    template_id = cur.lastrowid
                    action = "import_template_created"
                else:
                    self.db.execute("UPDATE import_templates SET name = ?, data = ?, updated_at = ? WHERE id = ?", (t["name"], _dump(t), now, template_id))
                    action = "import_template_changed"
            except sqlite3.IntegrityError:
                raise TemplateNameTaken(t["name"]) from None
            self.audit.append(action, user_id=user.id, username=user.username, target=str(template_id), detail={"name": t["name"]})
        return self.get(template_id)

    def delete(self, template_id: int, user) -> None:
        with self.db.tx():
            t = self.get(template_id)
            self.db.execute("DELETE FROM import_templates WHERE id = ?", (template_id,))
            self.audit.append("import_template_deleted", user_id=user.id, username=user.username, target=str(template_id), detail={"name": t["name"]})

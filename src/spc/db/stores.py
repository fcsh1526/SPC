"""Datasets and reports in the database.

A dataset is stored as one JSON document (the same form the archive uses). Datasets are immutable
in memory: a mark makes a new dataset, which replaces the stored one and raises `revision`.
`modify` reads, changes and writes in one transaction, so two people marking at the same time
cannot overwrite each other.
A report is a snapshot. It keeps its own copy of the data and does not change when the dataset does.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from spc.data import Dataset
from spc.data.serialize import dataset_from_dict, dataset_to_dict
from spc.db.database import Database


class DatasetNotFound(KeyError):
    """No dataset with this id."""


class ReportNotFound(KeyError):
    """No report with this id."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class DatasetStore:
    def __init__(self, db: Database):
        self.db = db

    def add(self, dataset: Dataset, owner_id: int, name: str = "") -> str:
        key = uuid.uuid4().hex
        stamp = now_iso()
        label = (name or (dataset.source.name if dataset.source else "") or "dataset")[:200]
        self.db.execute(
            "INSERT INTO datasets (id, owner_id, name, created_at, updated_at, revision, n_total, n_invalid, data)"
            " VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (key, owner_id, label, stamp, stamp, dataset.n_total, len(dataset.invalid_info()), _dump(dataset_to_dict(dataset))),
        )
        return key

    def get(self, key: str) -> Dataset:
        row = self.db.one("SELECT data FROM datasets WHERE id = ?", (key,))
        if row is None:
            raise DatasetNotFound(key)
        return dataset_from_dict(json.loads(row["data"]))

    def modify(self, key: str, change: Callable[[Dataset], Dataset]) -> Dataset:
        """Apply `change` to the stored dataset and store the result, in one transaction."""
        with self.db.tx():
            row = self.db.one("SELECT data FROM datasets WHERE id = ?", (key,))
            if row is None:
                raise DatasetNotFound(key)
            new = change(dataset_from_dict(json.loads(row["data"])))
            self.db.execute(
                "UPDATE datasets SET data = ?, updated_at = ?, revision = revision + 1, n_total = ?, n_invalid = ? WHERE id = ?",
                (_dump(dataset_to_dict(new)), now_iso(), new.n_total, len(new.invalid_info()), key),
            )
            return new

    def owner(self, key: str) -> int:
        row = self.db.one("SELECT owner_id FROM datasets WHERE id = ?", (key,))
        if row is None:
            raise DatasetNotFound(key)
        return row["owner_id"]

    def delete(self, key: str) -> None:
        if self.db.execute("DELETE FROM datasets WHERE id = ?", (key,)).rowcount == 0:
            raise DatasetNotFound(key)

    def list(self, limit: int = 200) -> list[dict]:
        rows = self.db.all(
            "SELECT d.id, d.name, d.created_at, d.updated_at, d.revision, d.n_total, d.n_invalid, u.username AS owner"
            " FROM datasets d JOIN users u ON u.id = d.owner_id ORDER BY d.updated_at DESC, d.rowid DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def __len__(self) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM datasets")["n"]


class ReportStore:
    def __init__(self, db: Database):
        self.db = db

    def add(self, generated, dataset_id: str, owner_id: int) -> None:
        self.db.execute(
            "INSERT INTO reports (id, dataset_id, owner_id, created_at, language, digest, html, archive) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (generated.report_id, dataset_id, owner_id, now_iso(), generated.language,
             generated.archive["integrity"]["digest"], generated.html, _dump(generated.archive)),
        )

    def get(self, report_id: str):
        """(report_id, language, html, archive dict) of a stored report."""
        row = self.db.one("SELECT id, language, html, archive FROM reports WHERE id = ?", (report_id,))
        if row is None:
            raise ReportNotFound(report_id)
        return row["id"], row["language"], row["html"], json.loads(row["archive"])

    def list(self, limit: int = 200) -> list[dict]:
        rows = self.db.all(
            "SELECT r.id, r.dataset_id, r.created_at, r.language, r.digest, u.username AS owner"
            " FROM reports r JOIN users u ON u.id = r.owner_id ORDER BY r.created_at DESC, r.rowid DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]


class ProfileNotFound(KeyError):
    """No customer profile with this id."""


class ProfileNameTaken(ValueError):
    """Another profile has this name."""


class ProfileStore:
    """Customer profiles (see `spc.profile`). The data is one JSON document: {"analysis": {...}, "report": {...}}.
    A change raises the revision number. Old revisions are not kept: a report keeps its own snapshot."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(r) -> dict:
        return {"id": r["id"], "name": r["name"], "revision": r["revision"], "created_at": r["created_at"],
                "updated_at": r["updated_at"], **json.loads(r["data"])}

    def list(self) -> list[dict]:
        return [self._row(r) for r in self.db.all("SELECT * FROM profiles ORDER BY name COLLATE NOCASE")]

    def get(self, profile_id: int) -> dict:
        r = self.db.one("SELECT * FROM profiles WHERE id = ?", (profile_id,))
        if r is None:
            raise ProfileNotFound(profile_id)
        return self._row(r)

    def create(self, name: str, analysis: dict, report: dict, user_id: int) -> dict:
        stamp = now_iso()
        with self.db.tx():
            if self.db.one("SELECT 1 FROM profiles WHERE name = ?", (name,)):
                raise ProfileNameTaken(name)
            cur = self.db.execute(
                "INSERT INTO profiles (name, revision, data, created_at, updated_at, created_by) VALUES (?, 1, ?, ?, ?, ?)",
                (name, _dump({"analysis": analysis, "report": report}), stamp, stamp, user_id))
            return self.get(cur.lastrowid)

    def update(self, profile_id: int, name: str, analysis: dict, report: dict) -> dict:
        with self.db.tx():
            self.get(profile_id)
            clash = self.db.one("SELECT id FROM profiles WHERE name = ? AND id != ?", (name, profile_id))
            if clash:
                raise ProfileNameTaken(name)
            self.db.execute(
                "UPDATE profiles SET name = ?, data = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                (name, _dump({"analysis": analysis, "report": report}), now_iso(), profile_id))
            return self.get(profile_id)

    def delete(self, profile_id: int) -> None:
        if self.db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,)).rowcount == 0:
            raise ProfileNotFound(profile_id)

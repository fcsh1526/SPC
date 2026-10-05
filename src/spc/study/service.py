"""Machine performance studies: records, checklist items, closing. Every change is written together with its audit entry."""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from spc.auth.audit import Audit
from spc.db.database import Database
from spc.db.stores import DatasetNotFound, DatasetStore, now_iso
from spc.study import checklist as cl
from spc.study.checklist import StudyError


class StudyNotFound(KeyError):
    """No study with this id."""


class StudyNameTaken(ValueError):
    """Another study has this name."""


def _label(user) -> str:
    return f"{user.display_name or user.username} ({user.username})" if getattr(user, "display_name", "") else user.username


def _dump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


class StudyService:
    def __init__(self, db: Database, audit: Audit, datasets: DatasetStore, msa=None):
        self.db, self.audit, self.datasets, self.msa = db, audit, datasets, msa

    def _log(self, action: str, user, target: str, detail: dict) -> None:
        self.audit.append(action, user_id=user.id, username=user.username, target=target, detail=detail)

    # ------------------------------------------------------------------ storage
    @staticmethod
    def _row(r) -> dict:
        return {"id": r["id"], "revision": r["revision"], "created_at": r["created_at"], "updated_at": r["updated_at"], **json.loads(r["data"])}

    def get(self, study_id: int) -> dict:
        r = self.db.one("SELECT * FROM studies WHERE id = ?", (study_id,))
        if r is None:
            raise StudyNotFound(study_id)
        return self._row(r)

    def list(self) -> list[dict]:
        out = []
        for r in self.db.all("SELECT * FROM studies ORDER BY updated_at DESC, id DESC"):
            s = self._row(r)
            ev = self._evaluate(s)
            out.append({k: s[k] for k in ("id", "name", "machine", "characteristic", "station", "updated_at", "closed")} |
                       {"ready": ev["ready"], "blockers": len(ev["blockers"]), "flagged": len(ev["flagged"])})
        return out

    def _save(self, study_id: int, data: dict) -> None:
        self.db.execute("UPDATE studies SET data = ?, name = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                        (_dump(data), data["name"], now_iso(), study_id))

    @staticmethod
    def _payload(study: dict) -> dict:
        return {k: v for k, v in study.items() if k not in ("id", "revision", "created_at", "updated_at")}

    # ------------------------------------------------------------------ evaluation
    def _facts(self, record: dict):
        ds_id = record.get("dataset_id")
        if not ds_id:
            return None, False
        try:
            ds = self.datasets.get(ds_id)
        except DatasetNotFound:
            return None, True
        from spc.service.analysis import _normality

        vals = ds.values[ds.valid_mask]
        return cl.facts_from_dataset(ds, _normality(np.asarray(vals, dtype=float)) if vals.size >= 3 else None), False

    def _msa(self, study: dict):
        sid = study.get("measurement_system_id")
        if not sid or self.msa is None:
            return None
        try:
            system = self.msa.view(sid)
        except KeyError:
            return {"state": "missing"}
        return {"state": "gate", "gate": system["gate"], "name": system["system"]["name"]}

    def _evaluate(self, study: dict) -> dict:
        facts, missing = self._facts(study)
        return cl.evaluate(study, study.get("items", {}), facts, missing, self._msa(study))

    def view(self, study_id: int) -> dict:
        study = self.get(study_id)
        return {"study": study, "evaluation": self._evaluate(study)}

    # ------------------------------------------------------------------ changes
    def create(self, record_in: dict, user) -> dict:
        try:
            record = cl.validate_record(record_in)
        except ValueError as exc:
            raise StudyError("invalid_input", str(exc)) from None
        self._check_dataset(record)
        data = {**record, "items": {}, "closed": None}
        with self.db.tx():
            if self.db.one("SELECT 1 FROM studies WHERE name = ?", (record["name"],)):
                raise StudyNameTaken(record["name"])
            stamp = now_iso()
            cur = self.db.execute("INSERT INTO studies (name, revision, data, created_at, updated_at, created_by) VALUES (?, 1, ?, ?, ?, ?)",
                                  (record["name"], _dump(data), stamp, stamp, user.id))
            self._log("study_created", user, record["name"], {"id": cur.lastrowid})
        return self.view(cur.lastrowid)

    def _check_dataset(self, record: dict) -> None:
        if record.get("measurement_system_id") and self.msa is not None:
            try:
                self.msa.get(record["measurement_system_id"])
            except KeyError:
                raise StudyError("msa_system_not_found", "the measurement system does not exist", 404) from None
        if record["dataset_id"]:
            try:
                self.datasets.get(record["dataset_id"])
            except DatasetNotFound:
                raise StudyError("dataset_not_found", "the data set does not exist", 404) from None

    def update(self, study_id: int, record_in: dict, user) -> dict:
        study = self.get(study_id)
        if study["closed"]:
            raise StudyError("study_closed", "the study is closed: reopen it with a reason first", 409)
        try:
            record = cl.validate_record(record_in)
        except ValueError as exc:
            raise StudyError("invalid_input", str(exc)) from None
        self._check_dataset(record)
        with self.db.tx():
            clash = self.db.one("SELECT id FROM studies WHERE name = ? AND id != ?", (record["name"], study_id))
            if clash:
                raise StudyNameTaken(record["name"])
            self._save(study_id, {**self._payload(study), **record})
            self._log("study_updated", user, record["name"], {"id": study_id})
        return self.view(study_id)

    def set_item(self, study_id: int, key: str, status: str, note: str, user) -> dict:
        study = self.get(study_id)
        if study["closed"]:
            raise StudyError("study_closed", "the study is closed: reopen it with a reason first", 409)
        if key not in cl.BY_KEY:
            raise StudyError("unknown_item", "no such checklist item", 404)
        if status not in cl.STATUSES:
            raise StudyError("invalid_input", f"status must be one of {cl.STATUSES}")
        if len(note) > 2000:
            raise StudyError("invalid_input", "the note is longer than 2000 characters")
        if status in ("not_applicable", "deviation") and len(note.strip()) < 3:
            raise StudyError("note_required", "a note is required: say why this does not apply, or what was agreed")
        items = dict(study.get("items", {}))
        if status == "open" and not note.strip():
            items.pop(key, None)
        else:
            items[key] = {"status": status, "note": note.strip(), "by": _label(user), "at": now_iso()}
        with self.db.tx():
            self._save(study_id, {**self._payload(study), "items": items})
            self._log("study_item", user, study["name"], {"id": study_id, "item": key, "status": status, "note": note.strip()})
        return self.view(study_id)

    def close(self, study_id: int, reason: str, user) -> dict:
        study = self.get(study_id)
        if study["closed"]:
            raise StudyError("study_closed", "the study is closed already", 409)
        if not reason.strip():
            raise StudyError("reason_required", "a statement is required to close the study")
        ev = self._evaluate(study)
        if not ev["ready"]:
            raise StudyError("study_not_ready", "items are still open or failed", 409, blockers=ev["blockers"])
        closed = {"by": _label(user), "at": now_iso(), "reason": reason.strip(), "flagged": ev["flagged"]}
        with self.db.tx():
            self._save(study_id, {**self._payload(study), "closed": closed})
            self._log("study_closed", user, study["name"], {"id": study_id, "reason": reason.strip(), "flagged": ev["flagged"]})
        return self.view(study_id)

    def reopen(self, study_id: int, reason: str, user) -> dict:
        study = self.get(study_id)
        if not study["closed"]:
            raise StudyError("study_not_closed", "the study is open", 409)
        if not reason.strip():
            raise StudyError("reason_required", "a reason is required to reopen the study")
        with self.db.tx():
            self._save(study_id, {**self._payload(study), "closed": None})
            self._log("study_reopened", user, study["name"], {"id": study_id, "reason": reason.strip()})
        return self.view(study_id)

    def delete(self, study_id: int, user) -> None:
        study = self.get(study_id)
        with self.db.tx():
            self.db.execute("DELETE FROM studies WHERE id = ?", (study_id,))
            self._log("study_deleted", user, study["name"], {"id": study_id})

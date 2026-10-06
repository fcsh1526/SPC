"""Reference cases and validation runs. A run keeps what it ran (the cases as they were), what it found, and a digest over the record."""

from __future__ import annotations

import hashlib
import json
import platform
from typing import Any

import numpy
import scipy

from spc import __version__
from spc.auth.audit import Audit
from spc.db.database import Database
from spc.db.stores import now_iso
from spc.params import AnalysisParams
from spc.validation import checks as C
from spc.validation import custom as U
from spc.validation import iso11462 as ISO


class ValidationError(ValueError):
    def __init__(self, code: str, message: str = "", status: int = 400, **params):
        super().__init__(message or code)
        self.code, self.status, self.params = code, status, params


def _dump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest_of(record: dict) -> str:
    return hashlib.sha256(_dump(record).encode("utf-8")).hexdigest()


def _label(user) -> str:
    return f"{user.display_name or user.username} ({user.username})" if getattr(user, "display_name", "") else user.username


class ValidationService:
    def __init__(self, db: Database, audit: Audit):
        self.db, self.audit = db, audit

    # ------------------------------------------------------------------ reference cases
    @staticmethod
    def _case(r) -> dict:
        return {"id": r["id"], "created_at": r["created_at"], "updated_at": r["updated_at"], **json.loads(r["data"])}

    def get_case(self, case_id: int) -> dict:
        r = self.db.one("SELECT * FROM validation_cases WHERE id = ?", (case_id,))
        if r is None:
            raise ValidationError("validation_case_not_found", "reference case not found", 404)
        return self._case(r)

    def list_cases(self) -> list[dict]:
        return [{k: v for k, v in self._case(r).items() if k != "values"} | {"n_values": len(json.loads(r["data"])["values"])}
                for r in self.db.all("SELECT * FROM validation_cases ORDER BY name COLLATE NOCASE")]

    def _validated(self, record: dict) -> dict:
        try:
            return U.validate_case(record)
        except ValueError as exc:
            raise ValidationError("invalid_input", str(exc)) from None

    def create_case(self, record_in: dict, user) -> dict:
        record = self._validated(record_in)
        with self.db.tx():
            if self.db.one("SELECT 1 FROM validation_cases WHERE name = ?", (record["name"],)):
                raise ValidationError("validation_case_name_taken", "a case with this name exists already", 409)
            stamp = now_iso()
            cur = self.db.execute("INSERT INTO validation_cases (name, data, created_at, updated_at, created_by) VALUES (?, ?, ?, ?, ?)",
                                  (record["name"], _dump(record), stamp, stamp, user.id))
            self.audit.append("validation_case_created", user_id=user.id, username=user.username, target=record["name"], detail={"id": cur.lastrowid})
        return self.get_case(cur.lastrowid)

    def update_case(self, case_id: int, record_in: dict, user) -> dict:
        old = self.get_case(case_id)
        record = self._validated(record_in)
        with self.db.tx():
            if self.db.one("SELECT 1 FROM validation_cases WHERE name = ? AND id != ?", (record["name"], case_id)):
                raise ValidationError("validation_case_name_taken", "a case with this name exists already", 409)
            self.db.execute("UPDATE validation_cases SET name = ?, data = ?, updated_at = ? WHERE id = ?", (record["name"], _dump(record), now_iso(), case_id))
            self.audit.append("validation_case_updated", user_id=user.id, username=user.username, target=record["name"], detail={"id": case_id, "was": old["name"]})
        return self.get_case(case_id)

    def delete_case(self, case_id: int, user) -> None:
        old = self.get_case(case_id)
        with self.db.tx():
            self.db.execute("DELETE FROM validation_cases WHERE id = ?", (case_id,))
            self.audit.append("validation_case_deleted", user_id=user.id, username=user.username, target=old["name"], detail={"id": case_id})

    # ------------------------------------------------------------------ runs
    def run(self, user) -> dict:
        builtin = C.run_builtin()
        cases = [self.get_case(r["id"]) for r in self.db.all("SELECT id FROM validation_cases ORDER BY name COLLATE NOCASE")]
        case_results = []
        for case in cases:
            checks = U.run_case(case["id"], case)
            case_results.append({"id": case["id"], "name": case["name"], "description": case["description"], "source": case["source"],
                                 "definition": {k: case[k] for k in ("values", "subgroup_size", "request", "expected")}, "checks": [c.to_dict() for c in checks]})
        v_checks = [c for cr in case_results for c in cr["checks"]]
        failed_v = sum(not c.ok for c in builtin)
        failed_c = sum(not c["ok"] for c in v_checks)
        record = {
            "engine_version": __version__, "python": platform.python_version(), "numpy": numpy.__version__, "scipy": scipy.__version__,
            "parameters": {k: v for k, v in AnalysisParams().to_dict().items() if k not in ("engine_version",)},
            "verification": {"checks": [c.to_dict() for c in builtin], "failed": failed_v, "status": "pass" if not failed_v else "fail"},
            "validation": {"cases": case_results, "failed": failed_c, "status": "none" if not case_results else "pass" if not failed_c else "fail"},
            "created_by": _label(user), "created_at": now_iso(),
        }
        record["verdict"] = "fail" if failed_v or failed_c else "verified" if not case_results else "pass"
        digest = digest_of(record)
        with self.db.tx():
            cur = self.db.execute("INSERT INTO validation_runs (data, digest, created_at, created_by) VALUES (?, ?, ?, ?)", (_dump(record), digest, record["created_at"], user.id))
            self.audit.append("validation_run", user_id=user.id, username=user.username, target=str(cur.lastrowid),
                              detail={"id": cur.lastrowid, "verdict": record["verdict"], "failed_verification": failed_v, "failed_validation": failed_c,
                                      "cases": len(case_results), "engine": __version__, "digest": digest})
        return self.get_run(cur.lastrowid)

    def get_run(self, run_id: int) -> dict:
        r = self.db.one("SELECT * FROM validation_runs WHERE id = ?", (run_id,))
        if r is None:
            raise ValidationError("validation_run_not_found", "validation run not found", 404)
        record = json.loads(r["data"])
        return {"id": r["id"], "digest": r["digest"], "intact": digest_of(record) == r["digest"], **record}

    def list_runs(self) -> list[dict]:
        out = []
        for r in self.db.all("SELECT id, data, digest, created_at FROM validation_runs ORDER BY id DESC LIMIT 100"):
            d = json.loads(r["data"])
            out.append({"id": r["id"], "created_at": r["created_at"], "created_by": d["created_by"], "engine_version": d["engine_version"], "verdict": d["verdict"],
                        "failed": d["verification"]["failed"] + d["validation"]["failed"], "cases": len(d["validation"]["cases"]), "digest": r["digest"]})
        return out

    # ------------------------------------------------------------------ ISO/TR 11462-3
    def iso_examples(self) -> list[dict]:
        """The eleven examples: what is known of each, whether the user entered it as a reference case, and what the last run found."""
        cases = {c["name"]: c["id"] for c in self.list_cases()}
        last = self.db.one("SELECT data FROM validation_runs ORDER BY id DESC LIMIT 1")
        results: dict[str, str] = {}
        if last:
            for c in json.loads(last["data"])["validation"]["cases"]:
                results[c["name"]] = "pass" if all(x["ok"] for x in c["checks"]) else "fail"
        out = []
        for e in ISO.CATALOGUE:
            name = ISO.case_name(e["number"])
            out.append({**e, "name": name, "source": ISO.SOURCE.format(n=e["number"]), "case_id": cases.get(name), "last": results.get(name)})
        return out

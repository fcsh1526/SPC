"""FastAPI application: JSON API under /api and the web user interface under /.

Errors come back as {"error": {"code", "message", "params"}}. The code is stable and the user
interface turns it into text in the user's language. The message is English, for logs and developers.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Literal

import json
import secrets

import numpy as np
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from spc import __version__
from spc.api.accounts import add_account_routes
from spc.api.monitors import add_monitor_routes
from spc.api.studies import add_study_routes
from spc.api.msa import add_msa_routes
from spc.api.plans import add_plan_routes
from spc.api.validation import add_validation_routes
from spc.api.equipment import add_equipment_routes
from spc.service.state_tests import state_tests_for_dataset
from spc.equipment.model import EquipmentError
from spc.equipment.service import EquipmentService
from spc.validation.service import ValidationError, ValidationService
from spc.plan.model import PlanError
from spc.plan.service import PeopleService, PlanNameTaken, PlanNotFound, PlanService
from spc.msa.service import MsaProblem, MsaService, SystemNameTaken, SystemNotFound
from spc.api.errors import ApiError, error_response as _error
from spc.service.model_suggestion import suggest_for_dataset
from spc.api.schemas import (
    StateTestsBody,
    TimeModelBody,
    AnalyzeBody,
    ArlBody,
    AttributeBody,
    MarkBody,
    ProfileBody,
    RestartBody,
    ReportBody,
    SuspectsBody,
    TargetBody,
)
from spc.auth import Audit, AuthError, AuthService, User
from spc.monitor.model import MonitorError
from spc.monitor.notify import Notifier
from spc.monitor.service import MonitorService
from spc.monitor.store import IncidentNotFound, MonitorNameTaken, MonitorNotFound
from spc.study.checklist import StudyError
from spc.study.service import StudyNameTaken, StudyNotFound, StudyService
from spc.core.arl_oc import alarm_probability, arl, required_subgroup_size
from spc.core.capability import Stage, TargetAdjustmentNotAllowed, required_targets
from spc.core.charts import attribute as attr
from spc.db import (
    Database, DatasetNotFound, DatasetStore, ProfileNameTaken, ProfileNotFound, ProfileStore, ReportNotFound, ReportStore,
)
from spc.data import ColumnMap, DataImportError, Dataset, IncompleteSubgroupsError, load_csv, preview_csv, suspects, to_csv
from spc.profile import ReportTemplate, default_target_table, missing_fields, snapshot, validate_analysis
from spc.report import ReportError, generate, report_xlsx, reproduce
from spc.service import analyze

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"
LANGUAGES = ("zh-TW", "en")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
}


SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
PUBLIC = {("GET", "/api/meta"), ("POST", "/api/auth/login")}  # everything else under /api needs a session
ALLOWED_BEFORE_PASSWORD_CHANGE = {("GET", "/api/meta"), ("GET", "/api/auth/me"), ("POST", "/api/auth/logout"), ("POST", "/api/auth/password")}


def _dataset_json(key: str, ds: Dataset) -> dict:
    return {
        "id": key,
        "summary": ds.summary(),
        "source": None if ds.source is None else asdict(ds.source),
        "has_subgroup": ds.subgroup is not None,
        "has_timestamp": ds.timestamp is not None,
        "tags": list(ds.tags),
        "warnings": [{"code": f"data_{w.code}", "params": dict(w.params)} for w in ds.warnings],
        "log": [asdict(e) for e in ds.log],
        "restarts": [{"pos": p, "reason": r, "by": b, "at": a, "new_limits": p in ds.phase_positions()}
                     for p, (r, b, a) in ds.restart_info().items()],
    }


def _row_json(ds: Dataset, i: int, info: dict, restarts: dict | None = None) -> dict:
    mark = info.get(i)
    restart = (restarts or {}).get(i)
    phases = ds.phase_positions() if restarts else set()
    return {
        "pos": i,
        "source_row": int(ds.source_rows[i]),
        "value": float(ds.values[i]),
        "subgroup": None if ds.subgroup is None else str(ds.subgroup[i]),
        "timestamp": None if ds.timestamp is None else str(ds.timestamp[i]).replace("T", " "),
        "tags": {k: str(v[i]) for k, v in ds.tags.items()},
        "valid": mark is None,
        "invalid": None if mark is None else {"reason": mark[0], "by": mark[1], "at": mark[2]},
        "restart": None if restart is None else {"reason": restart[0], "by": restart[1], "at": restart[2],
                                                 "new_limits": i in phases},
    }


REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'"  # no script at all


def create_app(
    db: Database | None = None,
    max_upload: int = MAX_UPLOAD_BYTES,
    secure_cookies: bool | None = None,
    auth: AuthService | None = None,
    notifiers: list[Notifier] | None = None,
) -> FastAPI:
    """`db` defaults to a private in-memory database. `secure_cookies` None means: Secure when the request is https."""
    db = Database() if db is None else db
    audit = Audit(db)
    auth = AuthService(db, audit) if auth is None else auth

    def authenticate(request: Request) -> None:
        """Runs before every route. A route is protected unless it is listed in PUBLIC."""
        key = (request.method, request.url.path)
        if key in PUBLIC or not key[1].startswith("/api/"):  # the page itself must load to show the login form
            return
        info = auth.session(request.cookies.get("spc_session"))
        if info is None:
            raise ApiError(401, "not_authenticated", "login required")
        if request.method not in SAFE_METHODS and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), info.csrf):
            raise ApiError(403, "csrf_failed", "missing or wrong CSRF token")
        if info.user.must_change and key not in ALLOWED_BEFORE_PASSWORD_CHANGE:
            raise ApiError(403, "password_change_required", "the password must be changed first")
        request.state.session = info

    def require(role: str):
        def check(request: Request) -> User:
            user = request.state.session.user
            if not user.can(role):
                raise ApiError(403, "forbidden", f"this needs the {role} role", role=role)
            return user

        return check

    reader, operator, writer, admin = require("viewer"), require("operator"), require("engineer"), require("admin")

    app = FastAPI(title="SPC", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json",
                  dependencies=[Depends(authenticate)])
    store = DatasetStore(db)
    reports = ReportStore(db)
    profiles = ProfileStore(db)
    app.state.db, app.state.auth, app.state.audit, app.state.store, app.state.reports = db, auth, audit, store, reports
    app.state.profiles = profiles
    msa_systems = MsaService(db, audit)
    app.state.msa = msa_systems
    monitors = MonitorService(db, audit, store, notifiers or [], msa_systems)
    app.state.monitors = monitors
    studies = StudyService(db, audit, store, msa_systems)
    app.state.studies = studies
    people = PeopleService(db, audit, auth)
    plan_service = PlanService(db, audit, people, monitors, msa_systems)
    app.state.people, app.state.plans = people, plan_service
    validation_service = ValidationService(db, audit)
    app.state.validation = validation_service
    equipment_service = EquipmentService(db, audit, monitors, auth)
    app.state.equipment = equipment_service

    # ------------------------------------------------------------------ plumbing

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            if not request.url.path.startswith("/api/docs"):  # the docs page loads its own assets
                response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")  # answers depend on the login
        return response

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return _error(exc.status, exc.code, exc.message, exc.params)

    @app.exception_handler(DataImportError)
    async def _import_error(_: Request, exc: DataImportError):
        issues = [{"line": i.line, "column": i.column, "code": i.code, "message": i.message} for i in exc.issues[:200]]
        return _error(400, "import_failed", str(exc), {"issues": issues, "n_issues": len(exc.issues)})

    @app.exception_handler(IncompleteSubgroupsError)
    async def _incomplete(_: Request, exc: IncompleteSubgroupsError):
        labels = [d.label for d in exc.details]
        return _error(400, "incomplete_subgroups", str(exc), {"labels": labels[:20], "count": len(labels)})

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        errors = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()]
        return _error(422, "validation", "invalid request", {"errors": errors})

    @app.exception_handler(AuthError)
    async def _auth_error(_: Request, exc: AuthError):
        return _error(exc.status, exc.code, exc.message, exc.params)

    @app.exception_handler(DatasetNotFound)
    async def _missing(_: Request, exc: DatasetNotFound):
        return _error(404, "dataset_not_found", "dataset not found")

    @app.exception_handler(MonitorError)
    async def _monitor_error(_: Request, exc: MonitorError):
        return _error(exc.status, exc.code, str(exc), exc.params)

    @app.exception_handler(MonitorNotFound)
    async def _monitor_missing(_: Request, exc: MonitorNotFound):
        return _error(404, "monitor_not_found", "monitor not found")

    @app.exception_handler(IncidentNotFound)
    async def _incident_missing(_: Request, exc: IncidentNotFound):
        return _error(404, "incident_not_found", "incident not found")

    @app.exception_handler(PlanError)
    async def _plan_error(_: Request, exc: PlanError):
        return _error(exc.status, exc.code, str(exc), exc.params)

    @app.exception_handler(EquipmentError)
    async def _equipment_error(_: Request, exc: EquipmentError):
        return _error(exc.status, exc.code, str(exc), exc.params)

    @app.exception_handler(ValidationError)
    async def _validation_error(_: Request, exc: ValidationError):
        return _error(exc.status, exc.code, str(exc), exc.params)

    @app.exception_handler(PlanNotFound)
    async def _plan_missing(_: Request, exc: PlanNotFound):
        return _error(404, "plan_not_found", "control plan not found")

    @app.exception_handler(PlanNameTaken)
    async def _plan_taken(_: Request, exc: PlanNameTaken):
        return _error(409, "plan_name_taken", "a control plan with this name exists already")

    @app.exception_handler(MsaProblem)
    async def _msa_problem(_: Request, exc: MsaProblem):
        return _error(exc.status, exc.code, str(exc), exc.params)

    @app.exception_handler(SystemNotFound)
    async def _msa_missing(_: Request, exc: SystemNotFound):
        return _error(404, "msa_system_not_found", "measurement system not found")

    @app.exception_handler(SystemNameTaken)
    async def _msa_taken(_: Request, exc: SystemNameTaken):
        return _error(409, "msa_system_name_taken", "a measurement system with this name exists already")

    @app.exception_handler(StudyError)
    async def _study_error(_: Request, exc: StudyError):
        return _error(exc.status, exc.code, str(exc), exc.params)

    @app.exception_handler(StudyNotFound)
    async def _study_missing(_: Request, exc: StudyNotFound):
        return _error(404, "study_not_found", "study not found")

    @app.exception_handler(StudyNameTaken)
    async def _study_taken(_: Request, exc: StudyNameTaken):
        return _error(409, "study_name_taken", "a study with this name exists already")

    @app.exception_handler(MonitorNameTaken)
    async def _monitor_taken(_: Request, exc: MonitorNameTaken):
        return _error(409, "monitor_name_taken", "a monitor with this name exists already")

    @app.exception_handler(ProfileNotFound)
    async def _profile_missing(_: Request, exc: ProfileNotFound):
        return _error(404, "profile_not_found", "customer profile not found")

    @app.exception_handler(ProfileNameTaken)
    async def _profile_taken(_: Request, exc: ProfileNameTaken):
        return _error(409, "profile_name_taken", "a customer profile with this name exists already")

    @app.exception_handler(ReportNotFound)
    async def _report_missing(_: Request, exc: ReportNotFound):
        return _error(404, "report_not_found", "report not found")

    @app.exception_handler(ReportError)
    async def _report_error(_: Request, exc: ReportError):
        code = {"spec_missing": "report_needs_spec"}.get(exc.code, "invalid_input")
        return _error(400, code, str(exc))

    @app.exception_handler(ValueError)
    async def _value(_: Request, exc: ValueError):
        return _error(400, "invalid_input", str(exc))

    async def read_body(request: Request) -> bytes:
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_upload:
            raise ApiError(413, "file_too_large", "file is too large", limit_mb=max_upload // (1024 * 1024))
        raw = await request.body()
        if len(raw) > max_upload:
            raise ApiError(413, "file_too_large", "file is too large", limit_mb=max_upload // (1024 * 1024))
        if not raw:
            raise ApiError(400, "empty_file", "the request body is empty")
        return raw

    # ------------------------------------------------------------------ meta

    @app.get("/api/meta")
    def meta(request: Request):
        """Public. Also tells the page whether it is signed in, so a first visit makes no failing request."""
        info = auth.session(request.cookies.get("spc_session"))
        return {"version": __version__, "languages": list(LANGUAGES), "max_upload_mb": max_upload // (1024 * 1024),
                "setup_needed": auth.user_count() == 0, "default_targets": default_target_table(),
                "session": None if info is None else {"user": info.user.to_json(), "csrf": info.csrf}}

    add_account_routes(app, auth, audit, admin, secure_cookies)
    add_monitor_routes(app, monitors, store, reports, audit, db, reader, operator, writer, admin)
    add_study_routes(app, studies, reader, writer, admin)
    add_msa_routes(app, msa_systems, reader, writer, admin)
    add_plan_routes(app, plan_service, people, reader, writer, admin)
    add_validation_routes(app, validation_service, reader, writer, admin)
    add_equipment_routes(app, equipment_service, reader, writer, admin)

    # ------------------------------------------------------------------ import

    @app.post("/api/preview")
    async def preview(request: Request, encoding: str = "auto", delimiter: str | None = None, _: User = Depends(writer)):
        return preview_csv(await read_body(request), delimiter=delimiter or None, encoding=encoding)

    @app.post("/api/datasets")
    async def create_dataset(
        request: Request,
        value: str,
        subgroup: str | None = None,
        timestamp: str | None = None,
        tags: list[str] = Query(default=[]),
        source_row: str | None = None,
        valid: str | None = None,
        invalid_reason: str | None = None,
        invalid_by: str | None = None,
        invalid_at: str | None = None,
        decimal: str = ".",
        delimiter: str | None = None,
        encoding: str = "auto",
        missing: str = "error",
        filename: str | None = None,
        user: User = Depends(writer),
    ):
        raw = await read_body(request)
        columns = ColumnMap(
            value=value, subgroup=subgroup or None, timestamp=timestamp or None, tags=tuple(tags),
            source_row=source_row or None, valid=valid or None, invalid_reason=invalid_reason or None,
            invalid_by=invalid_by or None, invalid_at=invalid_at or None,
        )
        ds = load_csv(raw, columns, delimiter=delimiter or None, decimal=decimal, encoding=encoding, missing=missing)
        if filename and ds.source is not None:
            from dataclasses import replace

            ds = replace(ds, source=replace(ds.source, name=Path(filename).name[:200]))
        with db.tx():
            key = store.add(ds, user.id)
            audit.append("dataset_created", user_id=user.id, username=user.username, target=key,
                         detail={"name": ds.source.name if ds.source else "", "n": ds.n_total})
        return _dataset_json(key, ds)

    # ------------------------------------------------------------------ data

    @app.get("/api/datasets")
    def list_datasets():
        return {"datasets": store.list()}

    @app.delete("/api/datasets/{key}")
    def delete_dataset(key: str, user: User = Depends(writer)):
        if store.owner(key) != user.id and not user.can("admin"):
            raise ApiError(403, "forbidden", "only the owner or an admin can delete a dataset", role="admin")
        with db.tx():
            store.delete(key)
            audit.append("dataset_deleted", user_id=user.id, username=user.username, target=key)
        return {"ok": True}

    @app.get("/api/datasets/{key}")
    def get_dataset(key: str):
        return _dataset_json(key, store.get(key))

    @app.get("/api/datasets/{key}/rows")
    def get_rows(key: str, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=2000)):
        ds = store.get(key)
        info, restarts = ds.invalid_info(), ds.restart_info()
        stop = min(ds.n_total, offset + limit)
        return {"total": ds.n_total, "offset": offset, "rows": [_row_json(ds, i, info, restarts) for i in range(offset, stop)]}

    @app.post("/api/datasets/{key}/suspects")
    def get_suspects(key: str, body: SuspectsBody):
        ds = store.get(key)
        found = suspects(ds, body.method, body.threshold)
        return {
            "method": body.method,
            "note": "hints_only",
            "suspects": [{"position": s.position, "value": s.value, "score": s.score} for s in found],
        }

    def check_positions(ds: Dataset, positions: list[int], want_invalid: bool) -> None:
        bad = [p for p in positions if not 0 <= p < ds.n_total]
        if bad:
            raise ApiError(400, "positions_out_of_range", "positions out of range", positions=bad[:20])
        if len(set(positions)) != len(positions):
            raise ApiError(400, "duplicate_positions", "duplicate positions")
        state = ds.invalid_info()
        wrong = [p for p in positions if (p in state) != want_invalid]
        if wrong:
            raise ApiError(
                409, "not_invalid" if want_invalid else "already_invalid",
                "some values are already invalid" if not want_invalid else "some values are not invalid",
                positions=wrong[:20],
            )

    def _marked(key: str, action: str, body: MarkBody, user: User, change) -> dict:
        with db.tx():  # the change and its audit entry stand or fall together
            new = store.modify(key, change)
            audit.append(action, user_id=user.id, username=user.username, target=key,
                         detail={"n": len(body.positions), "positions": body.positions[:50], "reason": body.reason})
        return _dataset_json(key, new)

    @app.post("/api/datasets/{key}/invalid")
    def mark_invalid(key: str, body: MarkBody, user: User = Depends(writer)):
        if not body.reason.strip():
            raise ApiError(400, "reason_required", "a reason is required to mark a value as invalid")

        def change(ds: Dataset) -> Dataset:
            check_positions(ds, body.positions, want_invalid=False)
            return ds.mark_invalid(body.positions, body.reason, user.label)

        return _marked(key, "dataset_marked_invalid", body, user, change)

    @app.post("/api/datasets/{key}/restore")
    def restore(key: str, body: MarkBody, user: User = Depends(writer)):
        if not body.reason.strip():
            raise ApiError(400, "reason_required", "a reason is required to restore a value")

        def change(ds: Dataset) -> Dataset:
            check_positions(ds, body.positions, want_invalid=True)
            return ds.restore(body.positions, body.reason, user.label)

        return _marked(key, "dataset_restored", body, user, change)

    def _restarted(key: str, action: str, body: MarkBody, user: User, change) -> dict:
        with db.tx():
            new = store.modify(key, change)
            audit.append(action, user_id=user.id, username=user.username, target=key,
                         detail={"n": len(body.positions), "positions": body.positions[:50], "reason": body.reason,
                                 **({"new_limits": True} if getattr(body, "new_limits", False) else {})})
        return _dataset_json(key, new)

    @app.post("/api/datasets/{key}/restarts")
    def add_restart(key: str, body: RestartBody, user: User = Depends(writer)):
        """Restart the moving characteristics of the I-MR chart before the given values."""
        if not body.reason.strip():
            raise ApiError(400, "reason_required", "a reason is required to restart the chart")

        def change(ds: Dataset) -> Dataset:
            bad = [p for p in body.positions if not 0 <= p < ds.n_total]
            if bad:
                raise ApiError(400, "positions_out_of_range", "positions out of range", positions=bad[:20])
            if 0 in body.positions:
                raise ApiError(400, "restart_at_start", "a restart before the first value has no effect")
            again = [p for p in body.positions if p in ds.restart_info()]
            if again:
                raise ApiError(409, "already_restart", "a restart exists already", positions=again[:20])
            return ds.add_restart(body.positions, body.reason, user.label, new_limits=body.new_limits)

        return _restarted(key, "dataset_restart_added", body, user, change)

    @app.post("/api/datasets/{key}/restarts/remove")
    def remove_restart(key: str, body: MarkBody, user: User = Depends(writer)):
        if not body.reason.strip():
            raise ApiError(400, "reason_required", "a reason is required to take a restart away")

        def change(ds: Dataset) -> Dataset:
            missing = [p for p in body.positions if p not in ds.restart_info()]
            if missing:
                raise ApiError(409, "not_restart", "there is no restart here", positions=missing[:20])
            return ds.remove_restart(body.positions, body.reason, user.label)

        return _restarted(key, "dataset_restart_removed", body, user, change)

    @app.get("/api/datasets/{key}/export.xlsx")
    def export_xlsx(key: str, lang: Literal["zh-TW", "en"] = "en"):
        from spc.report.xlsx import XLSX_TYPE, dataset_xlsx

        return Response(
            dataset_xlsx(store.get(key), lang), media_type=XLSX_TYPE,
            headers={"Content-Disposition": 'attachment; filename="spc-data.xlsx"'},
        )

    @app.get("/api/datasets/{key}/export.csv")
    def export_csv(key: str):
        text = to_csv(store.get(key))
        return Response(
            ("﻿" + text).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="spc-data.csv"'},
        )

    # ------------------------------------------------------------------ reports

    def _apply_msa_gate(meta, system_id: int, what: str):
        """The gate of the measurement system stands before a report: a blocked gate refuses. The uncertainty of the studies and what the
        gate says go into the report (elements 7, 8 and 22), unless the author entered their own uncertainty."""
        from dataclasses import replace

        system = msa_systems.view(system_id)
        gate_result = system["gate"]
        if gate_result["status"] == "block":
            raise ApiError(409, "msa_gate_blocked", f"the measurement system {system['system']['name']!r} is not proven for this {what}", system=system["system"]["name"],
                           blocking=gate_result["blocking"])
        s, u = system["system"], gate_result["uncertainty"]
        pct = gate_result["checks"]["grr"].get("pct")
        text = (f"Measurement system {s['name']}: resolution {s['resolution']}"
                + (f", gauge R&R {pct:.1f} % of the {gate_result['checks']['grr']['basis'].replace('_', ' ')}, ndc {gate_result['checks']['grr']['ndc']:.1f}" if pct is not None else "")
                + f". MSA gate: {gate_result['status']}"
                + (f" (waived: {', '.join(gate_result['waived'])})" if gate_result["waived"] else "")
                + (f" (remarks: {', '.join(gate_result['remarks'])})" if gate_result["remarks"] else "") + ".")
        out = replace(meta, technical_conditions=(meta.technical_conditions + "\n" + text).strip())
        if u and not meta.uncertainty:
            out = replace(out, uncertainty=u["U"], coverage_factor=u["k"], guard_band_risk=u["guard_risk"])
        return out

    @app.post("/api/datasets/{key}/reports")
    def create_report(key: str, body: ReportBody, user: User = Depends(writer)):
        profile_id = body.analysis.profile_id or body.profile_id
        profile = profiles.get(profile_id) if profile_id else None
        request, deviations = body.analysis.resolve(profile)
        meta = body.meta.to_meta()
        if body.measurement_system_id:
            meta = _apply_msa_gate(meta, body.measurement_system_id, "report")
        language, snap = body.language, None
        if profile:
            template = ReportTemplate.from_dict(profile["report"])
            unknown = sorted(set(meta.extra) - {f.key for f in template.extra_fields})
            if unknown:
                raise ApiError(400, "report_field_unknown", "unknown extra field", fields=unknown)
            missing = missing_fields(meta, template, meta.extra)
            if missing:
                raise ApiError(400, "report_field_required", "the customer profile requires these fields", fields=missing)
            if "language" not in body.model_fields_set and template.language:
                language = template.language
            snap = snapshot(profile["id"], profile["name"], profile["revision"], template, deviations)
        elif meta.extra:
            raise ApiError(400, "report_field_unknown", "extra fields belong to a customer profile", fields=sorted(meta.extra))
        plan_snap = plan_service.snapshot(body.control_plan_id) if body.control_plan_id else None
        g = generate(store.get(key), request, meta, language, created_by=user.label, profile=snap, control_plan=plan_snap)
        with db.tx():
            reports.add(g, key, user.id)
            audit.append("report_created", user_id=user.id, username=user.username, target=g.report_id,
                         detail={"dataset": key, "digest": g.archive["integrity"]["digest"],
                                 **({"control_plan": plan_snap["name"], "control_plan_revision": plan_snap["revision"]} if plan_snap else {}),
                                 **({"profile": profile["name"], "profile_revision": profile["revision"],
                                     "deviations": sorted(deviations)} if profile else {})})
        base = f"/api/reports/{g.report_id}"
        return {
            "id": g.report_id,
            "language": g.language,
            "digest": g.archive["integrity"]["digest"],
            "urls": {"html": base, "download": f"{base}?download=1", "archive": f"{base}/archive.json"},
        }

    @app.get("/api/reports")
    def list_reports():
        return {"reports": reports.list()}

    @app.get("/api/reports/{rid}")
    def get_report(rid: str, download: int = 0):
        report_id, _, html, _ = reports.get(rid)
        headers = {"Content-Security-Policy": REPORT_CSP, "Cache-Control": "no-store"}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="spc-report-{report_id}.html"'
        return Response(html.encode("utf-8"), media_type="text/html; charset=utf-8", headers=headers)

    @app.get("/api/reports/{rid}/archive.json")
    def get_archive(rid: str):
        report_id, _, _, archive = reports.get(rid)
        text = json.dumps(archive, ensure_ascii=False, indent=1, sort_keys=True)
        return Response(
            text.encode("utf-8"),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="spc-archive-{report_id}.json"'},
        )

    @app.get("/api/reports/{rid}/report.xlsx")
    def get_report_xlsx(rid: str):
        from spc.report.xlsx import XLSX_TYPE

        report_id, _, _, archive = reports.get(rid)
        return Response(
            report_xlsx(archive), media_type=XLSX_TYPE,
            headers={"Content-Disposition": f'attachment; filename="spc-report-{report_id}.xlsx"', "Cache-Control": "no-store"},
        )

    @app.post("/api/archive/check")
    async def check_archive(request: Request):
        raw = await read_body(request)
        try:
            archive = json.loads(raw)
            result = reproduce(archive)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ApiError(400, "archive_unreadable", f"the file is not a readable archive: {exc}")
        return {
            "integrity_ok": result.integrity_ok,
            "reproduced": result.reproduced,
            "same_engine_version": result.same_engine_version,
            "differences": list(result.differences),
        }

    # ------------------------------------------------------------------ customer profiles

    def _profile_parts(body: ProfileBody) -> tuple[str, dict, dict]:
        return body.name.strip(), validate_analysis(body.analysis), ReportTemplate.from_dict(body.report).to_dict()

    @app.get("/api/profiles")
    def list_profiles():
        return {"profiles": profiles.list()}

    @app.post("/api/profiles")
    def create_profile(body: ProfileBody, user: User = Depends(admin)):
        name, analysis, report = _profile_parts(body)
        with db.tx():
            created = profiles.create(name, analysis, report, user.id)
            audit.append("profile_created", user_id=user.id, username=user.username, target=name, detail={"id": created["id"]})
        return created

    @app.put("/api/profiles/{profile_id}")
    def update_profile(profile_id: int, body: ProfileBody, user: User = Depends(admin)):
        name, analysis, report = _profile_parts(body)
        with db.tx():
            updated = profiles.update(profile_id, name, analysis, report)
            audit.append("profile_updated", user_id=user.id, username=user.username, target=name,
                         detail={"id": profile_id, "revision": updated["revision"]})
        return updated

    @app.delete("/api/profiles/{profile_id}")
    def delete_profile(profile_id: int, user: User = Depends(admin)):
        with db.tx():
            name = profiles.get(profile_id)["name"]
            profiles.delete(profile_id)
            audit.append("profile_deleted", user_id=user.id, username=user.username, target=name, detail={"id": profile_id})
        return {"ok": True}

    # ------------------------------------------------------------------ analysis and tools

    @app.post("/api/datasets/{key}/time-model")
    def time_model(key: str, body: TimeModelBody):
        """A suggestion for the time-dependent distribution model (draft 9.4) with its evidence. The person decides."""
        try:
            return suggest_for_dataset(store.get(key), body.subgroup_size, body.hints)
        except ValueError as exc:
            raise ApiError(400, "invalid_input", str(exc)) from None

    @app.post("/api/datasets/{key}/state-tests")
    def state_tests(key: str, body: StateTestsBody):
        """Grubbs, Bartlett and Fisher for the states of a process (ISO 22514-8). They inform; the person decides how to analyse."""
        try:
            return state_tests_for_dataset(store.get(key), body.alpha, body.by)
        except ValueError as exc:
            raise ApiError(400, "invalid_input", str(exc)) from None

    @app.post("/api/datasets/{key}/analyze")
    def run_analysis(key: str, body: AnalyzeBody):
        profile = profiles.get(body.profile_id) if body.profile_id else None
        request, deviations = body.resolve(profile)
        result = analyze(store.get(key), request)
        if profile:  # what the screen shows is what the report will use
            result["profile"] = {"id": profile["id"], "name": profile["name"], "revision": profile["revision"],
                                 "deviations": sorted(deviations)}
        return result

    @app.post("/api/targets")
    def targets(body: TargetBody):
        try:
            t = required_targets(Stage(body.stage), body.characteristic_class, body.n, body.confidence, edition=body.edition)
        except TargetAdjustmentNotAllowed:
            return {"blocked": True}
        return {"blocked": False, **asdict(t)}

    @app.post("/api/arl")
    def arl_tool(body: ArlBody):
        shifts = [round(0.25 * i, 2) for i in range(0, 13)]
        curve = [
            {"shift": s, "alarm_probability": float(alarm_probability(s, body.n, body.alpha)),
             "arl": float(arl(s, body.n, body.alpha))}
            for s in shifts
        ]
        out = {
            "n": body.n, "alpha": body.alpha,
            "alarm_probability": float(alarm_probability(body.shift, body.n, body.alpha)),
            "arl": float(arl(body.shift, body.n, body.alpha)),
            "curve": curve, "required_n": None,
        }
        if body.max_arl is not None:
            try:
                out["required_n"] = required_subgroup_size(body.shift, body.max_arl, body.alpha)
            except ValueError:
                out["required_n"] = None
        return out

    @app.post("/api/attribute-chart")
    def attribute_chart(body: AttributeBody):
        counts = np.asarray(body.counts, dtype=float)
        sizes = body.sizes
        if body.kind in ("p", "np", "u") and sizes is None:
            raise ApiError(400, "sizes_required", "sizes are required for this chart")
        if body.kind == "p":
            chart = attr.p_chart(counts, sizes, body.alpha)
        elif body.kind == "np":
            chart = attr.np_chart(counts, sizes if not isinstance(sizes, list) else sizes[0], body.alpha)
        elif body.kind == "u":
            chart = attr.u_chart(counts, sizes, body.alpha)
        else:
            chart = attr.c_chart(counts, body.alpha)
        return {
            "kind": chart.kind, "alpha": chart.alpha,
            "center": float(chart.center) if np.ndim(chart.center) == 0 else [float(c) for c in chart.center],
            "lcl": chart.lcl.tolist(), "ucl": chart.ucl.tolist(), "values": chart.values.tolist(),
            "alarms": np.flatnonzero(chart.alarms()).tolist(),
            "warnings": [{"code": f"attribute_{w.code}", "params": dict(w.params)} for w in chart.warnings],
        }

    # ------------------------------------------------------------------ user interface

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app

"""FastAPI application: JSON API under /api and the web user interface under /.

Errors come back as {"error": {"code", "message", "params"}}. The code is stable and the user
interface turns it into text in the user's language. The message is English, for logs and developers.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import json

import numpy as np
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from spc import __version__
from spc.api.schemas import (
    AnalyzeBody,
    ArlBody,
    AttributeBody,
    MarkBody,
    ReportBody,
    SuspectsBody,
    TargetBody,
)
from spc.api.store import DatasetNotFound, DatasetStore, ReportNotFound, ReportStore
from spc.core.arl_oc import alarm_probability, arl, required_subgroup_size
from spc.core.capability import Stage, TargetAdjustmentNotAllowed, required_targets
from spc.core.charts import attribute as attr
from spc.data import ColumnMap, DataImportError, Dataset, IncompleteSubgroupsError, load_csv, preview_csv, suspects, to_csv
from spc.report import ReportError, generate, reproduce
from spc.service import analyze

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"
LANGUAGES = ("zh-TW", "en")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **params):
        self.status, self.code, self.message, self.params = status, code, message, params


def _error(status: int, code: str, message: str, params: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "params": params or {}}})


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
    }


def _row_json(ds: Dataset, i: int, info: dict) -> dict:
    mark = info.get(i)
    return {
        "pos": i,
        "source_row": int(ds.source_rows[i]),
        "value": float(ds.values[i]),
        "subgroup": None if ds.subgroup is None else str(ds.subgroup[i]),
        "timestamp": None if ds.timestamp is None else str(ds.timestamp[i]).replace("T", " "),
        "tags": {k: str(v[i]) for k, v in ds.tags.items()},
        "valid": mark is None,
        "invalid": None if mark is None else {"reason": mark[0], "by": mark[1], "at": mark[2]},
    }


REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'"  # no script at all


def create_app(store: DatasetStore | None = None, max_upload: int = MAX_UPLOAD_BYTES) -> FastAPI:
    app = FastAPI(title="SPC", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    store = DatasetStore() if store is None else store  # an empty store is falsy, so no `or`
    app.state.store = store
    reports = ReportStore()
    app.state.reports = reports

    # ------------------------------------------------------------------ plumbing

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            if not request.url.path.startswith("/api/docs"):  # the docs page loads its own assets
                response.headers.setdefault(k, v)
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

    @app.exception_handler(DatasetNotFound)
    async def _missing(_: Request, exc: DatasetNotFound):
        return _error(404, "dataset_not_found", "dataset not found (the server may have restarted)")

    @app.exception_handler(ReportNotFound)
    async def _report_missing(_: Request, exc: ReportNotFound):
        return _error(404, "report_not_found", "report not found (the server may have restarted)")

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
    def meta():
        return {"version": __version__, "languages": list(LANGUAGES), "max_upload_mb": max_upload // (1024 * 1024)}

    # ------------------------------------------------------------------ import

    @app.post("/api/preview")
    async def preview(request: Request, encoding: str = "auto", delimiter: str | None = None):
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
        key = store.add(ds)
        return _dataset_json(key, ds)

    # ------------------------------------------------------------------ data

    @app.get("/api/datasets/{key}")
    def get_dataset(key: str):
        return _dataset_json(key, store.get(key))

    @app.get("/api/datasets/{key}/rows")
    def get_rows(key: str, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=2000)):
        ds = store.get(key)
        info = ds.invalid_info()
        stop = min(ds.n_total, offset + limit)
        return {"total": ds.n_total, "offset": offset, "rows": [_row_json(ds, i, info) for i in range(offset, stop)]}

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

    @app.post("/api/datasets/{key}/invalid")
    def mark_invalid(key: str, body: MarkBody):
        ds = store.get(key)
        if not body.reason.strip():
            raise ApiError(400, "reason_required", "a reason is required to mark a value as invalid")
        if not body.by.strip():
            raise ApiError(400, "person_required", "the person who marks the value is required")
        check_positions(ds, body.positions, want_invalid=False)
        new = ds.mark_invalid(body.positions, body.reason, body.by)
        store.replace(key, new)
        return _dataset_json(key, new)

    @app.post("/api/datasets/{key}/restore")
    def restore(key: str, body: MarkBody):
        ds = store.get(key)
        if not body.reason.strip():
            raise ApiError(400, "reason_required", "a reason is required to restore a value")
        if not body.by.strip():
            raise ApiError(400, "person_required", "the person is required")
        check_positions(ds, body.positions, want_invalid=True)
        new = ds.restore(body.positions, body.reason, body.by)
        store.replace(key, new)
        return _dataset_json(key, new)

    @app.get("/api/datasets/{key}/export.csv")
    def export_csv(key: str):
        text = to_csv(store.get(key))
        return Response(
            ("﻿" + text).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="spc-data.csv"'},
        )

    # ------------------------------------------------------------------ reports

    @app.post("/api/datasets/{key}/reports")
    def create_report(key: str, body: ReportBody):
        g = generate(store.get(key), body.analysis.to_request(), body.meta.to_meta(), body.language)
        reports.add(g.report_id, g)
        base = f"/api/reports/{g.report_id}"
        return {
            "id": g.report_id,
            "language": g.language,
            "digest": g.archive["integrity"]["digest"],
            "urls": {"html": base, "download": f"{base}?download=1", "archive": f"{base}/archive.json"},
        }

    @app.get("/api/reports/{rid}")
    def get_report(rid: str, download: int = 0):
        g = reports.get(rid)
        headers = {"Content-Security-Policy": REPORT_CSP, "Cache-Control": "no-store"}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="spc-report-{g.report_id}.html"'
        return Response(g.html.encode("utf-8"), media_type="text/html; charset=utf-8", headers=headers)

    @app.get("/api/reports/{rid}/archive.json")
    def get_archive(rid: str):
        g = reports.get(rid)
        text = json.dumps(g.archive, ensure_ascii=False, indent=1, sort_keys=True)
        return Response(
            text.encode("utf-8"),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="spc-archive-{g.report_id}.json"'},
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

    # ------------------------------------------------------------------ analysis and tools

    @app.post("/api/datasets/{key}/analyze")
    def run_analysis(key: str, body: AnalyzeBody):
        return analyze(store.get(key), body.to_request())

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

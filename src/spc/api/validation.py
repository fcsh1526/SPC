"""Verification and validation of the analysis software (draft 11.2). Routes under /api/validation."""

from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, Response

from spc.api.schemas import ValidationCaseBody
from spc.auth import User
from spc.validation.render import render_run
from spc.validation.service import ValidationService


def add_validation_routes(app: FastAPI, svc: ValidationService, reader, engineer, admin) -> None:
    @app.get("/api/validation/cases")
    def list_cases(_: User = Depends(reader)):
        return {"cases": svc.list_cases()}

    @app.get("/api/validation/cases/{case_id}")
    def get_case(case_id: int, _: User = Depends(reader)):
        return svc.get_case(case_id)

    @app.post("/api/validation/cases")
    def create_case(body: ValidationCaseBody, user: User = Depends(engineer)):
        return svc.create_case(body.record, user)

    @app.put("/api/validation/cases/{case_id}")
    def update_case(case_id: int, body: ValidationCaseBody, user: User = Depends(engineer)):
        return svc.update_case(case_id, body.record, user)

    @app.delete("/api/validation/cases/{case_id}")
    def delete_case(case_id: int, user: User = Depends(admin)):
        svc.delete_case(case_id, user)
        return {"ok": True}

    @app.post("/api/validation/runs")
    def run(user: User = Depends(engineer)):
        return svc.run(user)

    @app.get("/api/validation/runs")
    def list_runs(_: User = Depends(reader)):
        return {"runs": svc.list_runs()}

    @app.get("/api/validation/runs/{run_id}")
    def get_run(run_id: int, _: User = Depends(reader)):
        return svc.get_run(run_id)

    @app.get("/api/validation/runs/{run_id}/report")
    def report(run_id: int, lang: Literal["zh-TW", "en"] = "en", download: bool = False, _: User = Depends(reader)):
        html = render_run(svc.get_run(run_id), lang)
        headers = {"Content-Disposition": f'attachment; filename="validation-{run_id}.html"'} if download else {}
        return Response(html.encode("utf-8"), media_type="text/html; charset=utf-8", headers=headers)

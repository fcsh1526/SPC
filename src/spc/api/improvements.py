"""Improvement cycles (PDCA of control loop 3). Routes under /api/improvements."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Query

from spc.api.schemas import ImprovementBody, ImprovementTextBody
from spc.auth import User
from spc.improvement.service import ImprovementService


def add_improvement_routes(app: FastAPI, svc: ImprovementService, reader, engineer, admin) -> None:
    @app.get("/api/improvements")
    def list_improvements(status: str | None = Query(None, pattern="^(planned|implemented|verified|standardised)$"), _: User = Depends(reader)):
        return {"improvements": svc.list(status)}

    @app.post("/api/improvements")
    def create_improvement(body: ImprovementBody, user: User = Depends(engineer)):
        return svc.create(body.record, user)

    @app.get("/api/improvements/{imp_id}")
    def get_improvement(imp_id: int, _: User = Depends(reader)):
        return svc.view(imp_id)

    @app.post("/api/improvements/{imp_id}/implement")
    def implement(imp_id: int, body: ImprovementTextBody, user: User = Depends(engineer)):
        return svc.implement(imp_id, body.text, user)

    @app.post("/api/improvements/{imp_id}/verify")
    def verify(imp_id: int, user: User = Depends(engineer)):
        return svc.verify(imp_id, user)

    @app.post("/api/improvements/{imp_id}/standardise")
    def standardise(imp_id: int, body: ImprovementTextBody, user: User = Depends(engineer)):
        return svc.standardise(imp_id, body.text, user)

    @app.post("/api/improvements/{imp_id}/rework")
    def rework(imp_id: int, body: ImprovementTextBody, user: User = Depends(engineer)):
        return svc.rework(imp_id, body.text, user)

    @app.delete("/api/improvements/{imp_id}")
    def delete_improvement(imp_id: int, user: User = Depends(admin)):
        svc.delete(imp_id, user)
        return {"ok": True}

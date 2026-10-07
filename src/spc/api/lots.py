"""Lots and their disposition (quality conformance gate). Routes under /api/lots."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Query

from spc.api.schemas import LotBody, LotDecisionBody, ReasonBody
from spc.auth import User
from spc.disposition.service import LotService


def add_lot_routes(app: FastAPI, svc: LotService, reader, operator, engineer, admin) -> None:
    @app.get("/api/lots")
    def list_lots(status: str | None = Query(None, pattern="^(open|held|decided)$"), _: User = Depends(reader)):
        return {"lots": svc.list(status)}

    @app.post("/api/lots")
    def create_lot(body: LotBody, user: User = Depends(operator)):
        return svc.create(body.record, user)

    @app.get("/api/lots/{lot_id}")
    def get_lot(lot_id: int, _: User = Depends(reader)):
        return svc.view(lot_id)

    @app.post("/api/lots/{lot_id}/hold")
    def hold_lot(lot_id: int, body: ReasonBody, user: User = Depends(operator)):
        return svc.hold(lot_id, body.reason, user)

    @app.post("/api/lots/{lot_id}/decision")
    def decide_lot(lot_id: int, body: LotDecisionBody, user: User = Depends(operator)):
        return svc.decide(lot_id, body.model_dump(), user)  # the service checks what the role of the user may decide

    @app.post("/api/lots/{lot_id}/reopen")
    def reopen_lot(lot_id: int, body: ReasonBody, user: User = Depends(engineer)):
        return svc.reopen(lot_id, body.reason, user)

    @app.delete("/api/lots/{lot_id}")
    def delete_lot(lot_id: int, user: User = Depends(admin)):
        svc.delete(lot_id, user)
        return {"ok": True}

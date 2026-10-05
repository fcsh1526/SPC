"""Measurement systems and the MSA gate. Routes under /api/msa."""

from __future__ import annotations

from fastapi import Depends, FastAPI

from spc.api.schemas import MsaBody, MsaStudyBody, ReasonBody
from spc.auth import User
from spc.msa.service import MsaService


def add_msa_routes(app: FastAPI, svc: MsaService, reader, engineer, admin) -> None:
    @app.get("/api/msa")
    def list_systems(_: User = Depends(reader)):
        return {"systems": svc.list()}

    @app.post("/api/msa")
    def create_system(body: MsaBody, user: User = Depends(engineer)):
        return svc.create(body.record, user)

    @app.get("/api/msa/{sid}")
    def get_system(sid: int, _: User = Depends(reader)):
        return svc.view(sid)

    @app.put("/api/msa/{sid}")
    def update_system(sid: int, body: MsaBody, user: User = Depends(engineer)):
        return svc.update(sid, body.record, user)

    @app.post("/api/msa/{sid}/studies")
    def add_study(sid: int, body: MsaStudyBody, user: User = Depends(engineer)):
        return svc.add_study(sid, body.kind, body.date, body.note, body.input, user)

    @app.post("/api/msa/{sid}/studies/{study_id}/void")
    def void_study(sid: int, study_id: int, body: ReasonBody, user: User = Depends(engineer)):
        return svc.void_study(sid, study_id, body.reason, user)

    @app.put("/api/msa/{sid}/waivers/{check}")
    def set_waiver(sid: int, check: str, body: ReasonBody, user: User = Depends(engineer)):
        return svc.set_waiver(sid, check, body.reason, user)

    @app.delete("/api/msa/{sid}/waivers/{check}")
    def remove_waiver(sid: int, check: str, user: User = Depends(engineer)):
        return svc.remove_waiver(sid, check, user)

    @app.delete("/api/msa/{sid}")
    def delete_system(sid: int, user: User = Depends(admin)):
        svc.delete(sid, user)
        return {"ok": True}

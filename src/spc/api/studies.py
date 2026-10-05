"""Machine performance studies (draft 8.1 to 8.3): a record, a checklist, closing. Routes under /api/studies."""

from __future__ import annotations

from fastapi import Depends, FastAPI

from spc.api.schemas import ReasonBody, StudyBody, StudyItemBody
from spc.auth import User
from spc.study.service import StudyService


def add_study_routes(app: FastAPI, svc: StudyService, reader, engineer, admin) -> None:
    @app.get("/api/studies")
    def list_studies(_: User = Depends(reader)):
        return {"studies": svc.list()}

    @app.post("/api/studies")
    def create_study(body: StudyBody, user: User = Depends(engineer)):
        return svc.create(body.record, user)

    @app.get("/api/studies/{sid}")
    def get_study(sid: int, _: User = Depends(reader)):
        return svc.view(sid)

    @app.put("/api/studies/{sid}")
    def update_study(sid: int, body: StudyBody, user: User = Depends(engineer)):
        return svc.update(sid, body.record, user)

    @app.put("/api/studies/{sid}/items/{key}")
    def set_item(sid: int, key: str, body: StudyItemBody, user: User = Depends(engineer)):
        return svc.set_item(sid, key, body.status, body.note, user)

    @app.post("/api/studies/{sid}/close")
    def close_study(sid: int, body: ReasonBody, user: User = Depends(engineer)):
        return svc.close(sid, body.reason, user)

    @app.post("/api/studies/{sid}/reopen")
    def reopen_study(sid: int, body: ReasonBody, user: User = Depends(engineer)):
        return svc.reopen(sid, body.reason, user)

    @app.delete("/api/studies/{sid}")
    def delete_study(sid: int, user: User = Depends(admin)):
        svc.delete(sid, user)
        return {"ok": True}

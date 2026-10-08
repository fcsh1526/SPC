"""Import templates: the saved column maps of customers' files. Routes under /api/import-templates."""

from __future__ import annotations

from fastapi import Depends, FastAPI

from spc.api.schemas import ImportTemplateBody
from spc.auth import User
from spc.data.templates import ImportTemplates


def add_import_template_routes(app: FastAPI, svc: ImportTemplates, reader, writer) -> None:
    @app.get("/api/import-templates")
    def list_templates(_: User = Depends(reader)):
        return {"templates": svc.list()}

    @app.post("/api/import-templates")
    def create_template(body: ImportTemplateBody, user: User = Depends(writer)):
        return svc.save(body.record, user)

    @app.put("/api/import-templates/{template_id}")
    def change_template(template_id: int, body: ImportTemplateBody, user: User = Depends(writer)):
        return svc.save(body.record, user, template_id)

    @app.delete("/api/import-templates/{template_id}")
    def delete_template(template_id: int, user: User = Depends(writer)):
        svc.delete(template_id, user)
        return {"ok": True}

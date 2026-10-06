"""Equipment interface (draft 11.1): OPC UA links. Routes under /api/equipment."""

from __future__ import annotations

from fastapi import Depends, FastAPI

from spc.api.schemas import EquipmentBody
from spc.auth import User
from spc.equipment.service import EquipmentService


def add_equipment_routes(app: FastAPI, svc: EquipmentService, reader, engineer, admin) -> None:
    @app.get("/api/equipment/links")
    def list_links(_: User = Depends(reader)):
        return {"links": svc.list(), "runner": svc.runner.running}

    @app.get("/api/equipment/links/{link_id}")
    def get_link(link_id: int, _: User = Depends(reader)):
        return svc.view(link_id)

    @app.post("/api/equipment/links")
    def create_link(body: EquipmentBody, user: User = Depends(engineer)):
        return svc.create(body.record, user)

    @app.put("/api/equipment/links/{link_id}")
    def update_link(link_id: int, body: EquipmentBody, user: User = Depends(engineer)):
        return svc.update(link_id, body.record, user)

    @app.delete("/api/equipment/links/{link_id}")
    def delete_link(link_id: int, user: User = Depends(admin)):
        svc.delete(link_id, user)
        return {"ok": True}

    @app.post("/api/equipment/links/{link_id}/test")
    def test_link(link_id: int, user: User = Depends(engineer)):
        return svc.test(link_id, user)

    @app.post("/api/equipment/links/{link_id}/enable")
    def enable_link(link_id: int, user: User = Depends(engineer)):
        return svc.enable(link_id, user)

    @app.post("/api/equipment/links/{link_id}/disable")
    def disable_link(link_id: int, user: User = Depends(engineer)):
        return svc.disable(link_id, user)

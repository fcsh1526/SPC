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

    @app.get("/api/lots/export.csv")
    def export_lots(_: User = Depends(reader)):
        """All lots with their decision as CSV, for a CAQ or ERP system."""
        import csv
        import io

        from fastapi.responses import Response

        from spc.data.csv_io import spreadsheet_safe as safe

        out = io.StringIO()
        w = csv.writer(out, lineterminator="\r\n")
        w.writerow(["lot_no", "product", "characteristic", "quantity", "status", "decision", "decided_by", "decided_at", "customer_ref", "reason", "monitor_id", "seq_from", "seq_to",
                    "inspected", "good", "rejected", "rejected_to", "parent_lot_id", "recorded_at"])
        for row in reversed(svc.list(None)):
            lot = svc.get(row["id"])
            d = lot.get("decision") or {}
            s = d.get("sorted") or {}
            w.writerow([safe(lot["lot_no"]), safe(lot["product"]), safe(lot["characteristic"]), lot["quantity"], lot["status"], d.get("kind", ""), safe(d.get("by", "")), d.get("at", ""),
                        safe(d.get("customer_ref", "")), safe(d.get("reason", "")), lot["monitor_id"] or "", lot["seq_from"] or "", lot["seq_to"] or "", s.get("inspected", ""), s.get("good", ""),
                        s.get("rejected", ""), s.get("rejected_to") or "", lot["parent_lot_id"] or "", lot["created_at"]])
        return Response(("\ufeff" + out.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="lots.csv"'})

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

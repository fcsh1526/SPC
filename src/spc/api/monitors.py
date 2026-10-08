"""SPC monitors: control loop 1 at the line. Routes under /api/monitors and /api/incidents."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Query

from spc.api.errors import ApiError
from spc.api.schemas import (
    CloseBody, EventBody, LimitsBody, MonitorConfigBody, MonitorCreateBody, OngoingReportBody, PointBody, ReasonBody,
)
from spc.auth import Audit, User
from spc.db import ReportStore, DatasetStore
from spc.monitor.service import MonitorService
from spc.report import generate


def add_monitor_routes(app: FastAPI, svc: MonitorService, datasets: DatasetStore, reports: ReportStore, audit: Audit,
                       db, reader, operator, engineer, admin) -> None:
    store = svc.store

    @app.get("/api/monitors")
    def list_monitors():
        return {"monitors": store.list()}

    @app.post("/api/monitors")
    def create_monitor(body: MonitorCreateBody, user: User = Depends(engineer)):
        monitor = svc.create(body.config, body.source, user)
        return svc.view(monitor["id"], user)

    @app.get("/api/monitors/{mid}")
    def get_monitor(mid: int, user: User = Depends(reader), limit: int = Query(100, ge=1, le=1000)):
        return svc.view(mid, user, limit)

    @app.get("/api/monitors/{mid}/export.csv")
    def export_points(mid: int, _: User = Depends(reader)):
        """The points of a monitor as CSV, for a CAQ system or a spreadsheet: one row per point, with the alarms, the incident and the validity."""
        import csv
        import io

        from fastapi.responses import Response

        from spc.data.csv_io import spreadsheet_safe as safe

        monitor = store.get(mid)
        points = store.points(mid, limit=1_000_000)
        width = max((len(p["values"]) for p in points), default=0)
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\r\n")
        w.writerow(["seq", "taken_at", "entered_at", "entered_by", "label", "tags", *[f"value_{i + 1}" for i in range(width)], "location", "variation", "valid", "invalid_reason", "alarms", "warnings", "incident_id", "new_cycle"])
        for p in points:
            w.writerow([p["seq"], p["taken_at"], p["entered_at"], safe(p["entered_by"] or ""), safe(p["label"] or ""), safe(";".join(f"{k}={v}" for k, v in sorted((p["tags"] or {}).items()))),
                        *[repr(v) for v in p["values"]], *[""] * (width - len(p["values"])), p["loc"] if p["loc"] is not None else "", p["var"] if p["var"] is not None else "",
                        int(p["valid"]), safe((p["invalid"] or {}).get("reason") or ""), ";".join(f"{a['chart']}:{a['rule']}" for a in p["alarms"]),
                        ";".join(f"{a['chart']}:{a['rule']}" for a in p["warnings"]), p["incident_id"] or "", safe(p.get("cycle") or "")])
        return Response(("\ufeff" + out.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="monitor-{monitor["id"]}-points.csv"'})

    @app.put("/api/monitors/{mid}")
    def update_monitor(mid: int, body: MonitorConfigBody, user: User = Depends(engineer)):
        svc.update(mid, body.config, user)
        return svc.view(mid, user)

    @app.delete("/api/monitors/{mid}")
    def delete_monitor(mid: int, user: User = Depends(admin)):
        with db.tx():
            name = store.get(mid)["name"]
            store.delete(mid)
            audit.append("monitor_deleted", user_id=user.id, username=user.username, target=name, detail={"id": mid})
        return {"ok": True}

    @app.post("/api/monitors/{mid}/limits")
    def set_limits(mid: int, body: LimitsBody, user: User = Depends(engineer)):
        svc.set_limits(mid, body.source, body.reason, user)
        return svc.view(mid, user)

    @app.post("/api/monitors/{mid}/ack")
    def acknowledge(mid: int, user: User = Depends(operator)):
        svc.acknowledge_ocap(mid, user)
        return {"acknowledged": True}

    @app.post("/api/monitors/{mid}/points")
    def add_point(mid: int, body: PointBody, user: User = Depends(operator)):
        return svc.add_point(mid, body.values, body.label, body.tags, body.taken_at, user, body.part, body.cycle)

    @app.get("/api/monitors/{mid}/points")
    def points(mid: int, limit: int = Query(100, ge=1, le=2000), before: int | None = Query(None, ge=1), _: User = Depends(reader)):
        store.get(mid)
        return {"points": store.points(mid, limit=limit, before=before)}

    @app.post("/api/monitors/{mid}/points/{seq}/invalid")
    def invalidate(mid: int, seq: int, body: ReasonBody, user: User = Depends(operator)):
        return svc.invalidate_point(mid, seq, body.reason, user)

    @app.get("/api/monitors/{mid}/incidents")
    def monitor_incidents(mid: int, status: str | None = Query(None, pattern="^(open|closed)$"), _: User = Depends(reader)):
        store.get(mid)
        return {"incidents": svc.incidents(mid, status)}

    @app.post("/api/monitors/{mid}/incidents/{iid}/events")
    def incident_event(mid: int, iid: int, body: EventBody, user: User = Depends(operator)):
        svc.add_event(mid, iid, body.kind, body.step, body.text, user)
        return svc.incident_view(store.get(mid), store.incident(iid))

    @app.post("/api/monitors/{mid}/incidents/{iid}/close")
    def close_incident(mid: int, iid: int, body: CloseBody, user: User = Depends(operator)):
        return svc.close_incident(mid, iid, body.outcome, body.text, user)

    @app.get("/api/alerts")
    def alerts(_: User = Depends(reader)):
        """Open incidents of all monitors: what the people at the line and their supervisors have to look at."""
        out = []
        for inc in svc.incidents(None, "open"):
            m = store.get(inc["monitor_id"])
            out.append({k: inc[k] for k in ("id", "monitor_id", "opened_at", "point_seq", "rules", "acked_by", "overdue")} |
                       {"monitor": m["name"], "line": m["line"], "characteristic": m["characteristic"]})
        return {"open": len(out), "unacknowledged": sum(1 for a in out if not a["acked_by"]), "overdue": sum(1 for a in out if a["overdue"]),
                "incidents": out}

    @app.get("/api/monitors/{mid}/ongoing")
    def ongoing(mid: int, window: int = Query(125, ge=10, le=2000), steps: int = Query(6, ge=1, le=12), _: User = Depends(reader)):
        return svc.ongoing(mid, window, steps)

    @app.post("/api/monitors/{mid}/ongoing-report")
    def ongoing_report(mid: int, body: OngoingReportBody, user: User = Depends(engineer)):
        """A normal study report on the latest points: the window is stored as a data set, so the report can be reproduced."""
        monitor = store.get(mid)
        dataset, pts = svc.window_dataset(monitor, body.window)
        request = svc.analysis_request(monitor)
        if request.lsl is None and request.usl is None:
            raise ApiError(400, "report_needs_spec", "the monitor has no specification limits for the report")
        meta = body.meta.to_meta()
        from dataclasses import replace
        meta = replace(meta, process=meta.process or monitor["process"], characteristic=meta.characteristic or monitor["characteristic"],
                       unit=meta.unit or monitor["unit"], machine=meta.machine or monitor["line"])
        name = f"{monitor['name']} · #{pts[0]['seq']}–#{pts[-1]['seq']}"
        g = generate(dataset, request, meta, body.language, created_by=user.label)
        with db.tx():
            key = datasets.add(dataset, user.id, name)
            reports.add(g, key, user.id)
            audit.append("monitor_ongoing_report", user_id=user.id, username=user.username, target=g.report_id,
                         detail={"monitor": monitor["name"], "dataset": key, "from_seq": pts[0]["seq"], "to_seq": pts[-1]["seq"]})
        base = f"/api/reports/{g.report_id}"
        return {"id": g.report_id, "dataset_id": key, "language": g.language, "digest": g.archive["integrity"]["digest"],
                "urls": {"html": base, "download": f"{base}?download=1", "archive": f"{base}/archive.json"}}


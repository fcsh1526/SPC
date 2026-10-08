"""Measurement systems and the MSA gate. Routes under /api/msa."""

from __future__ import annotations

from fastapi import Depends, FastAPI

from spc.api.schemas import CpImpactBody, GpcBody, LinearityMonitorBody, MsaBody, MsaStudyBody, MultipleReadingsBody, PvCorrectionBody, ReasonBody
from spc.api.errors import ApiError
from spc.auth import User
from spc.core import msa_aiag
from spc.core.msa import MsaError
from spc.msa.service import MsaService


def _calc(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except MsaError as exc:
        raise ApiError(400, "invalid_input", str(exc)) from None


def add_msa_routes(app: FastAPI, svc: MsaService, reader, engineer, admin) -> None:
    # calculators of the AIAG MSA manual that are not evidence of a system: nothing is stored
    @app.post("/api/msa-calc/gpc")
    def calc_gpc(body: GpcBody, _: User = Depends(reader)):
        """Gage performance curve (chapter IV F): the probability of accepting a part of a given reference value."""
        return _calc(msa_aiag.gage_performance_curve, body.lsl, body.usl, body.bias, body.sigma, body.reference_values, body.points)

    @app.post("/api/msa-calc/multiple-readings")
    def calc_multiple_readings(body: MultipleReadingsBody, _: User = Depends(reader)):
        """Reducing variation through multiple readings (chapter IV G)."""
        return _calc(msa_aiag.multiple_readings, body.current, body.target)

    @app.post("/api/msa-calc/cp-impact")
    def calc_cp_impact(body: CpImpactBody, _: User = Depends(reader)):
        """Impact of the GRR on the capability index Cp (appendix B)."""
        return _calc(msa_aiag.capability_impact, body.cp, body.grr, body.basis, body.given)

    @app.post("/api/msa-calc/pv")
    def calc_pv(body: PvCorrectionBody, _: User = Depends(reader)):
        """Part variation with the equipment variation taken out (appendix E)."""
        return _calc(msa_aiag.pv_error_corrected, body.range_of_part_averages, body.n_parts, body.ev, body.appraisers, body.trials)

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

    @app.post("/api/msa/{sid}/linearity-monitor")
    def linearity_monitor(sid: int, body: LinearityMonitorBody, _: User = Depends(reader)):
        return svc.linearity_monitor(sid, body.references, body.readings, body.epsilon)

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

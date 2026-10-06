"""External signatures of report archives. Routes under /api/reports/{rid}/signatures and /api/signers."""

from __future__ import annotations

from fastapi import Depends, FastAPI

from spc.api.schemas import SignatureBody, SignerBody
from spc.auth import User
from spc.signing.service import SigningService


def add_signing_routes(app: FastAPI, svc: SigningService, reader, engineer, admin) -> None:
    @app.get("/api/reports/{rid}/signing-payload")
    def payload(rid: str, _: User = Depends(reader)):
        return svc.payload(rid)

    @app.get("/api/reports/{rid}/signatures")
    def signatures(rid: str, _: User = Depends(reader)):
        return svc.list_signatures(rid)

    @app.post("/api/reports/{rid}/signatures")
    def add_signature(rid: str, body: SignatureBody, user: User = Depends(engineer)):
        return svc.add_signature(rid, body.signature, body.key, body.scheme, body.note, user)

    @app.delete("/api/reports/{rid}/signatures/{sig_id}")
    def delete_signature(rid: str, sig_id: int, user: User = Depends(admin)):
        svc.delete_signature(rid, sig_id, user)
        return {"ok": True}

    @app.get("/api/signers")
    def signers(_: User = Depends(reader)):
        return {"signers": svc.signers()}

    @app.post("/api/signers")
    def add_signer(body: SignerBody, user: User = Depends(admin)):
        return svc.add_signer(body.name, body.key, user)

    @app.post("/api/signers/{signer_id}/enable")
    def enable_signer(signer_id: int, user: User = Depends(admin)):
        return svc.set_signer_active(signer_id, True, user)

    @app.post("/api/signers/{signer_id}/disable")
    def disable_signer(signer_id: int, user: User = Depends(admin)):
        return svc.set_signer_active(signer_id, False, user)

    @app.delete("/api/signers/{signer_id}")
    def delete_signer(signer_id: int, user: User = Depends(admin)):
        svc.delete_signer(signer_id, user)
        return {"ok": True}

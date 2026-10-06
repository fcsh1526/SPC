"""Trusted signers and the signatures of stored reports. A signature is checked again each time it is shown, against the archive as it is now."""

from __future__ import annotations

import base64
import json

from spc.auth import User
from spc.db.database import Database
from spc.db.stores import ReportNotFound, now_iso
from spc.report.archive import verify_archive
from spc.signing import core
from spc.signing.core import SigningError


class SigningService:
    def __init__(self, db: Database, audit, reports):
        self.db, self.audit, self.reports = db, audit, reports

    # --- trusted signers -------------------------------------------------
    @staticmethod
    def _signer(r) -> dict:
        d = json.loads(r["data"])
        return {"id": r["id"], "name": r["name"], "fingerprint": r["fingerprint"], "active": bool(r["active"]),
                "created_at": r["created_at"], **d}

    def signers(self) -> list[dict]:
        return [self._signer(r) for r in self.db.all("SELECT * FROM trusted_signers ORDER BY name COLLATE NOCASE")]

    def add_signer(self, name: str, pem: str, user: User) -> dict:
        name = (name or "").strip()
        if not name or len(name) > 80:
            raise SigningError("signer_name_invalid", "the name of a signer has 1 to 80 characters")
        key, cert = core.load_key(pem)
        fp = core.key_fingerprint(key)
        data = {"key": core.describe_key(key), "certificate": core.describe_certificate(cert), "pem": pem.strip()}
        if self.db.one("SELECT 1 FROM trusted_signers WHERE name = ?", (name,)):
            raise SigningError("signer_name_taken", "another signer has this name", 409)
        if self.db.one("SELECT 1 FROM trusted_signers WHERE fingerprint = ?", (fp,)):
            raise SigningError("signer_key_known", "this key is already a trusted signer", 409)
        cur = self.db.execute(
            "INSERT INTO trusted_signers (name, fingerprint, data, active, created_at, created_by) VALUES (?, ?, ?, 1, ?, ?)",
            (name, fp, json.dumps(data, ensure_ascii=False), now_iso(), user.id))
        self.audit.append("signer_added", user_id=user.id, username=user.username, target=name, detail={"fingerprint": fp})
        return self._signer(self.db.one("SELECT * FROM trusted_signers WHERE id = ?", (cur.lastrowid,)))

    def set_signer_active(self, signer_id: int, active: bool, user: User) -> dict:
        row = self.db.one("SELECT * FROM trusted_signers WHERE id = ?", (signer_id,))
        if row is None:
            raise SigningError("signer_not_found", "no such signer", 404)
        self.db.execute("UPDATE trusted_signers SET active = ? WHERE id = ?", (1 if active else 0, signer_id))
        self.audit.append("signer_enabled" if active else "signer_disabled", user_id=user.id, username=user.username, target=row["name"],
                          detail={"fingerprint": row["fingerprint"]})
        return self._signer(self.db.one("SELECT * FROM trusted_signers WHERE id = ?", (signer_id,)))

    def delete_signer(self, signer_id: int, user: User) -> None:
        row = self.db.one("SELECT * FROM trusted_signers WHERE id = ?", (signer_id,))
        if row is None:
            raise SigningError("signer_not_found", "no such signer", 404)
        self.db.execute("DELETE FROM trusted_signers WHERE id = ?", (signer_id,))
        self.audit.append("signer_deleted", user_id=user.id, username=user.username, target=row["name"], detail={"fingerprint": row["fingerprint"]})

    # --- signatures --------------------------------------------------------
    def _report(self, rid: str):
        try:
            return self.reports.get(rid)
        except ReportNotFound:
            raise SigningError("report_not_found", "report not found", 404) from None

    def payload(self, rid: str) -> dict:
        """What the signer has to sign: the message and the digest in it."""
        report_id, _, _, archive = self._report(rid)
        if not verify_archive(archive):
            raise SigningError("signature_archive_broken", "the stored archive no longer matches its digest", 409)
        digest = archive["integrity"]["digest"]
        msg = core.message(digest)
        return {"report_id": report_id, "digest": digest, "message": msg.decode("ascii"), "message_base64": base64.b64encode(msg).decode("ascii"),
                "schemes": list(core.SCHEMES)}

    def add_signature(self, rid: str, signature: str, pem: str, scheme: str | None, note: str, user: User) -> dict:
        report_id, _, _, archive = self._report(rid)
        if not verify_archive(archive):
            raise SigningError("signature_archive_broken", "the stored archive no longer matches its digest", 409)
        if scheme and scheme not in core.SCHEMES:
            raise SigningError("signature_scheme_unknown", "unknown signature scheme", scheme=scheme)
        if len(note or "") > 300:
            raise SigningError("signature_note_long", "the note has at most 300 characters")
        key, cert = core.load_key(pem)
        raw = core.decode_signature(signature)
        digest = archive["integrity"]["digest"]
        used = core.verify(key, raw, digest, scheme)
        if used is None:
            raise SigningError("signature_invalid", "the signature does not match this archive under this key", 422)
        fp = core.key_fingerprint(key)
        for r in self.db.all("SELECT data FROM report_signatures WHERE report_id = ?", (report_id,)):
            if json.loads(r["data"])["fingerprint"] == fp:
                raise SigningError("signature_duplicate", "this key has already signed this report", 409)
        data = {"digest": digest, "scheme": used, "signature": base64.b64encode(raw).decode("ascii"), "fingerprint": fp, "key": core.describe_key(key),
                "certificate": core.describe_certificate(cert), "pem": pem.strip(), "note": (note or "").strip()}
        cur = self.db.execute("INSERT INTO report_signatures (report_id, data, created_at, created_by) VALUES (?, ?, ?, ?)",
                              (report_id, json.dumps(data, ensure_ascii=False), now_iso(), user.id))
        self.audit.append("report_signed", user_id=user.id, username=user.username, target=report_id, detail={"fingerprint": fp, "scheme": used})
        return self.list_signatures(report_id, only=cur.lastrowid)["signatures"][0]

    def list_signatures(self, rid: str, only: int | None = None) -> dict:
        report_id, _, _, archive = self._report(rid)
        intact = verify_archive(archive)
        digest = archive["integrity"]["digest"] if intact else None
        trusted = {r["fingerprint"]: dict(r) for r in self.db.all("SELECT * FROM trusted_signers WHERE active = 1")}
        out = []
        for r in self.db.all("SELECT s.id, s.data, s.created_at, u.username FROM report_signatures s LEFT JOIN users u ON u.id = s.created_by"
                             " WHERE s.report_id = ? ORDER BY s.id", (report_id,)):
            if only is not None and r["id"] != only:
                continue
            d = json.loads(r["data"])
            valid = False
            if intact and d["digest"] == digest:
                try:
                    key, _ = core.load_key(d["pem"])
                    valid = core.verify(key, base64.b64decode(d["signature"]), digest, d["scheme"]) is not None
                except SigningError:
                    valid = False
            t = trusted.get(d["fingerprint"])
            out.append({"id": r["id"], "created_at": r["created_at"], "recorded_by": r["username"], "scheme": d["scheme"], "key": d["key"],
                        "fingerprint": d["fingerprint"], "certificate": d["certificate"], "note": d["note"], "valid": valid,
                        "signer": t["name"] if t else None, "trusted": bool(valid and t)})
        return {"report_id": report_id, "digest": archive["integrity"]["digest"], "archive_intact": intact, "signatures": out}

    def counts(self) -> dict[str, int]:
        return {r["report_id"]: r["n"] for r in self.db.all("SELECT report_id, COUNT(*) AS n FROM report_signatures GROUP BY report_id")}

    def delete_signature(self, rid: str, sig_id: int, user: User) -> None:
        row = self.db.one("SELECT data FROM report_signatures WHERE id = ? AND report_id = ?", (sig_id, rid))
        if row is None:
            raise SigningError("signature_not_found", "no such signature", 404)
        self.db.execute("DELETE FROM report_signatures WHERE id = ?", (sig_id,))
        self.audit.append("signature_removed", user_id=user.id, username=user.username, target=rid, detail={"fingerprint": json.loads(row["data"])["fingerprint"]})

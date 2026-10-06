"""External signatures: the message, the schemes, trust, and what happens when the archive or the key changes."""

import base64
import json

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa  # noqa: E402

from spc.signing import core  # noqa: E402
from spc.signing.__main__ import main as sign_cli  # noqa: E402
from tests.conftest import logged_in_client, make_app  # noqa: E402
from tests.test_api import REPORT_BODY, upload  # noqa: E402

DIGEST = "ab" * 32


def pub_pem(private) -> str:
    return private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def priv_pem(private) -> bytes:
    return private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


KEYS = {
    "ed25519": lambda: ed25519.Ed25519PrivateKey.generate(),
    "p256": lambda: ec.generate_private_key(ec.SECP256R1()),
    "p384": lambda: ec.generate_private_key(ec.SECP384R1()),
    "rsa": lambda: rsa.generate_private_key(65537, 2048),
}


@pytest.mark.parametrize("kind", KEYS)
def test_every_scheme_signs_and_verifies(kind):
    private = KEYS[kind]()
    raw, scheme = core.sign(private, DIGEST)
    key, cert = core.load_key(pub_pem(private))
    assert cert is None and core.verify(key, raw, DIGEST) == scheme
    assert core.verify(key, raw, "cd" * 32) is None  # another archive
    assert core.verify(key, raw[:-1] + bytes([raw[-1] ^ 1]), DIGEST) is None  # a changed signature


def test_pkcs1v15_and_p384_with_sha256():
    private = KEYS["rsa"]()
    raw, scheme = core.sign(private, DIGEST, "rsa-pkcs1v15-sha256")
    assert scheme == "rsa-pkcs1v15-sha256" and core.verify(core.load_key(pub_pem(private))[0], raw, DIGEST) == scheme
    p384 = KEYS["p384"]()
    raw, scheme = core.sign(p384, DIGEST, "ecdsa-sha256")
    assert core.verify(core.load_key(pub_pem(p384))[0], raw, DIGEST) == "ecdsa-sha256"


def test_message_format_and_fingerprint_is_stable():
    assert core.message(DIGEST) == b"spc-archive-v1\n" + DIGEST.encode()
    with pytest.raises(core.SigningError) as e:
        core.message("xyz")
    assert e.value.code == "signature_archive_broken"
    k = KEYS["ed25519"]()
    assert core.key_fingerprint(k.public_key()) == core.key_fingerprint(core.load_key(pub_pem(k))[0])


def test_weak_and_unreadable_keys_are_refused():
    with pytest.raises(core.SigningError) as e:
        core.load_key(pub_pem(rsa.generate_private_key(65537, 1024)))
    assert e.value.code == "signature_key_weak"
    with pytest.raises(core.SigningError) as e:
        core.load_key(pub_pem(ec.generate_private_key(ec.SECP521R1())))
    assert e.value.code == "signature_key_weak"
    with pytest.raises(core.SigningError) as e:
        core.load_key("not a key")
    assert e.value.code == "signature_key_unreadable"
    for bad in ("", "zz", "AAAA"):
        with pytest.raises(core.SigningError):
            core.decode_signature(bad)
    raw = bytes(range(64))
    assert core.decode_signature(base64.b64encode(raw).decode()) == raw and core.decode_signature(raw.hex()) == raw


def self_signed(private):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID
    import datetime as dt

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "QA Signer")])
    now = dt.datetime(2026, 1, 1)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key()).serial_number(7)
            .not_valid_before(now).not_valid_after(now + dt.timedelta(days=3650)).sign(private, None if isinstance(private, ed25519.Ed25519PrivateKey) else hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def test_certificate_is_read_and_has_the_same_fingerprint_as_its_key():
    private = KEYS["p256"]()
    key, cert = core.load_key(self_signed(private))
    info = core.describe_certificate(cert)
    assert "QA Signer" in info["subject"] and info["self_signed"] and info["serial"] == "7"
    assert core.key_fingerprint(key) == core.key_fingerprint(core.load_key(pub_pem(private))[0])


# ------------------------------------------------------------------ service and API

@pytest.fixture
def env():
    app = make_app()
    client = logged_in_client(app, "eng")
    ds = upload(client).json()
    out = client.post(f"/api/datasets/{ds['id']}/reports", json=REPORT_BODY).json()
    return app, client, out


def sign_report(client, out, private, **extra):
    pl = client.get(f"/api/reports/{out['report_id'] if 'report_id' in out else out['id']}/signing-payload").json()
    raw, scheme = core.sign(private, pl["digest"])
    body = {"signature": base64.b64encode(raw).decode(), "key": pub_pem(private), **extra}
    return pl, client.post(f"/api/reports/{pl['report_id']}/signatures", json=body)


def test_payload_sign_list_and_trust(env):
    app, client, out = env
    admin = logged_in_client(app, "admin")
    private = KEYS["ed25519"]()
    pl, r = sign_report(client, out, private, note="released")
    assert pl["message"].startswith("spc-archive-v1\n") and pl["digest"] == out["digest"]
    assert r.status_code == 200, r.text
    sig = r.json()
    assert sig["valid"] and not sig["trusted"] and sig["signer"] is None and sig["note"] == "released" and sig["scheme"] == "ed25519"
    # trust it: the same signature is now trusted, with the signer's name
    s = admin.post("/api/signers", json={"name": "QA lead", "key": pub_pem(private)})
    assert s.status_code == 200, s.text
    rid = pl["report_id"]
    got = client.get(f"/api/reports/{rid}/signatures").json()
    assert got["archive_intact"] and got["signatures"][0]["trusted"] and got["signatures"][0]["signer"] == "QA lead"
    assert [x["signatures"] for x in client.get("/api/reports").json()["reports"] if x["id"] == rid] == [1]
    # disabled signer: valid but no longer trusted
    admin.post(f"/api/signers/{s.json()['id']}/disable")
    assert not client.get(f"/api/reports/{rid}/signatures").json()["signatures"][0]["trusted"]
    admin.post(f"/api/signers/{s.json()['id']}/enable")
    assert client.get(f"/api/reports/{rid}/signatures").json()["signatures"][0]["trusted"]
    kinds = [e["action"] for e in admin.get("/api/audit?limit=50").json()["entries"]]
    assert {"report_signed", "signer_added", "signer_disabled", "signer_enabled"} <= set(kinds)


def test_invalid_duplicate_and_wrong_key_are_refused(env):
    app, client, out = env
    a, b = KEYS["p256"](), KEYS["rsa"]()
    pl, r = sign_report(client, out, a)
    rid = pl["report_id"]
    assert r.status_code == 200
    _, again = sign_report(client, out, a)
    assert again.status_code == 409 and again.json()["error"]["code"] == "signature_duplicate"
    raw, _ = core.sign(a, pl["digest"])
    wrong = client.post(f"/api/reports/{rid}/signatures", json={"signature": base64.b64encode(raw).decode(), "key": pub_pem(b)})
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "signature_invalid"
    other = core.sign(a, "cd" * 32)[0]  # signed another digest
    bad = client.post(f"/api/reports/{rid}/signatures", json={"signature": base64.b64encode(other).decode(), "key": pub_pem(KEYS["ed25519"]())})
    assert bad.json()["error"]["code"] == "signature_invalid"
    junk = client.post(f"/api/reports/{rid}/signatures", json={"signature": "###", "key": pub_pem(a)})
    assert junk.json()["error"]["code"] == "signature_unreadable"
    assert client.get("/api/reports/nope/signatures").status_code == 404


def test_roles(env):
    app, client, out = env
    viewer = logged_in_client(app, "view")
    private = KEYS["ed25519"]()
    pl, r = sign_report(client, out, private)
    rid = pl["report_id"]
    assert viewer.get(f"/api/reports/{rid}/signatures").status_code == 200
    raw, _ = core.sign(KEYS["ed25519"](), pl["digest"])
    assert viewer.post(f"/api/reports/{rid}/signatures", json={"signature": base64.b64encode(raw).decode(), "key": pub_pem(private)}).status_code == 403
    assert client.post("/api/signers", json={"name": "x", "key": pub_pem(private)}).status_code == 403  # engineer is not admin
    assert client.delete(f"/api/reports/{rid}/signatures/{r.json()['id']}").status_code == 403
    admin = logged_in_client(app, "admin")
    assert admin.delete(f"/api/reports/{rid}/signatures/{r.json()['id']}").status_code == 200
    assert client.get(f"/api/reports/{rid}/signatures").json()["signatures"] == []


def test_signer_rules(env):
    app, _, _ = env
    admin = logged_in_client(app, "admin")
    k = KEYS["ed25519"]()
    assert admin.post("/api/signers", json={"name": "A", "key": pub_pem(k)}).status_code == 200
    assert admin.post("/api/signers", json={"name": "a", "key": pub_pem(KEYS["p256"]())}).json()["error"]["code"] == "signer_name_taken"
    assert admin.post("/api/signers", json={"name": "B", "key": pub_pem(k)}).json()["error"]["code"] == "signer_key_known"
    assert admin.post("/api/signers", json={"name": " ", "key": pub_pem(k)}).json()["error"]["code"] == "signer_name_invalid"
    sid = admin.get("/api/signers").json()["signers"][0]["id"]
    assert admin.delete(f"/api/signers/{sid}").status_code == 200
    assert admin.delete(f"/api/signers/{sid}").status_code == 404


def test_a_changed_archive_invalidates_the_signature(env):
    app, client, out = env
    pl, r = sign_report(client, out, KEYS["ed25519"]())
    rid = pl["report_id"]
    assert client.get(f"/api/reports/{rid}/signatures").json()["signatures"][0]["valid"]
    db = app.state.db
    row = db.one("SELECT archive FROM reports WHERE id = ?", (rid,))
    archive = json.loads(row["archive"])
    archive["dataset"]["values"][0] += 0.5
    db.execute("UPDATE reports SET archive = ? WHERE id = ?", (json.dumps(archive), rid))
    got = client.get(f"/api/reports/{rid}/signatures").json()
    assert got["archive_intact"] is False and got["signatures"][0]["valid"] is False and got["signatures"][0]["trusted"] is False
    assert client.get(f"/api/reports/{rid}/signing-payload").json()["error"]["code"] == "signature_archive_broken"


def test_cli_payload_sign_verify(env, tmp_path, capsysbinary):
    app, client, out = env
    rid = out.get("report_id") or out["id"]
    archive = tmp_path / "a.json"
    archive.write_bytes(client.get(f"/api/reports/{rid}/archive.json").content)
    private = KEYS["p256"]()
    (tmp_path / "k.pem").write_bytes(priv_pem(private))
    (tmp_path / "pub.pem").write_text(pub_pem(private))
    assert sign_cli(["payload", str(archive)]) == 0
    assert capsysbinary.readouterr().out == b"spc-archive-v1\n" + out["digest"].encode()
    assert sign_cli(["sign", str(archive), "--key", str(tmp_path / "k.pem")]) == 0
    sig = capsysbinary.readouterr().out.decode().strip()
    (tmp_path / "s.txt").write_text(sig)
    assert sign_cli(["verify", str(archive), "--cert", str(tmp_path / "pub.pem"), "--signature", str(tmp_path / "s.txt")]) == 0
    # the signature made by the command line is accepted by the web program
    r = client.post(f"/api/reports/{rid}/signatures", json={"signature": sig, "key": pub_pem(private)})
    assert r.status_code == 200 and r.json()["valid"]
    # a changed archive is refused by the tool
    changed = json.loads(archive.read_text())
    changed["dataset"]["values"][0] += 1
    archive.write_text(json.dumps(changed))
    assert sign_cli(["payload", str(archive)]) == 2

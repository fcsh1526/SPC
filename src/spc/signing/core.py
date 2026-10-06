"""Signing and verifying the digest of an archive with asymmetric keys (library `cryptography`, optional dependency `sign`).

What is signed is the message `spc-archive-v1\\n<sha-256 digest of the archive>`: the digest covers the whole archive (data, marks, parameters,
results, report inputs), so the signature covers all of it. The signer makes the signature with their own tool (openssl, a smart card, a signing
service) or with `python -m spc.signing sign`; this program only verifies it.

Schemes: ed25519; ecdsa-sha256 (P-256) and ecdsa-sha384 (P-384), DER signatures as openssl writes them; rsa-pss-sha256 and rsa-pkcs1v15-sha256 with keys of
at least 2048 bits. A signature is valid when it matches the digest under the key. Whether the key belongs to a person that is trusted is a separate
question, answered by the list of trusted signers.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import timezone
from typing import Any

PREFIX = b"spc-archive-v1\n"
SCHEMES = ("ed25519", "ecdsa-sha256", "ecdsa-sha384", "rsa-pss-sha256", "rsa-pkcs1v15-sha256")
MIN_RSA_BITS = 2048


class SigningError(ValueError):
    def __init__(self, code: str, message: str = "", status: int = 400, **params):
        super().__init__(message or code)
        self.code, self.status, self.params = code, status, params


def _crypto():
    try:
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
    except ImportError:
        raise SigningError("signing_unavailable", "the library for signatures is not installed: pip install 'spc[sign]'", 501) from None
    return x509, InvalidSignature, hashes, serialization, ec, ed25519, padding, rsa


def message(digest: str) -> bytes:
    if not (isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)):
        raise SigningError("signature_archive_broken", "the digest of the archive is not a SHA-256 value")
    return PREFIX + digest.encode("ascii")


def load_key(pem: str) -> tuple[Any, Any]:
    """(public key, certificate or None) from a PEM text that holds a certificate or a public key."""
    x509, _, _, serialization, ec, ed25519, _, rsa = _crypto()
    data = pem.strip().encode("utf-8")
    cert = None
    try:
        if b"BEGIN CERTIFICATE" in data:
            cert = x509.load_pem_x509_certificate(data)
            key = cert.public_key()
        else:
            key = serialization.load_pem_public_key(data)
    except Exception:
        raise SigningError("signature_key_unreadable", "the key is not a PEM certificate or public key") from None
    if isinstance(key, rsa.RSAPublicKey):
        if key.key_size < MIN_RSA_BITS:
            raise SigningError("signature_key_weak", f"an RSA key needs at least {MIN_RSA_BITS} bits", bits=key.key_size)
    elif isinstance(key, ec.EllipticCurvePublicKey):
        if key.curve.name not in ("secp256r1", "secp384r1"):
            raise SigningError("signature_key_weak", "only the curves P-256 and P-384 are accepted", curve=key.curve.name)
    elif not isinstance(key, ed25519.Ed25519PublicKey):
        raise SigningError("signature_key_unreadable", "the key type is not supported (RSA, P-256, P-384, Ed25519)")
    return key, cert


def key_fingerprint(key) -> str:
    """SHA-256 of the DER form of the public key: it stays the same when a certificate is renewed for the same key."""
    _, _, _, serialization, *_ = _crypto()
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


def describe_key(key) -> str:
    _, _, _, _, ec, ed25519, _, rsa = _crypto()
    if isinstance(key, rsa.RSAPublicKey):
        return f"RSA {key.key_size}"
    if isinstance(key, ec.EllipticCurvePublicKey):
        return {"secp256r1": "ECDSA P-256", "secp384r1": "ECDSA P-384"}[key.curve.name]
    return "Ed25519"


def describe_certificate(cert) -> dict | None:
    if cert is None:
        return None
    _, _, hashes, serialization, *_ = _crypto()
    utc = lambda d: d.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    nb = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before
    na = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
    return {"subject": cert.subject.rfc4514_string(), "issuer": cert.issuer.rfc4514_string(), "serial": format(cert.serial_number, "x"),
            "not_before": utc(nb), "not_after": utc(na), "fingerprint": cert.fingerprint(hashes.SHA256()).hex(), "self_signed": cert.subject == cert.issuer}


def decode_signature(text: str) -> bytes:
    """Hexadecimal when every character is a hex digit (a hex text is also valid base64, but a base64 signature of this length is never all hex), else base64."""
    s = "".join((text or "").split())
    try:
        if s and len(s) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in s):
            raw = bytes.fromhex(s)
        else:
            raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        raise SigningError("signature_unreadable", "the signature is neither base64 nor hexadecimal") from None
    if not 32 <= len(raw) <= 1024:
        raise SigningError("signature_unreadable", "the signature has an impossible length")
    return raw


def _check(key, signature: bytes, msg: bytes, scheme: str) -> bool:
    _, InvalidSignature, hashes, _, ec, ed25519, padding, rsa = _crypto()
    try:
        if scheme == "ed25519" and isinstance(key, ed25519.Ed25519PublicKey):
            key.verify(signature, msg)
        elif scheme in ("ecdsa-sha256", "ecdsa-sha384") and isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, msg, ec.ECDSA(hashes.SHA256() if scheme.endswith("256") else hashes.SHA384()))
        elif scheme == "rsa-pss-sha256" and isinstance(key, rsa.RSAPublicKey):
            key.verify(signature, msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.AUTO), hashes.SHA256())
        elif scheme == "rsa-pkcs1v15-sha256" and isinstance(key, rsa.RSAPublicKey):
            key.verify(signature, msg, padding.PKCS1v15(), hashes.SHA256())
        else:
            return False
        return True
    except (InvalidSignature, ValueError):
        return False


def schemes_for(key) -> tuple[str, ...]:
    _, _, _, _, ec, ed25519, _, rsa = _crypto()
    if isinstance(key, rsa.RSAPublicKey):
        return ("rsa-pss-sha256", "rsa-pkcs1v15-sha256")
    if isinstance(key, ec.EllipticCurvePublicKey):
        return ("ecdsa-sha256",) if key.curve.name == "secp256r1" else ("ecdsa-sha384", "ecdsa-sha256")
    return ("ed25519",)


def verify(key, signature: bytes, digest: str, scheme: str | None = None) -> str | None:
    """The scheme under which the signature is valid, or None. Without a scheme the ones that fit the key are tried."""
    msg = message(digest)
    for s in ([scheme] if scheme else schemes_for(key)):
        if s in SCHEMES and _check(key, signature, msg, s):
            return s
    return None


def sign(private_key, digest: str, scheme: str | None = None) -> tuple[bytes, str]:
    """For `python -m spc.signing sign` and for tests: the signature and the scheme that was used."""
    _, _, hashes, _, ec, ed25519, padding, rsa = _crypto()
    msg = message(digest)
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        return private_key.sign(msg), "ed25519"
    if isinstance(private_key, ec.EllipticCurvePrivateKey):
        sch = scheme or ("ecdsa-sha384" if private_key.curve.name == "secp384r1" else "ecdsa-sha256")
        return private_key.sign(msg, ec.ECDSA(hashes.SHA384() if sch.endswith("384") else hashes.SHA256())), sch
    if isinstance(private_key, rsa.RSAPrivateKey):
        if scheme == "rsa-pkcs1v15-sha256":
            return private_key.sign(msg, padding.PKCS1v15(), hashes.SHA256()), scheme
        return private_key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256()), "rsa-pss-sha256"
    raise SigningError("signature_key_unreadable", "the key type is not supported")

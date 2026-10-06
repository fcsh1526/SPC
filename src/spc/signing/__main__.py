"""Command line for the person that signs, outside the web program: `spc-sign payload|sign|verify`."""

from __future__ import annotations

import argparse
import base64
import json
import sys

from spc.report.archive import verify_archive
from spc.signing import core


def _digest(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        archive = json.load(f)
    if not verify_archive(archive):
        raise core.SigningError("signature_archive_broken", "the archive does not match its own digest")
    return archive["integrity"]["digest"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="spc-sign", description="Sign or check the digest of an SPC report archive (archive.json).")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("payload", help="print the message to sign (for openssl or a signing service)")
    a.add_argument("archive")
    a = sub.add_parser("sign", help="sign with a private key in PEM (not encrypted); prints the signature in base64")
    a.add_argument("archive")
    a.add_argument("--key", required=True)
    a.add_argument("--scheme", choices=core.SCHEMES)
    a = sub.add_parser("verify", help="check a signature against an archive and a public key or certificate")
    a.add_argument("archive")
    a.add_argument("--cert", required=True)
    a.add_argument("--signature", required=True, help="file with the signature (base64 or hex)")
    args = p.parse_args(argv)
    try:
        digest = _digest(args.archive)
        if args.cmd == "payload":
            sys.stdout.buffer.write(core.message(digest))
        elif args.cmd == "sign":
            from cryptography.hazmat.primitives import serialization

            with open(args.key, "rb") as f:
                private = serialization.load_pem_private_key(f.read(), password=None)
            raw, scheme = core.sign(private, digest, args.scheme)
            print(base64.b64encode(raw).decode("ascii"))
            print(f"scheme: {scheme}", file=sys.stderr)
        else:
            with open(args.cert, encoding="utf-8") as f:
                key, _ = core.load_key(f.read())
            with open(args.signature, encoding="utf-8") as f:
                raw = core.decode_signature(f.read())
            scheme = core.verify(key, raw, digest)
            print(f"{'valid' if scheme else 'INVALID'} fingerprint={core.key_fingerprint(key)}" + (f" scheme={scheme}" if scheme else ""))
            return 0 if scheme else 1
    except (core.SigningError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

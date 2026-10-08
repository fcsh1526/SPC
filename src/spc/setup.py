"""spc-setup: the first setup of a data folder. It makes the folders, the database, the first administrator, the settings file and, on request, a self-signed certificate.

    spc-setup --data-dir D:\\SPC\\data --admin admin --password-file pw.txt --host 0.0.0.0 --port 8443 --tls self-signed

It can be run again: what exists is kept (users, settings, certificates) unless --force-config is given. The password file is deleted after it is read.
Then start the server with:  spc-serve --config D:\\SPC\\data\\spc.json
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from spc.auth import AuthError, AuthService
from spc.db import Database

CONFIG_NAME = "spc.json"
DB_NAME = "spc.sqlite3"


def self_signed(cert: Path, key: Path, host: str | None = None, days: int = 825) -> None:
    """A self-signed certificate for this computer's name, localhost and 127.0.0.1. Browsers warn about it until it is trusted; a company certificate is better."""
    try:
        import ipaddress

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        raise SystemExit("a self-signed certificate needs the 'cryptography' package (pip install cryptography)")
    host = host or socket.gethostname()
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host[:64]), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SPC")])
    now = datetime.now(timezone.utc)
    names = [x509.DNSName(host), x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    cert_obj = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=days)).add_extension(x509.SubjectAlternativeName(names), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True).sign(private, hashes.SHA256()))
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    try:
        os.chmod(key, 0o600)
    except OSError:
        pass
    cert.write_bytes(cert_obj.public_bytes(serialization.Encoding.PEM))


def setup(data_dir: Path, admin: str | None = None, password: str | None = None, host: str = "127.0.0.1", port: int = 8000, tls: str = "none",
          cert: str | None = None, key: str | None = None, force_config: bool = False) -> dict:
    data_dir = Path(data_dir)
    for sub in ("", "backup", "qualification", "certs"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    db_path = data_dir / DB_NAME
    created_db = not db_path.exists()
    db = Database(db_path)
    auth = AuthService(db)
    created_admin = False
    if auth.user_count() == 0:
        if not admin or not password:
            db.close()
            raise SystemExit("there is no user yet: give --admin and a password (--password-file or --password-stdin)")
        auth.create_user(admin, password, "admin", "")
        created_admin = True
    db.close()
    config_path = data_dir / CONFIG_NAME
    wrote = False
    if force_config or not config_path.exists():
        config = {"db": str(db_path), "host": host, "port": port}
        if tls == "self-signed":
            cert_path, key_path = data_dir / "certs" / "spc.crt", data_dir / "certs" / "spc.key"
            if force_config or not cert_path.exists():
                self_signed(cert_path, key_path)
            config.update(ssl_certfile=str(cert_path), ssl_keyfile=str(key_path), secure_cookies=True)
        elif tls == "files":
            if not cert or not key or not Path(cert).is_file() or not Path(key).is_file():
                raise SystemExit("--tls files needs --cert and --key that exist")
            config.update(ssl_certfile=str(Path(cert).resolve()), ssl_keyfile=str(Path(key).resolve()), secure_cookies=True)
        config_path.write_text(json.dumps(config, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        wrote = True
    return {"data_dir": str(data_dir), "database": str(db_path), "created_database": created_db, "created_admin": created_admin, "config": str(config_path), "config_written": wrote,
            "settings": json.loads(config_path.read_text(encoding="utf-8"))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SPC first setup")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--admin", default=None, help="name of the first administrator (only when there is no user yet)")
    parser.add_argument("--password-file", default=None, help="a file whose first line is the password; it is deleted after it is read")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 serves the network (use https then)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--tls", choices=("none", "self-signed", "files"), default="none")
    parser.add_argument("--cert", default=None)
    parser.add_argument("--key", default=None)
    parser.add_argument("--force-config", action="store_true")
    args = parser.parse_args(argv)
    password = None
    if args.password_file:
        p = Path(args.password_file)
        try:
            password = p.read_text(encoding="utf-8").split("\n")[0].rstrip("\r")
        except OSError as exc:
            print(f"cannot read the password file: {exc}", file=sys.stderr)
            return 2
        try:
            p.unlink()
        except OSError:
            pass
    elif args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    elif args.admin and sys.stdin.isatty():
        password = getpass.getpass("Password of the administrator: ")
    try:
        r = setup(Path(args.data_dir), args.admin, password, args.host, args.port, args.tls, args.cert, args.key, args.force_config)
    except AuthError as exc:
        print(f"error: {exc.code} {exc.params or ''}", file=sys.stderr)
        return 1
    print(f"data folder {r['data_dir']}\ndatabase {r['database']} ({'created' if r['created_database'] else 'existing'})")
    print("administrator created" if r["created_admin"] else "users exist: none created")
    print(f"settings {r['config']} ({'written' if r['config_written'] else 'kept'}): {json.dumps(r['settings'])}")
    print(f"start:  spc-serve --config {r['config']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

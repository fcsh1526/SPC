"""Run the server: python -m spc.api  (or the `spc-serve` command)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import uvicorn

from spc.api.app import create_app
from spc.db import Database
from spc.monitor.notify import WebhookNotifier


CONFIG_KEYS = {"db": str, "host": str, "port": int, "secure_cookies": bool, "alert_webhook": str, "ssl_certfile": str, "ssl_keyfile": str}


def read_config(path: str) -> dict:
    """The settings file written by spc-setup: a JSON object with db, host, port, secure_cookies, alert_webhook, ssl_certfile, ssl_keyfile. Unknown keys are an error."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read the settings file {path}: {exc}")
    if not isinstance(data, dict) or set(data) - set(CONFIG_KEYS):
        raise SystemExit(f"the settings file {path} may only hold {sorted(CONFIG_KEYS)}")
    for k, v in data.items():
        if not isinstance(v, CONFIG_KEYS[k]) or (CONFIG_KEYS[k] is int and isinstance(v, bool)):
            raise SystemExit(f"setting {k} of {path} must be of type {CONFIG_KEYS[k].__name__}")
    return data


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SPC web server")
    parser.add_argument("--config", default=os.environ.get("SPC_CONFIG", ""), help="settings file (JSON) made by spc-setup (env SPC_CONFIG); options given here win over it")
    parser.add_argument("--ssl-certfile", default="", help="serve https with this certificate (PEM)")
    parser.add_argument("--ssl-keyfile", default="", help="the private key of the certificate (PEM)")
    parser.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1: local access only")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=os.environ.get("SPC_DB", "spc.sqlite3"), help="SQLite file (env SPC_DB)")
    parser.add_argument("--secure-cookies", action="store_true",
                        help="mark the session cookie Secure. Use it when the site is served over https (also behind a proxy)")
    parser.add_argument("--alert-webhook", default=os.environ.get("SPC_ALERT_WEBHOOK", ""),
                        help="URL that receives a JSON POST when an SPC monitor opens an incident (env SPC_ALERT_WEBHOOK)")
    pre, _ = parser.parse_known_args(argv)
    if pre.config:
        parser.set_defaults(**read_config(pre.config))
    args = parser.parse_args(argv)
    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        raise SystemExit("give both --ssl-certfile and --ssl-keyfile")
    db = Database(args.db)
    notifiers = [WebhookNotifier(args.alert_webhook)] if args.alert_webhook else []
    app = create_app(db, secure_cookies=True if (args.secure_cookies or args.ssl_certfile) else None, notifiers=notifiers)
    try:  # the OPC UA links that are enabled connect now and reconnect by themselves
        app.state.equipment.runner.start()
    except Exception as exc:
        print(f"OPC UA interface not started: {exc}")
    if app.state.auth.user_count() == 0:
        print(f"No users yet. Create the first administrator:  spc-admin --db {args.db} create-user NAME --role admin")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.secure_cookies and not args.ssl_certfile:
        print("WARNING: the server is reachable from the network without https. Passwords and session cookies "
              "travel in clear text. Put it behind an https proxy and use --secure-cookies.")
    tls = {"ssl_certfile": args.ssl_certfile, "ssl_keyfile": args.ssl_keyfile} if args.ssl_certfile else {}
    uvicorn.run(app, host=args.host, port=args.port, **tls)


if __name__ == "__main__":
    main()

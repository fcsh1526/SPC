"""Run the server: python -m spc.api  (or the `spc-serve` command)."""

from __future__ import annotations

import argparse
import os

import uvicorn

from spc.api.app import create_app
from spc.db import Database
from spc.monitor.notify import WebhookNotifier


def main() -> None:
    parser = argparse.ArgumentParser(description="SPC web server")
    parser.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1: local access only")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=os.environ.get("SPC_DB", "spc.sqlite3"), help="SQLite file (env SPC_DB)")
    parser.add_argument("--secure-cookies", action="store_true",
                        help="mark the session cookie Secure. Use it when the site is served over https (also behind a proxy)")
    parser.add_argument("--alert-webhook", default=os.environ.get("SPC_ALERT_WEBHOOK", ""),
                        help="URL that receives a JSON POST when an SPC monitor opens an incident (env SPC_ALERT_WEBHOOK)")
    args = parser.parse_args()
    db = Database(args.db)
    notifiers = [WebhookNotifier(args.alert_webhook)] if args.alert_webhook else []
    app = create_app(db, secure_cookies=True if args.secure_cookies else None, notifiers=notifiers)
    if app.state.auth.user_count() == 0:
        print(f"No users yet. Create the first administrator:  spc-admin --db {args.db} create-user NAME --role admin")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.secure_cookies:
        print("WARNING: the server is reachable from the network without https. Passwords and session cookies "
              "travel in clear text. Put it behind an https proxy and use --secure-cookies.")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

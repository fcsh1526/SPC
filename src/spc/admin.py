"""Command line administration. Needs access to the database file, so it is how the first user is made.

    spc-admin --db spc.sqlite3 create-user alice --role admin
    spc-admin --db spc.sqlite3 reset-password alice
    spc-admin --db spc.sqlite3 unlock alice
    spc-admin --db spc.sqlite3 list-users
    spc-admin --db spc.sqlite3 audit-verify
    spc-admin --db spc.sqlite3 backup --to D:\\SPC-backup
    spc-admin --db spc.sqlite3 restore D:\\SPC-backup\\spc-backup-20261008T093112Z.sqlite3

The password is asked on the terminal (not shown), or read from the first line of standard input with --password-stdin.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from spc.auth import AuthError, AuthService, ROLES
from spc.db import Database


def read_password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("The two passwords differ.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SPC administration")
    parser.add_argument("--db", default=os.environ.get("SPC_DB", "spc.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    pw = argparse.ArgumentParser(add_help=False)
    pw.add_argument("--password-stdin", action="store_true")
    create = sub.add_parser("create-user", parents=[pw])
    create.add_argument("username")
    create.add_argument("--role", choices=ROLES, default="engineer")
    create.add_argument("--display-name", default="")
    create.add_argument("--must-change", action="store_true", help="force a new password at the first login")
    reset = sub.add_parser("reset-password", parents=[pw])
    reset.add_argument("username")
    unlock = sub.add_parser("unlock")
    unlock.add_argument("username")
    sub.add_parser("list-users")
    sub.add_parser("audit-verify")
    backup = sub.add_parser("backup", help="write a checked copy of the database and its SHA-256 into a folder (the server may keep running)")
    backup.add_argument("--to", required=True, help="the folder")
    restore = sub.add_parser("restore", help="replace the database by a backup (stop the server first); the replaced file is kept")
    restore.add_argument("file")
    restore.add_argument("--yes", action="store_true", help="do it without asking")
    args = parser.parse_args(argv)

    if args.command in ("backup", "restore"):
        from spc import maintenance

        try:
            if args.command == "backup":
                r = maintenance.backup(args.db, args.to)
                print(f"backup {r['file']}\nsha256 {r['sha256']}\naudit entries {r['audit_entries']}")
            else:
                if not args.yes and input(f"Replace {args.db} by {args.file}? The server must be stopped. Type yes: ").strip().lower() != "yes":
                    return 1
                r = maintenance.restore(args.file, args.db)
                print(f"restored from {r['restored_from']}\nprevious database kept as {r['kept_previous']}\naudit chain {'ok' if r['audit_ok'] else 'BROKEN'} ({r['audit_entries']} entries)")
            return 0
        except maintenance.MaintenanceError as exc:
            print(f"error: {exc.code}: {exc}", file=sys.stderr)
            return 1

    auth = AuthService(Database(args.db))
    try:
        if args.command == "create-user":
            user = auth.create_user(args.username, read_password(args), args.role, args.display_name, must_change=args.must_change)
            print(f"created {user.username} ({user.role})")
        elif args.command == "reset-password":
            target = next((u for u in auth.list_users() if u.username.lower() == args.username.lower()), None)
            if target is None:
                raise SystemExit("No such user.")
            auth.reset_password(target.id, read_password(args), must_change=True)
            print(f"password of {target.username} reset. It must be changed at the next login.")
        elif args.command == "unlock":
            auth.unlock(args.username)
            print("unlocked")
        elif args.command == "list-users":
            for u in auth.list_users():
                print(f"{u.username}\t{u.role}\t{'active' if u.active else 'disabled'}\t{u.display_name}")
        elif args.command == "audit-verify":
            result = auth.audit.verify()
            print(result)
            return 0 if result["ok"] else 1
    except AuthError as exc:
        print(f"error: {exc.code} {exc.params or ''}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

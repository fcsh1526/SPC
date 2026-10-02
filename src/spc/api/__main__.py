"""Run the server: python -m spc.api  (or the `spc-serve` command)."""

from __future__ import annotations

import argparse

import uvicorn

from spc.api.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="SPC web server")
    parser.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1: local access only")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: there is no login yet. Anyone who can reach this port can read and change the data.")
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()

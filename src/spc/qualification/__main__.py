"""spc-qualify: installation and operational qualification of this installation.

    spc-qualify iq    [--db FILE] [--require-manifest] --out DIR     installation qualification
    spc-qualify oq    --out DIR                                      operational qualification (a scratch database; the site data are not touched)
    spc-qualify all   [--db FILE] [--require-manifest] --out DIR     both, one record
    spc-qualify verify RECORD.json                                   is the record unchanged?
    spc-qualify manifest [--root DIR]                                write RELEASE.json (the build does this)

Each run writes a JSON record and a printable HTML page (English and Traditional Chinese) into --out. The exit code is 0 when no check failed, 1 when one failed, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from spc.qualification import iq as IQ
from spc.qualification import manifest as M
from spc.qualification import oq as OQ
from spc.qualification import report as R


def _write(out: Path, record: dict, attachments: dict[str, bytes]) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    stamp = record["created_at"].replace(":", "").replace("-", "")
    base = f"{record['kind'].upper()}-{stamp}-{record['digest'][:8]}"
    paths = []
    for name, data in attachments.items():
        p = out / f"{base}-{name}"
        p.write_bytes(data)
        paths.append(p)
    p = out / f"{base}.json"
    p.write_text(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    paths.append(p)
    for lang in ("en", "zh-TW"):
        q = out / f"{base}.{lang}.html"
        q.write_text(R.render(record, lang), encoding="utf-8")
        paths.append(q)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SPC installation and operational qualification")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("iq", "oq", "all"):
        p = sub.add_parser(name)
        p.add_argument("--out", required=True, help="folder for the record")
        if name != "oq":
            p.add_argument("--db", default=None, help="the site database to inspect (read only)")
            p.add_argument("--manifest", default=None)
            p.add_argument("--require-manifest", action="store_true", help="a release must carry RELEASE.json: its absence fails")
            p.add_argument("--data-dir", default=None)
        if name != "iq":
            p.add_argument("--quiet", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("record")
    m = sub.add_parser("manifest")
    m.add_argument("--root", default=None)
    args = parser.parse_args(argv)

    if args.command == "verify":
        try:
            record = json.loads(Path(args.record).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"cannot read the record: {exc}", file=sys.stderr)
            return 2
        ok = R.verify_record(record)
        print("the record is unchanged" if ok else "THE RECORD WAS CHANGED: the digest does not match its content")
        return 0 if ok else 1
    if args.command == "manifest":
        record = M.write(args.root)
        print(f"{len(record['files'])} files, {len(record['dependencies'])} packages, digest {record['digest']}")
        return 0

    checks: list[dict] = []
    attachments: dict[str, bytes] = {}
    manifest = M.load(getattr(args, "manifest", None)) if args.command != "oq" else M.load()
    if manifest is not None and manifest.get("unreadable"):
        manifest = None
    if args.command in ("iq", "all"):
        checks += IQ.run_iq(args.db, args.manifest, args.require_manifest, args.data_dir)
    if args.command in ("oq", "all"):
        def say(text):
            if not args.quiet:
                print(f"  OQ: {text} ...", flush=True)
        checks += OQ.run_oq(progress=say, artifacts=attachments)
    kind = {"iq": "iq", "oq": "oq", "all": "iqoq"}[args.command]
    names = {k: hashlib.sha256(v).hexdigest() for k, v in attachments.items()}
    record = R.make_record(kind, checks, IQ.host_info(), manifest, names)
    paths = _write(Path(args.out), record, attachments)
    for ch in checks:
        if ch["status"] in ("fail", "warn"):
            print(f"{ch['status'].upper():5} {ch['id']}: expected {ch['expected']}, found {ch['actual']}{'. ' + ch['note'] if ch['note'] else ''}")
    c = record["counts"]
    print(f"{record['kind'].upper()}: {record['verdict'].upper()}  ({c['pass']} passed, {c['fail']} failed, {c['warn']} to review, {c['info']} information)")
    print(f"record digest {record['digest']}")
    for p in paths:
        print(f"  {p}")
    return 1 if c["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())

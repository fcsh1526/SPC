"""The release manifest: the SHA-256 of every file of the installed program, the version, and the versions of the packages it was built and tested with.

The build writes `RELEASE.json` into the package folder. Installation qualification compares the installed files with it, so a changed, missing or added file is found,
and the installed environment with the locked package versions. The manifest has a digest over its own content; the digest is what a customer records as "the release".
It shows that the files are the files of the build. It is not a signature: whoever can change the files and the manifest can change both. Give the digest to the customer
by another way (the delivery note, a signed mail) and compare.
"""

from __future__ import annotations

import hashlib
import json
from importlib import metadata
from pathlib import Path

import spc

MANIFEST_NAME = "RELEASE.json"
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def package_root() -> Path:
    return Path(spc.__file__).resolve().parent


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def file_hashes(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name == MANIFEST_NAME or p.suffix in SKIP_SUFFIXES or SKIP_DIRS & set(p.relative_to(root).parts):
            continue
        out[p.relative_to(root).as_posix()] = sha256_file(p)
    return out


def _dump(x) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest_of(record: dict) -> str:
    return hashlib.sha256(_dump({k: v for k, v in record.items() if k != "digest"}).encode("utf-8")).hexdigest()


def installed_distributions() -> dict[str, str]:
    out = {}
    for d in metadata.distributions():
        name = (d.metadata["Name"] or "").strip().lower().replace("_", "-")
        if name:
            out[name] = d.version
    return dict(sorted(out.items()))


def build(root: Path | None = None, built_at: str | None = None, dependencies: dict[str, str] | None = None) -> dict:
    import platform
    from datetime import datetime, timezone

    root = Path(root) if root else package_root()
    record = {"format": 1, "name": "spc", "version": spc.__version__, "built_at": built_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "python": platform.python_version(), "files": file_hashes(root), "dependencies": installed_distributions() if dependencies is None else dependencies}
    record["digest"] = digest_of(record)
    return record


def write(root: Path | None = None, **kw) -> dict:
    root = Path(root) if root else package_root()
    record = build(root, **kw)
    (root / MANIFEST_NAME).write_text(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return record


def load(path: Path | None = None) -> dict | None:
    p = Path(path) if path else package_root() / MANIFEST_NAME
    if not p.is_file():
        return None
    try:
        record = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"unreadable": True}
    return record


def verify(manifest: dict, root: Path | None = None) -> dict:
    """The files under `root` against the manifest: {'intact': bool (digest of the manifest itself), 'missing', 'modified', 'extra', 'ok'}."""
    root = Path(root) if root else package_root()
    intact = isinstance(manifest.get("digest"), str) and digest_of(manifest) == manifest["digest"]
    expected = manifest.get("files") or {}
    now = file_hashes(root)
    missing = sorted(k for k in expected if k not in now)
    modified = sorted(k for k in expected if k in now and now[k] != expected[k])
    extra = sorted(k for k in now if k not in expected)
    return {"intact": intact, "missing": missing, "modified": modified, "extra": extra, "files": len(expected), "ok": bool(intact and not missing and not modified and not extra)}

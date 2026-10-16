"""The lock files of the Windows installer (packaging/windows/requirements-*.lock, written by tools/lock_windows.py):
every package is pinned with the SHA-256 of its wheel, and the pinned versions satisfy what the program declares."""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def read_lock(path: Path) -> dict[str, tuple[str, list[str]]]:
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    pins = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([A-Za-z0-9._-]+)==(\S+)\s+(.*)$", line)
        assert m, f"not a pinned line: {line}"
        pins[canonicalize_name(m.group(1))] = (m.group(2), re.findall(r"--hash=sha256:([0-9a-f]{64})", m.group(3)))
    return pins


@pytest.mark.parametrize("extras", [("web", "sign"), ("web", "sign", "opcua")], ids=["web-sign", "web-sign-opcua"])
def test_the_lock_pins_every_declared_package_with_a_hash(extras):
    pins = read_lock(ROOT / "packaging" / "windows" / f"requirements-{'-'.join(extras)}.lock")
    assert pins and all(len(h) == 1 for _v, h in pins.values()), "every package has exactly one sha256"
    declared = [Requirement(r) for r in PROJECT["dependencies"] + [d for e in extras for d in PROJECT["optional-dependencies"][e]]]
    for r in declared:
        name = canonicalize_name(r.name)
        assert name in pins, f"{r.name} is not in the lock file"
        assert r.specifier.contains(pins[name][0], prereleases=True), f"{r.name}=={pins[name][0]} does not meet {r.specifier}"


def test_the_build_installs_from_the_lock_with_hashes():
    build = (ROOT / "packaging" / "windows" / "build.ps1").read_text(encoding="utf-8")
    assert "--require-hashes" in build and "requirements-" in build

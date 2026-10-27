"""The release packages of Windows and Linux: the lock files (packaging/<os>/requirements-*.lock, written by tools/lock_packages.py)
pin every package with the SHA-256 of its wheel, the pinned versions satisfy what the program declares, both systems use the same
versions, and the build and install scripts keep the rules that the qualification relies on."""

import re
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
SETS = [("web", "sign"), ("web", "sign", "opcua")]
LINUX = ROOT / "packaging" / "linux"


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


@pytest.mark.parametrize("system", ["windows", "linux"])
@pytest.mark.parametrize("extras", SETS, ids=["web-sign", "web-sign-opcua"])
def test_the_lock_pins_every_declared_package_with_a_hash(system, extras):
    pins = read_lock(ROOT / "packaging" / system / f"requirements-{'-'.join(extras)}.lock")
    assert pins and all(len(h) == 1 for _v, h in pins.values()), "every package has exactly one sha256"
    declared = [Requirement(r) for r in PROJECT["dependencies"] + [d for e in extras for d in PROJECT["optional-dependencies"][e]]]
    for r in declared:
        name = canonicalize_name(r.name)
        assert name in pins, f"{r.name} is not in the lock file"
        assert r.specifier.contains(pins[name][0], prereleases=True), f"{r.name}=={pins[name][0]} does not meet {r.specifier}"


@pytest.mark.parametrize("extras", SETS, ids=["web-sign", "web-sign-opcua"])
def test_windows_and_linux_use_the_same_versions(extras):
    name = f"requirements-{'-'.join(extras)}.lock"
    win, lin = read_lock(ROOT / "packaging" / "windows" / name), read_lock(LINUX / name)
    both = set(win) & set(lin)
    assert {n: win[n][0] for n in both} == {n: lin[n][0] for n in both}


def test_the_builds_install_from_the_lock_with_hashes():
    for build in (ROOT / "packaging" / "windows" / "build.ps1", LINUX / "build.sh"):
        text = build.read_text(encoding="utf-8")
        assert "--require-hashes" in text and "requirements-" in text, build
    sh = (LINUX / "build.sh").read_text(encoding="utf-8")
    assert re.search(r"^PBS_SHA256=[0-9a-f]{64}$", sh, re.M) and "sha256sum -c" in sh  # the Python is checked before it is used
    assert "--require-manifest" in sh  # the self-test of the build


def test_the_linux_scripts_are_valid_and_executable():
    scripts = [LINUX / "build.sh", LINUX / "install.sh", LINUX / "uninstall.sh", *sorted((LINUX / "bin").iterdir())]
    for s in scripts:
        assert s.stat().st_mode & stat.S_IXUSR, f"{s.name} is not executable"
        shell = "bash" if s.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash") else "sh"
        subprocess.run([shell, "-n", str(s)], check=True)
    commands = {p.name for p in (LINUX / "bin").iterdir()}
    windows = {p.stem for p in (ROOT / "packaging" / "windows" / "bin").iterdir()}
    assert windows <= commands  # the same commands on both systems


def test_the_install_script_keeps_the_rules_of_the_windows_installer():
    text = (LINUX / "install.sh").read_text(encoding="utf-8")
    for code, step in ((11, "no password"), (12, "first setup"), (13, "qualification"), (14, "service"), (15, "backup")):
        assert re.search(rf"fail {code} \"{step}", text), (code, step)
    assert "--require-manifest" in text and 'report "result ok"' in text
    unit = (LINUX / "spc.service").read_text(encoding="utf-8")
    assert all(k in unit for k in ("User=@USER@", "Restart=always", "ProtectSystem=strict", "ReadWritePaths=@DATA@", "NoNewPrivileges=yes"))

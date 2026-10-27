#!/usr/bin/env python3
"""Writes the lock files of the release packages: every package with its exact version and the SHA-256 of its wheel.

    python3 tools/lock_packages.py --os windows   # packaging/windows/requirements-web-sign.lock, requirements-web-sign-opcua.lock
    python3 tools/lock_packages.py --os linux     # packaging/linux/requirements-web-sign.lock, requirements-web-sign-opcua.lock

build.ps1 (Windows) and build.sh (Linux) install from such a file with `pip install --require-hashes`, so a package holds exactly
the packages that were tested, and a changed or replaced download stops the build. Run it again only on purpose (a new package version
is a change of the release), then run the tests and commit the new lock files.

The Linux lock takes the versions of the Windows lock where both have a package (`--same-versions-as`, on by default), so the two
releases differ only in the wheels of the platform and in packages that only one system needs.

The wheels are taken for 64-bit Windows or x86-64 Linux (manylinux, glibc 2.28 or newer) and the Python minor version of the
packaged Python. pip evaluates environment markers (`; sys_platform == "win32"`) for the machine it runs on, not for the target, so
this script follows the dependencies itself with the markers of the target system.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from datetime import date
from email.parser import Parser
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename

ROOT = Path(__file__).resolve().parents[1]
SETS = (("web", "sign"), ("web", "sign", "opcua"))
TARGETS = {
    "windows": {"platforms": ["win_amd64"],
                "env": {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt", "platform_machine": "AMD64"},
                "builder": "build.ps1"},
    "linux": {"platforms": ["manylinux_2_28_x86_64", "manylinux_2_27_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64"],
              "env": {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix", "platform_machine": "x86_64"},
              "builder": "build.sh"},
}


def target_env(target: str, python: str) -> dict:
    env = default_environment()
    env.update(TARGETS[target]["env"], implementation_name="cpython", platform_python_implementation="CPython",
               python_version=python, python_full_version=python + ".0")
    return env


def roots(extras: tuple[str, ...]) -> list[Requirement]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    reqs = list(project["dependencies"])
    for e in extras:
        reqs += project["optional-dependencies"][e]
    return [Requirement(r) for r in reqs]


def read_pins(path: Path) -> dict[str, str]:
    """name -> version of a lock file."""
    text = path.read_text(encoding="utf-8")
    return {canonicalize_name(m.group(1)): m.group(2) for m in re.finditer(r"^([A-Za-z0-9._-]+)==(\S+)", text, re.M)}


def download(specs: list[str], dest: Path, target: str, python: str, constraints: Path | None) -> None:
    platforms = [a for p in TARGETS[target]["platforms"] for a in ("--platform", p)]
    extra = ["-c", str(constraints)] if constraints else []
    subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check", "--dest", str(dest),
                    "--only-binary=:all:", *platforms, "--python-version", python, "--implementation", "cp", *extra, *specs], check=True)


def wheels(dest: Path) -> dict[str, Path]:
    found = {}
    for w in dest.glob("*.whl"):
        name, _version, _build, _tags = parse_wheel_filename(w.name)
        found[canonicalize_name(name)] = w
    return found


def requires(wheel: Path, extras: set[str], env: dict) -> list[Requirement]:
    with zipfile.ZipFile(wheel) as z:
        meta = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        msg = Parser().parsestr(z.read(meta).decode("utf-8"))
    out = []
    for line in msg.get_all("Requires-Dist") or []:
        r = Requirement(line)
        if r.marker is None or any(r.marker.evaluate({**env, "extra": e}) for e in (extras or {""})):
            out.append(r)
    return out


def lock(target: str, extras: tuple[str, ...], python: str, pins: dict[str, str]) -> str:
    env = target_env(target, python)
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "wheels"
        dest.mkdir()
        constraints = None
        if pins:
            constraints = Path(tmp) / "constraints.txt"
            constraints.write_text("".join(f"{n}=={v}\n" for n, v in sorted(pins.items())), encoding="utf-8")
        top = [r for r in roots(extras) if r.marker is None or r.marker.evaluate(env)]
        download([str(r) for r in top], dest, target, python, constraints)
        while True:  # follow the dependencies with the markers of the target until nothing is missing
            have = wheels(dest)
            needed: dict[str, set[str]] = {}
            todo = [(canonicalize_name(r.name), set(r.extras)) for r in top]
            missing: list[str] = []
            while todo:
                name, ex = todo.pop()
                if name in needed and ex <= needed[name]:
                    continue
                needed.setdefault(name, set()).update(ex)
                if name not in have:
                    missing.append(name)
                    continue
                for r in requires(have[name], needed[name], env):
                    todo.append((canonicalize_name(r.name), set(r.extras)))
            if not missing:
                break
            download(sorted(set(missing)), dest, target, python, constraints)
        lines = []
        for name in sorted(needed):
            w = have[name]
            version = str(parse_wheel_filename(w.name)[1])
            digest = hashlib.sha256(w.read_bytes()).hexdigest()
            lines.append(f"{name}=={version} \\\n    --hash=sha256:{digest}")
    head = (f"# Packages of the {target.capitalize()} release (extras {','.join(extras)}), CPython {python}.\n"
            f"# Written by tools/lock_packages.py on {date.today().isoformat()}; {TARGETS[target]['builder']} installs them with pip --require-hashes.\n"
            "# Do not edit by hand: run the script again, test, and commit the result.\n")
    return head + "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--os", choices=sorted(TARGETS), required=True, help="the system of the release")
    ap.add_argument("--python", default="3.11", help="minor version of the packaged Python (default 3.11)")
    ap.add_argument("--same-versions-as", default=None,
                    help="take the versions of this system's lock files (default for linux: windows; 'none' to take the newest)")
    args = ap.parse_args()
    same = args.same_versions_as or ("windows" if args.os == "linux" else "none")
    for extras in SETS:
        name = f"requirements-{'-'.join(extras)}.lock"
        pins = read_pins(ROOT / "packaging" / same / name) if same != "none" else {}
        path = ROOT / "packaging" / args.os / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = lock(args.os, extras, args.python, pins)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"{path.relative_to(ROOT)}: {text.count('--hash=')} packages")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Writes the lock files of the Windows build: every package of the installer with its exact version and the SHA-256 of its wheel.

    python3 tools/lock_windows.py            # writes packaging/windows/requirements-web-sign.lock and requirements-web-sign-opcua.lock

build.ps1 installs from such a file with `pip install --require-hashes`, so the installer holds exactly the packages that were tested,
and a changed or replaced download stops the build. Run it again only on purpose (a new package version is a change of the release),
then run the tests and commit the new lock files.

The wheels are taken for 64-bit Windows and the Python minor version of the embedded Python. pip evaluates environment markers
(`; sys_platform == "win32"`) for the machine it runs on, not for the target, so this script follows the dependencies itself with the
markers of Windows: it adds what only Windows needs (colorama for click) and leaves out what only other systems need.
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
OUT = ROOT / "packaging" / "windows"
SETS = (("web", "sign"), ("web", "sign", "opcua"))


def windows_env(python: str) -> dict:
    env = default_environment()
    env.update(sys_platform="win32", platform_system="Windows", os_name="nt", platform_machine="AMD64",
               implementation_name="cpython", platform_python_implementation="CPython",
               python_version=python, python_full_version=python + ".0")
    return env


def roots(extras: tuple[str, ...]) -> list[Requirement]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    reqs = list(project["dependencies"])
    for e in extras:
        reqs += project["optional-dependencies"][e]
    return [Requirement(r) for r in reqs]


def download(specs: list[str], dest: Path, python: str) -> None:
    subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check", "--dest", str(dest),
                    "--only-binary=:all:", "--platform", "win_amd64", "--python-version", python, "--implementation", "cp", *specs], check=True)


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


def lock(extras: tuple[str, ...], python: str) -> str:
    env = windows_env(python)
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        top = [r for r in roots(extras) if r.marker is None or r.marker.evaluate(env)]
        download([str(r) for r in top], dest, python)
        while True:  # follow the dependencies with the markers of Windows until nothing is missing
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
            download(sorted(set(missing)), dest, python)
        lines = []
        for name in sorted(needed):
            w = have[name]
            version = str(parse_wheel_filename(w.name)[1])
            digest = hashlib.sha256(w.read_bytes()).hexdigest()
            lines.append(f"{name}=={version} \\\n    --hash=sha256:{digest}")
    head = (f"# Packages of the Windows installer (extras {','.join(extras)}), 64-bit Windows, CPython {python}.\n"
            f"# Written by tools/lock_windows.py on {date.today().isoformat()}; build.ps1 installs them with pip --require-hashes.\n"
            "# Do not edit by hand: run the script again, test, and commit the result.\n")
    return head + "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--python", default="3.11", help="minor version of the embedded Python (default 3.11)")
    args = ap.parse_args()
    for extras in SETS:
        path = OUT / f"requirements-{'-'.join(extras)}.lock"
        text = lock(extras, args.python)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"{path.relative_to(ROOT)}: {text.count('--hash=')} packages")
    return 0


if __name__ == "__main__":
    sys.exit(main())

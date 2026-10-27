#!/usr/bin/env bash
# Builds the Linux release of SPC (x86-64, glibc 2.28 or newer): a standalone Python, the program and its packages (locked versions
# and hashes), the release manifest, a self-test (IQ/OQ of the built tree), and dist/spc-<version>-linux-x86_64.tar.gz.
#
#   packaging/linux/build.sh            # web and sign packages
#   WITH_OPCUA=1 packaging/linux/build.sh
#
# Needs: Linux x86-64, bash, curl, tar, sha256sum, internet access to github.com (the Python) and PyPI (the packages).
# It is a build step of the vendor, not something a customer runs. The customer gets the .tar.gz and runs install.sh in it.
#
# The Python is python-build-standalone (a relocatable CPython, https://github.com/astral-sh/python-build-standalone), the same role
# as the embedded Python of the Windows installer. Its archive is checked against PBS_SHA256 (from the SHA256SUMS file of that release);
# the build stops when it differs. The self-test runs the installation and operational qualification on the built tree; a failed check
# stops the build.
set -euo pipefail

PY_VERSION=3.11.9
PBS_RELEASE=20240415
PBS_SHA256=78b1c16a9fd032997ba92a60f46a64f795cd18ff335659dfdf6096df277b24d5

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
out="${OUT_DIR:-$root/dist}"
extras="web,sign"
if [ "${WITH_OPCUA:-0}" = "1" ]; then extras="web,sign,opcua"; fi
version="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$root/pyproject.toml" | head -n 1)"
name="spc-$version-linux-x86_64"
build="$root/build/linux"
app="$build/$name"
echo "SPC $version, Python $PY_VERSION, packages $extras"
[ "$(uname -m)" = "x86_64" ] || { echo "build on x86-64 Linux" >&2; exit 2; }
rm -rf "$build"
mkdir -p "$app" "$out"

# 1. the standalone Python, checked against its published SHA-256
archive="cpython-$PY_VERSION+$PBS_RELEASE-x86_64-unknown-linux-gnu-install_only.tar.gz"
curl -fsSL -o "$build/$archive" "https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_RELEASE/$archive"
echo "$PBS_SHA256  $build/$archive" | sha256sum -c - || { echo "The Python archive has another SHA-256. Build stopped." >&2; exit 1; }
tar -xzf "$build/$archive" -C "$app"   # gives $app/python
py="$app/python/bin/python3"

# 2. the packages from the lock file (exact versions, SHA-256 of every wheel; any other file or an unlisted dependency stops the build),
#    then the program itself, built into a wheel and installed without dependencies
lock="$here/requirements-${extras//,/-}.lock"
"$py" -m pip install --quiet --disable-pip-version-check --no-warn-script-location --only-binary=:all: --require-hashes -r "$lock"
"$py" -m pip wheel --quiet --disable-pip-version-check --no-deps --wheel-dir "$build/wheels" "$root"
"$py" -m pip install --quiet --disable-pip-version-check --no-warn-script-location --no-deps "$build/wheels/spc-$version-"*.whl

# 3. the manifest of the release (SHA-256 of every file of the program, locked package versions)
site="$("$py" -c 'import os, spc; print(os.path.dirname(spc.__file__))')"
"$py" -m spc.qualification manifest --root "$site"

# 4. the self-test: IQ and OQ of the built tree
"$py" -m spc.qualification all --quiet --out "$build/qualification" --require-manifest \
  || { echo "The qualification of the build failed. See $build/qualification. Build stopped." >&2; exit 1; }

# 5. launchers, the service file, the install scripts and the instructions
cp -r "$here/bin" "$app/bin"
cp "$here/install.sh" "$here/uninstall.sh" "$here/spc.service" "$app/"
cp "$root/docs/INSTALLATION.md" "$app/INSTALLATION.md"
chmod 755 "$app/install.sh" "$app/uninstall.sh" "$app/bin/"*

# 6. the archive, its digest and the release manifest digest, for the delivery note
tar -C "$build" -czf "$out/$name.tar.gz" "$name"
sha="$(sha256sum "$out/$name.tar.gz" | cut -d ' ' -f 1)"
digest="$("$py" -c 'import json, sys; print(json.load(open(sys.argv[1]))["digest"])' "$site/RELEASE.json")"
printf '%s.tar.gz  sha256 %s\nrelease manifest digest %s\nbuilt-in qualification record: see build-qualification-%s-linux/\n' \
  "$name" "$sha" "$digest" "$version" > "$out/$name.txt"
rm -rf "$out/build-qualification-$version-linux"
cp -r "$build/qualification" "$out/build-qualification-$version-linux"
echo "Done: $out/$name.tar.gz"
echo "sha256 $sha"
echo "release manifest $digest"

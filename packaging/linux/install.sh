#!/usr/bin/env bash
# Installs or upgrades SPC on a Linux server with systemd. Run it as root in the unpacked release folder:
#
#   sudo ./install.sh --password-file pw.txt [--admin admin] [--data-dir /var/lib/spc] [--prefix /opt/spc]
#                     [--host 127.0.0.1] [--port 8443] [--tls self-signed|none] [--timezone Asia/Taipei] [--no-oq] [--no-service]
#
# Steps, as the Windows installer: (upgrade: stop the server, make a checked backup) -> copy the program to --prefix -> service account
# "spc" -> first setup (folders, database, administrator, settings, certificate) -> installation qualification (IQ) and, unless --no-oq,
# operational qualification (OQ) -> systemd service "spc", enabled and started. A failed step leaves the server stopped.
#
# --password-file names a file whose first line is the password of the first administrator; it is needed only when the data folder has
# no database yet. The given file is not deleted: delete it yourself. --no-service skips systemd (a container, or a test); start the
# server then with: <prefix>/bin/spc-serve --config <data-dir>/spc.json
#
# Every step writes "SPC: ..." on the screen and into <data-dir>/logs/install.log with the full output of the step. The last line is
# "SPC: result ok" or "SPC: result failed ...". Exit code: 0 installed, qualified and started; 2 wrong call; 11 no password for the first
# administrator; 12 first setup; 13 qualification (IQ/OQ); 14 service; 15 backup before the upgrade.
set -uo pipefail

src="$(cd "$(dirname "$0")" && pwd)"
prefix=/opt/spc
data=/var/lib/spc
host=127.0.0.1
port=8443
tls=self-signed
admin=admin
pwfile=""
tz=""
oq=1
service=1
user=spc

usage() { sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) prefix="$2"; shift 2 ;;
    --data-dir) data="$2"; shift 2 ;;
    --host) host="$2"; shift 2 ;;
    --port) port="$2"; shift 2 ;;
    --tls) tls="$2"; shift 2 ;;
    --admin) admin="$2"; shift 2 ;;
    --password-file) pwfile="$2"; shift 2 ;;
    --timezone) tz="$2"; shift 2 ;;
    --no-oq) oq=0; shift ;;
    --no-service) service=0; shift ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done

[ "$(id -u)" = "0" ] || { echo "run install.sh as root (sudo)" >&2; exit 2; }
[ -x "$src/python/bin/python3" ] && [ -d "$src/bin" ] || { echo "run install.sh in the unpacked release folder" >&2; exit 2; }
case "$tls" in self-signed|none) ;; *) echo "--tls is self-signed or none" >&2; exit 2 ;; esac
case "$port" in ''|*[!0-9]*) echo "--port is a number" >&2; exit 2 ;; esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then echo "--port is a number from 1 to 65535" >&2; exit 2; fi
if [ "$service" = "1" ] && ! command -v systemctl >/dev/null 2>&1; then echo "systemd not found: give --no-service" >&2; exit 2; fi
if [ -n "$pwfile" ]; then pwfile="$(cd "$(dirname "$pwfile")" && pwd)/$(basename "$pwfile")"; fi
case "$prefix" in /*) ;; *) echo "--prefix is an absolute path" >&2; exit 2 ;; esac
case "$data" in /*) ;; *) echo "--data-dir is an absolute path" >&2; exit 2 ;; esac
case "$data/" in "$prefix"/*) echo "--data-dir must not lie inside --prefix (an upgrade replaces --prefix)" >&2; exit 2 ;; esac

mkdir -p "$data/logs"
log="$data/logs/install.log"
report() { echo "SPC: $*"; echo "SPC: $*" >> "$log"; }
step() { echo "== $(date -u +%Y-%m-%dT%H:%M:%SZ)  $*" >> "$log"; "$@" >> "$log" 2>&1; }
fail() { report "result failed: $2"; exit "$1"; }
report "install $(basename "$src") to $prefix, data in $data"

# the service account: owns the data folder, runs the server, has no login
if ! id -u "$user" >/dev/null 2>&1; then
  useradd --system --home-dir "$data" --no-create-home --shell /usr/sbin/nologin "$user" || fail 2 "service account"
fi

existing=0
[ -f "$data/spc.sqlite3" ] && existing=1
# an upgrade: stop the server and make a checked copy of the database before any file is replaced
if [ "$service" = "1" ] && systemctl is-active --quiet spc 2>/dev/null; then systemctl stop spc; fi
if [ "$existing" = "1" ] && [ -x "$prefix/bin/spc-admin" ]; then
  chown -R "$user:$user" "$data"
  step "$prefix/bin/spc-admin" --db "$data/spc.sqlite3" backup --to "$data/backup"
  code=$?
  report "backup before the upgrade, code $code"
  [ "$code" = "0" ] || fail 15 "backup before the upgrade"
fi

# the program: a new copy replaces the old one as a whole (the data folder is elsewhere and stays)
new="$prefix.new.$$"
rm -rf "$new"
cp -a "$src" "$new" || fail 2 "copy of the program"
chown -R root:root "$new"
chmod -R go-w "$new"
if [ -d "$prefix" ]; then rm -rf "$prefix.old"; mv "$prefix" "$prefix.old"; fi
mv "$new" "$prefix" || fail 2 "copy of the program"
rm -rf "$prefix.old"
report "program copied"

# the data folder belongs to the service account; others have no access (password hashes and all data are in it)
chown -R "$user:$user" "$data"
chmod 750 "$data"

# first setup: folders, database, administrator, settings, certificate (an existing installation keeps users and settings)
args=(--data-dir "$data" --host "$host" --port "$port" --tls "$tls")
[ -n "$tz" ] && args+=(--display-timezone "$tz")
tmp=""
if [ "$existing" = "0" ]; then
  if [ -z "$pwfile" ] || [ ! -s "$pwfile" ]; then fail 11 "no password for the first administrator (give --password-file)"; fi
  tmp="$(mktemp "$data/.first-password.XXXXXX")"
  head -n 1 "$pwfile" > "$tmp"
  chown "$user:$user" "$tmp"
  chmod 600 "$tmp"
  args+=(--admin "$admin" --password-file "$tmp")
fi
step "$prefix/bin/spc-setup" "${args[@]}"
code=$?
[ -n "$tmp" ] && rm -f "$tmp"   # the password never stays on the disk, also when the setup failed
report "first setup, code $code"
[ "$code" = "0" ] || fail 12 "first setup"

# installation qualification, and operational qualification unless --no-oq; a failed check keeps the server stopped
if [ "$oq" = "1" ]; then
  step "$prefix/bin/spc-qualify" all --quiet --out "$data/qualification" --db "$data/spc.sqlite3" --require-manifest
else
  step "$prefix/bin/spc-qualify" iq --out "$data/qualification" --db "$data/spc.sqlite3" --require-manifest
fi
code=$?
report "qualification, code $code"
[ "$code" = "0" ] || fail 13 "qualification (read the record in $data/qualification)"

# the systemd service: starts with the computer, restarts after a stop, runs as the service account
if [ "$service" = "1" ]; then
  unit=/etc/systemd/system/spc.service
  sed -e "s|@PREFIX@|$prefix|g" -e "s|@DATA@|$data|g" -e "s|@USER@|$user|g" "$prefix/spc.service" > "$unit" || fail 14 "service"
  if [ "$port" -lt 1024 ]; then sed -i 's|^#AmbientCapabilities=|AmbientCapabilities=|' "$unit"; fi
  step systemctl daemon-reload && step systemctl enable spc && step systemctl restart spc
  code=$?
  report "server service, code $code"
  [ "$code" = "0" ] || fail 14 "service"
else
  report "server service skipped (--no-service): start it with $prefix/bin/spc-serve --config $data/spc.json"
fi
scheme=https
[ "$tls" = "none" ] && [ "$existing" = "0" ] && scheme=http
report "open ${scheme}://<this server>:<port of $data/spc.json>/ in a browser"
report "result ok"
exit 0

#!/usr/bin/env bash
# Removes the SPC program and its systemd service. Run it as root:  sudo /opt/spc/uninstall.sh [--prefix /opt/spc]
# The data folder (database, settings, certificates, backups, qualification records) and the service account "spc" stay:
# delete them yourself after the backups and the retention period are settled (rm -rf /var/lib/spc; userdel spc).
set -uo pipefail
prefix=/opt/spc
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) prefix="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" = "0" ] || { echo "run uninstall.sh as root (sudo)" >&2; exit 2; }
case "$prefix" in /|/usr|/usr/*|/etc|/var|/home) echo "refusing to remove $prefix" >&2; exit 2 ;; esac
[ -x "$prefix/python/bin/python3" ] || { echo "no SPC installation in $prefix" >&2; exit 2; }
if command -v systemctl >/dev/null 2>&1 && [ -f /etc/systemd/system/spc.service ]; then
  systemctl disable --now spc >/dev/null 2>&1
  rm -f /etc/systemd/system/spc.service
  systemctl daemon-reload
  echo "SPC: service removed"
fi
cd /
rm -rf "$prefix"
echo "SPC: program removed from $prefix; the data folder and the account spc are kept"

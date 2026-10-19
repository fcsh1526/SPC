"""The time zone in which the screens show times (the plant's zone).

Every time is stored, hashed, exported and signed in UTC ('...Z'). The screens show it in one zone for the whole plant, the
`display_timezone` of spc.json (an IANA name such as Asia/Taipei), always with its offset, so a time on a screen can be matched
with a record. The zone is a setting of the server, not of each browser: two screens never show the same record at different times.
spc-setup proposes the zone of the computer; without one the screens use UTC. A change of the zone is written into the audit trail.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT = "UTC"

# the Windows names of the zones a plant is most likely in (tzutil /g), and their IANA names; other zones are given with --display-timezone
WINDOWS_ZONES = {
    "UTC": "UTC", "Coordinated Universal Time": "UTC", "GMT Standard Time": "Europe/London",
    "Taipei Standard Time": "Asia/Taipei", "China Standard Time": "Asia/Shanghai", "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul", "Singapore Standard Time": "Asia/Singapore", "SE Asia Standard Time": "Asia/Bangkok",
    "India Standard Time": "Asia/Kolkata", "W. Europe Standard Time": "Europe/Berlin", "Central Europe Standard Time": "Europe/Budapest",
    "Romance Standard Time": "Europe/Paris", "Central European Standard Time": "Europe/Warsaw", "E. Europe Standard Time": "Europe/Chisinau",
    "Eastern Standard Time": "America/New_York", "Central Standard Time": "America/Chicago", "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles", "Central Standard Time (Mexico)": "America/Mexico_City",
    "E. South America Standard Time": "America/Sao_Paulo", "AUS Eastern Standard Time": "Australia/Sydney",
}


class InvalidTimezone(ValueError):
    pass


def validate(name: str) -> str:
    """The name when it is a time zone this computer knows (IANA, e.g. Asia/Taipei), else InvalidTimezone."""
    name = (name or "").strip()
    if not name:
        raise InvalidTimezone("empty time zone name")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTimezone(f"unknown time zone {name!r}: give an IANA name such as Asia/Taipei or UTC") from exc
    return name


def detect() -> str:
    """The IANA name of this computer's zone, or UTC when it cannot be found."""
    candidates: list[str] = []
    if os.environ.get("TZ"):
        candidates.append(os.environ["TZ"].lstrip(":"))
    if os.name == "nt":
        try:
            r = subprocess.run(["tzutil", "/g"], capture_output=True, text=True, timeout=10)
            candidates.append(WINDOWS_ZONES.get(r.stdout.strip(), ""))
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            candidates.append(Path("/etc/timezone").read_text(encoding="utf-8").strip())
        except OSError:
            pass
        try:
            target = os.path.realpath("/etc/localtime")
            if "zoneinfo/" in target:
                candidates.append(target.split("zoneinfo/", 1)[1])
        except OSError:
            pass
    for c in candidates:
        try:
            return validate(c)
        except InvalidTimezone:
            continue
    return DEFAULT


def record_change(audit, name: str) -> bool:
    """Writes 'display_timezone_set' into the audit trail when the zone differs from the last one recorded. True when it wrote."""
    row = audit.db.one("SELECT detail FROM audit WHERE action = 'display_timezone_set' ORDER BY id DESC LIMIT 1")
    before = json.loads(row["detail"]).get("to") if row else None
    if before == name:
        return False
    audit.append("display_timezone_set", username="system", target="spc.json", detail={"from": before, "to": name})
    return True

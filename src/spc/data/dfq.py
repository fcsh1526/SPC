"""The descriptive part of the quality data exchange format of ISO/TR 11462-5:2023 (the files *.DFD and *.DFQ): parts and characteristics.

What the copy of the standard that was available defines, and what is read here (clauses 4.1 to 4.3, tables 1 and 2, the first fields of table 3):
  * one field per line, `Kxxxx` and the content separated by a space, lines ended by CR LF (LF alone is read as well);
  * `K0100 n` is the first line of the header: the number of characteristics in the file; with several elements the key is extended by `/i` (i = 1 to n); `/0` gives a
    field to all characteristics at once (for example `K2022/0 2`);
  * K1000 to K1999 describe the part (table 1), K2000 to K2999 the characteristics (table 2), K0001 to K0999 the values (table 3), K5000 to K5999 the structure,
    K8000 to K8999 the control chart; as soon as a key of the characteristics appears the part header is complete and no K1xxx may follow;
  * the mandatory fields: K0100, at least one K1xxx and at least one K2xxx (K1001, K1002, K2001 and K2002 are recommended).
The preview that was available ends in the middle of table 3 (up to K0017): the clauses on the structure and the control chart keys, the writing modes (clause 6), the catalogues
(clause 7) and the examples (annex A) are not in it. So the VALUES (K0001 and the other fields of table 3) are NOT read: only the descriptive data are, and a file with values is
reported as such. The fields marked "o" in the standard (for example K2005, K2120, K2121) have a content that the standard leaves to be agreed with the supplier of the
evaluation software: they are kept as they are and not interpreted.
The limits: K2110 and K2111 are the lower and upper specification limits. The standard says that only one of the combinations K2101/K2110/K2111 and K2101/K2112/K2113 is shown to the user
but all five are in the file. When only the allowances are there, the limits are the nominal value minus the size of the lower allowance and plus the size of the upper one.
"""

from __future__ import annotations

import math
import re
from typing import Any

MAX_LINES = 200_000
MAX_CHARACTERISTICS = 5000
KEY = re.compile(r"^K(\d{4})(?:/(\d+))?(?:\s(.*))?$")

# table 1: part, (name, type, maximum number of characters)
PART_KEYS = {1001: ("part_number", "A", 30), 1002: ("part_description", "A", 80), 1003: ("part_abbreviation", "A", 20), 1004: ("part_amendment_status", "A", 20), 1005: ("product", "A", 40),
             1007: ("part_number_abbreviated", "A", 20), 1008: ("part_type", "A", 20), 1009: ("part_code", "A", 20), 1011: ("variant", "A", 20), 1022: ("manufacturer", "A", 80),
             1041: ("drawing_number", "A", 30), 1042: ("drawing_amendment", "A", 20), 1053: ("contract", "A", 40), 1072: ("supplier_description", "A", 40), 1081: ("machine_number", "A", 24),
             1082: ("machine_description", "A", 40), 1083: ("machine_number_numeric", "I5", 5), 1085: ("machine_location", "A", 40), 1086: ("operation", "A", 40),
             1087: ("operation_description", "A", 40), 1100: ("plant_sector", "A", 40), 1101: ("department", "A", 40), 1102: ("workshop", "A", 40), 1103: ("cost_centre", "A", 40),
             1110: ("order_number", "A", 20), 1201: ("test_facility_number", "A", 24), 1202: ("test_facility_description", "A", 40), 1203: ("reason_for_test", "A", 80),
             1206: ("test_location", "A", 40), 1209: ("inspection_type", "A", 20), 1230: ("gauge_room", "A", 40), 1231: ("measuring_program_number", "A", 20),
             1232: ("measuring_program_version", "A", 20), 1303: ("plant", "A", 40), 1343: ("test_plan_date", "A", 20), 1344: ("test_plan_developer", "A", 40),
             1802: ("user_field_1", "A", 255), 1900: ("remark", "A", 255)}
# table 2: characteristics
CHAR_KEYS = {2001: ("number", "A", 20), 2002: ("description", "A", 80), 2003: ("abbreviation", "A", 20), 2004: ("type", "I5", 5), 2005: ("class", "I5", 5), 2006: ("control_item", "I5", 5),
             2007: ("control_type", "I5", 5), 2008: ("group_type", "I5", 5), 2009: ("measured_quantity", "I5", 5), 2015: ("tool_wear_type", "I3", 3), 2016: ("full_measurement", "I3", 3),
             2019: ("ordinal_classes_catalogue", "I3", 3), 2022: ("decimals", "I5", 5), 2043: ("measuring_device", "A", 40), 2060: ("events_catalogue", "I5", 5),
             2061: ("process_parameter_catalogue", "I5", 5), 2062: ("cavity_catalogue", "I5", 5), 2063: ("machine_catalogue", "I5", 5), 2064: ("gauge_catalogue", "I5", 5),
             2065: ("operator_catalogue", "I5", 5), 2066: ("subcatalogue_k0061", "I5", 5), 2067: ("subcatalogue_k0062", "I5", 5), 2068: ("subcatalogue_k0063", "I5", 5),
             2092: ("characteristic_text", "A", 50), 2093: ("processing_status", "A", 80), 2100: ("target", "F", 22), 2101: ("nominal", "F", 22), 2110: ("lsl", "F", 22), 2111: ("usl", "F", 22),
             2112: ("lower_allowance", "F", 22), 2113: ("upper_allowance", "F", 22), 2114: ("lower_scrap_limit", "F", 22), 2115: ("upper_scrap_limit", "F", 22),
             2120: ("lower_limit_type", "I3", 3), 2121: ("upper_limit_type", "I3", 3), 2130: ("lower_plausibility_limit", "F", 22), 2131: ("upper_plausibility_limit", "F", 22),
             2142: ("unit", "A", 20), 2301: ("machine_number", "A", 20), 2302: ("machine_description", "A", 40), 2303: ("department", "A", 40), 2311: ("production_type", "A", 20),
             2312: ("production_type_description", "A", 40), 2320: ("contract_number", "A", 20), 2401: ("gauge_number", "A", 40), 2402: ("gauge_description", "A", 40),
             2403: ("gauge_group", "A", 20), 2404: ("gauge_resolution", "F", 22), 2406: ("gauge_manufacturer", "A", 40), 2407: ("spc_device_number", "A", 20),
             2408: ("spc_device_manufacturer", "A", 40), 2409: ("spc_device_type", "A", 20), 2410: ("test_location", "A", 40), 2411: ("test_begin", "A", 40), 2415: ("gauge_serial_number", "A", 20),
             2440: ("assembly_component", "A", 40), 2505: ("view_description", "A", 20), 2506: ("sheet_number", "I3", 3), 2630: ("calibration_uncertainty", "F", 22), 2900: ("remark", "A", 255)}
INT_RANGE = {"I3": 127, "I5": 32767, "I10": 2147483647}


class DfqError(ValueError):
    """The file cannot be read as the descriptive data of ISO/TR 11462-5."""


def _warn(out: list, code: str, line: int | None = None, **params) -> None:
    out.append({"code": code, "line": line, **params})


def _value(raw: str, kind: str, limit: int, key: int, line: int, warnings: list) -> Any:
    text = raw.strip()
    if kind == "A":
        if len(raw.rstrip("\r")) > limit:
            _warn(warnings, "field_too_long", line, key=f"K{key:04d}", limit=limit)
        return raw.rstrip("\r")
    if kind in INT_RANGE:
        try:
            v = int(text)
        except ValueError:
            _warn(warnings, "bad_number", line, key=f"K{key:04d}")
            return None
        if not 0 <= v <= INT_RANGE[kind]:
            _warn(warnings, "out_of_range", line, key=f"K{key:04d}", limit=INT_RANGE[kind])
        return v
    if kind == "F":
        try:
            v = float(text.replace(",", "."))
        except ValueError:
            _warn(warnings, "bad_number", line, key=f"K{key:04d}")
            return None
        if not math.isfinite(v):
            _warn(warnings, "bad_number", line, key=f"K{key:04d}")
            return None
        return v
    return raw


def parse(data: bytes | str) -> dict[str, Any]:
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
    else:
        text = data
    lines = text.split("\n")
    if len(lines) > MAX_LINES:
        raise DfqError("the file has too many lines")
    warnings: list[dict] = []
    declared: int | None = None
    part: dict[str, Any] = {}
    raw_part: dict[str, str] = {}
    chars: dict[int, dict[str, Any]] = {}
    raw_chars: dict[int, dict[str, str]] = {}
    globals_: dict[int, dict[str, Any]] = {}
    seen_characteristic = False
    values_lines = 0
    unknown: set[str] = set()
    first_key_line = True
    for n, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        m = KEY.match(line)
        if m is None:
            if KEY.match(line.strip()) is None:
                values_lines += 1  # a value record: the writing modes are not in the available pages
            continue
        key, idx, content = int(m.group(1)), (int(m.group(2)) if m.group(2) is not None else None), m.group(3) or ""
        if first_key_line:
            first_key_line = False
            if key != 100:
                _warn(warnings, "k0100_not_first", n)
        if key == 100:
            try:
                declared = int(content.strip())
            except ValueError:
                raise DfqError(f"line {n}: K0100 must be the number of characteristics") from None
            if not 1 <= declared <= MAX_CHARACTERISTICS:
                raise DfqError(f"line {n}: K0100 must be from 1 to {MAX_CHARACTERISTICS}")
            continue
        if 1000 <= key <= 1999:
            if seen_characteristic:
                _warn(warnings, "part_field_after_characteristic", n, key=f"K{key:04d}")
            spec = PART_KEYS.get(key)
            if spec is None:
                unknown.add(f"K{key:04d}")
                raw_part[f"K{key:04d}"] = content
                continue
            part[spec[0]] = _value(content, spec[1], spec[2], key, n, warnings)
            raw_part[f"K{key:04d}"] = content
        elif 2000 <= key <= 2999:
            seen_characteristic = True
            spec = CHAR_KEYS.get(key)
            if spec is None:
                unknown.add(f"K{key:04d}")
            val = _value(content, spec[1], spec[2], key, n, warnings) if spec else content
            name = spec[0] if spec else f"K{key:04d}"
            if idx == 0:
                globals_.setdefault(key, {})["value"] = (name, val, content)
            else:
                i = 1 if idx is None else idx
                if declared is not None and i > declared:
                    _warn(warnings, "index_above_k0100", n, key=f"K{key:04d}", index=i)
                chars.setdefault(i, {})[name] = val
                raw_chars.setdefault(i, {})[f"K{key:04d}"] = content
        # K0xxx (values), K5xxx (structure) and K8xxx (control chart) are not read
        elif 1 <= key <= 999:
            values_lines += 1
    if declared is None:
        raise DfqError("K0100, the number of characteristics, is missing: this is not a file of ISO/TR 11462-5")
    if not part:
        _warn(warnings, "no_part_field")
    if not chars and not globals_:
        _warn(warnings, "no_characteristic_field")
    for key, g in globals_.items():
        name, val, content = g["value"]
        for i in range(1, declared + 1):
            chars.setdefault(i, {}).setdefault(name, val)
            raw_chars.setdefault(i, {}).setdefault(f"K{key:04d}", content)
    if chars and max(chars) != declared and len(chars) != declared:
        _warn(warnings, "count_differs", None, declared=declared, found=len(chars))
    if part and "part_number" not in part and "part_description" not in part:
        _warn(warnings, "part_not_identified")
    out_chars = []
    for i in sorted(chars):
        c = chars[i]
        if "number" not in c and "description" not in c:
            _warn(warnings, "characteristic_not_identified", None, index=i)
        lsl, usl = c.get("lsl"), c.get("usl")
        nominal = c.get("nominal") if c.get("nominal") is not None else c.get("target")
        if (lsl is None or usl is None) and c.get("nominal") is not None:
            if lsl is None and c.get("lower_allowance") is not None:
                lsl = c["nominal"] - abs(c["lower_allowance"])
            if usl is None and c.get("upper_allowance") is not None:
                usl = c["nominal"] + abs(c["upper_allowance"])
        if lsl is not None and usl is not None and not lsl < usl:
            _warn(warnings, "limits_not_ordered", None, index=i)
            lsl = usl = None
        out_chars.append({"index": i, "name": c.get("description") or c.get("number") or f"#{i}", "fields": c, "raw": raw_chars.get(i, {}), "unit": c.get("unit"), "lsl": lsl, "usl": usl,
                          "nominal": nominal, "decimals": c.get("decimals"), "resolution": c.get("gauge_resolution"), "calibration_uncertainty": c.get("calibration_uncertainty"),
                          "gauge": c.get("gauge_description") or c.get("gauge_number")})
    if values_lines:
        _warn(warnings, "values_not_read", None, lines=values_lines)
    if unknown:
        _warn(warnings, "unknown_keys", None, keys=sorted(unknown)[:20])
    return {"declared": declared, "part": part, "raw_part": raw_part, "characteristics": out_chars, "warnings": warnings, "values_present": bool(values_lines)}

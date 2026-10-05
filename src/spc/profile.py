"""Customer profiles: the agreed analysis settings and the report layout of one customer.

A profile has two parts.

analysis  Settings that the customer agreement fixes. Only the keys that are present count:
          alpha, estimate_confidence, target_confidence, edition, stability_mode, stability_confidence,
          rules (the stability criteria that are switched on) and targets (the Pp/Ppk, Cp/Cpk and
          Pm/Pmk targets per stage and characteristic class, replacing the draft's example values).
report    Layout and content of the report: organisation line, title, form number, revision, footer,
          accent colour, logo, default language, whether the optional elements 21 and 22 appear,
          extra fields of the customer (asked when the report is made) and the fields that must be filled.

A profile is changed in place and its revision number goes up. A report keeps a snapshot of what it used
(name, revision, layout, deviations), and the analysis request in the archive holds the effective values,
including the complete target table. So a report never depends on a profile that changes later.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from spc.core.capability.target import CLASSES, DEFAULT_TARGETS
from spc.core.rules import RuleSet

STAGES = ("machine", "preliminary", "production")
ANALYSIS_KEYS = ("alpha", "estimate_confidence", "target_confidence", "edition", "stability_mode",
                 "stability_confidence", "rules", "targets")
REQUIRABLE = ("process", "machine", "site", "process_ref", "machine_ref", "persons", "period_text", "part_name",
              "part_number", "characteristic", "unit", "target", "technical_conditions", "deviations",
              "sampling_frequency", "recommendations", "uncertainty")
LANGUAGES = ("zh-TW", "en")
MAX_LOGO_BYTES = 150 * 1024
MAX_EXTRA_FIELDS = 12
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
LOGO_RE = re.compile(r"^data:image/(png|jpeg);base64,([A-Za-z0-9+/]+={0,2})$")
MAGIC = {"png": b"\x89PNG\r\n\x1a\n", "jpeg": b"\xff\xd8\xff"}
TEXT_LIMITS = {"organization": 200, "title": 200, "form_no": 100, "revision": 50, "footer": 500}


@dataclass(frozen=True)
class ExtraField:
    key: str
    label_en: str
    label_zh: str
    required: bool = False

    def label(self, lang: str) -> str:
        return (self.label_zh if lang == "zh-TW" else self.label_en) or self.label_en or self.label_zh or self.key


@dataclass(frozen=True)
class ReportTemplate:
    organization: str = ""
    title: str = ""
    form_no: str = ""
    revision: str = ""
    footer: str = ""
    accent: str = ""  # "#rrggbb" or empty for the default blue
    logo: str = ""  # data URI of a PNG or JPEG
    language: str = ""  # default language of the report; empty = the one chosen when it is made
    show_element_21: bool = True
    show_element_22: bool = True
    extra_fields: tuple[ExtraField, ...] = ()
    required: tuple[str, ...] = ()  # names of ReportMeta fields that must not be empty

    def to_dict(self) -> dict:
        return {
            "organization": self.organization, "title": self.title, "form_no": self.form_no, "revision": self.revision,
            "footer": self.footer, "accent": self.accent, "logo": self.logo, "language": self.language,
            "show_element_21": self.show_element_21, "show_element_22": self.show_element_22,
            "extra_fields": [{"key": f.key, "label_en": f.label_en, "label_zh": f.label_zh, "required": f.required}
                             for f in self.extra_fields],
            "required": list(self.required),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ReportTemplate":
        """Check and normalise. Raises ValueError with a message that names the field."""
        data = dict(data or {})
        unknown = set(data) - set(cls().to_dict())
        if unknown:
            raise ValueError(f"unknown report setting(s): {sorted(unknown)}")
        texts = {}
        for key, limit in TEXT_LIMITS.items():
            v = data.get(key, "")
            if not isinstance(v, str) or len(v) > limit:
                raise ValueError(f"report.{key} must be a text of at most {limit} characters")
            texts[key] = v.strip()
        accent = data.get("accent", "") or ""
        if accent and not COLOR_RE.match(accent):
            raise ValueError("report.accent must be a colour like #1f5fbf")
        language = data.get("language", "") or ""
        if language and language not in LANGUAGES:
            raise ValueError(f"report.language must be one of {LANGUAGES}")
        logo = data.get("logo", "") or ""
        if logo:
            check_logo(logo)
        flags = {}
        for key in ("show_element_21", "show_element_22"):
            v = data.get(key, True)
            if not isinstance(v, bool):
                raise ValueError(f"report.{key} must be true or false")
            flags[key] = v
        extra = []
        for item in data.get("extra_fields", []) or []:
            if not isinstance(item, Mapping) or set(item) - {"key", "label_en", "label_zh", "required"}:
                raise ValueError("report.extra_fields entries need key, label_en, label_zh and required")
            key = item.get("key", "")
            if not isinstance(key, str) or not KEY_RE.match(key):
                raise ValueError("an extra field key has 1 to 31 characters: a letter, then letters, digits or _ (lower case)")
            labels = [item.get("label_en", ""), item.get("label_zh", "")]
            if not all(isinstance(x, str) and len(x) <= 100 for x in labels) or not any(x.strip() for x in labels):
                raise ValueError(f"extra field {key!r} needs a label of at most 100 characters")
            extra.append(ExtraField(key, labels[0].strip(), labels[1].strip(), bool(item.get("required", False))))
        if len(extra) > MAX_EXTRA_FIELDS:
            raise ValueError(f"at most {MAX_EXTRA_FIELDS} extra fields")
        if len({f.key for f in extra}) != len(extra):
            raise ValueError("extra field keys must be different")
        required = tuple(data.get("required", []) or [])
        bad = [r for r in required if r not in REQUIRABLE]
        if bad or len(set(required)) != len(required):
            raise ValueError(f"report.required holds unknown or repeated fields: {bad}")
        return cls(accent=accent, logo=logo, language=language, extra_fields=tuple(extra), required=required, **texts, **flags)


def check_logo(uri: str) -> None:
    """Only PNG and JPEG, checked by their first bytes. An SVG can hold script, so it is refused."""
    m = LOGO_RE.match(uri)
    if not m:
        raise ValueError("the logo must be a PNG or JPEG data URI")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("the logo is not valid base64") from None
    if len(raw) > MAX_LOGO_BYTES:
        raise ValueError(f"the logo is larger than {MAX_LOGO_BYTES // 1024} KB")
    if not raw.startswith(MAGIC[m.group(1)]):
        raise ValueError("the logo content does not match its type")


# ---------------------------------------------------------------------------------------- analysis part

def default_target_table() -> dict[str, dict[str, list[float]]]:
    return {s.value: {c: list(v) for c, v in DEFAULT_TARGETS[s].items()} for s in DEFAULT_TARGETS}


def merge_target_table(overrides: Mapping[str, Any] | None) -> dict[str, dict[str, list[float]]]:
    """The complete table: the draft's example values with the customer's cells on top."""
    table = default_target_table()
    for stage, row in (overrides or {}).items():
        if stage not in STAGES or not isinstance(row, Mapping):
            raise ValueError(f"targets: unknown stage {stage!r}")
        for cls, pair in row.items():
            if cls not in CLASSES:
                raise ValueError(f"targets: unknown class {cls!r}")
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in pair)):
                raise ValueError(f"targets.{stage}.{cls} must be [p target, pk target]")
            p, pk = float(pair[0]), float(pair[1])
            if not (0 < pk <= p <= 10):
                raise ValueError(f"targets.{stage}.{cls}: need 0 < pk target <= p target <= 10")
            table[stage][cls] = [p, pk]
    return table


def validate_analysis(data: Mapping[str, Any] | None) -> dict:
    data = dict(data or {})
    unknown = set(data) - set(ANALYSIS_KEYS)
    if unknown:
        raise ValueError(f"unknown analysis setting(s): {sorted(unknown)}")
    out: dict[str, Any] = {}
    for key in ("alpha", "estimate_confidence", "target_confidence", "stability_confidence"):
        if data.get(key) is not None:
            v = data[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v < 1:
                raise ValueError(f"analysis.{key} must be a number between 0 and 1")
            out[key] = float(v)
    if data.get("edition") is not None:
        if data["edition"] not in ("draft", "final"):
            raise ValueError("analysis.edition must be 'draft' or 'final'")
        out["edition"] = data["edition"]
    if data.get("stability_mode") is not None:
        if data["stability_mode"] not in ("strict", "random_range"):
            raise ValueError("analysis.stability_mode must be 'strict' or 'random_range'")
        out["stability_mode"] = data["stability_mode"]
    if data.get("rules") is not None:
        try:
            RuleSet(**data["rules"])
        except TypeError as exc:
            raise ValueError(f"analysis.rules: {exc}") from None
        out["rules"] = dict(data["rules"])
    if data.get("targets") is not None:
        merge_target_table(data["targets"])  # validates
        out["targets"] = {s: {c: [float(x) for x in pair] for c, pair in row.items()} for s, row in data["targets"].items()}
    return out


def table_for_service(table: Mapping[str, Mapping[str, list[float]]] | None):
    """JSON table -> {Stage: {class: (p, pk)}} as `required_targets` wants it. None stays None (defaults)."""
    if table is None:
        return None
    from spc.core.capability.naming import Stage

    return {Stage(s): {c: (float(p), float(pk)) for c, (p, pk) in row.items()} for s, row in table.items()}


def resolve_analysis(explicit: Mapping[str, Any], defaults: Mapping[str, Any], analysis: Mapping[str, Any]):
    """Apply a profile to an analysis request.

    `explicit` holds the fields the caller set on purpose, `defaults` the values of all other fields,
    `analysis` the profile's settings. A field the caller did not set takes the profile's value. A field the
    caller set to something else than the profile keeps the caller's value and is reported as a deviation.
    Returns (values, deviations, target_table or None).
    """
    values = dict(defaults, **explicit)
    deviations: list[str] = []
    for key, wanted in analysis.items():
        if key == "targets":
            continue
        if key in explicit:
            if explicit[key] != wanted:
                deviations.append(key)
        else:
            values[key] = wanted
    table = None
    if "targets" in analysis:
        table = merge_target_table(analysis["targets"])
        if explicit.get("target_table") is not None and explicit["target_table"] != table:
            deviations.append("targets")
            table = explicit["target_table"]
    elif explicit.get("target_table") is not None:
        table = explicit["target_table"]
    return values, deviations, table


def snapshot(profile_id: int, name: str, revision: int, template: ReportTemplate, deviations: list[str]) -> dict:
    """What the archive keeps of the profile that was used."""
    return {"profile_id": profile_id, "name": name, "revision": revision, "report": template.to_dict(), "deviations": sorted(deviations)}


def missing_fields(meta, template: ReportTemplate, extra: Mapping[str, str]) -> list[str]:
    """Required fields that are empty: names of ReportMeta fields, and 'extra:<key>' for extra fields."""
    out = []
    for name in template.required:
        v = getattr(meta, name, None)
        if v is None or (isinstance(v, str) and not v.strip()):
            out.append(name)
    for f in template.extra_fields:
        if f.required and not str(extra.get(f.key, "")).strip():
            out.append(f"extra:{f.key}")
    return out

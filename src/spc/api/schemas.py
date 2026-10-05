"""Request bodies. Field limits reject nonsense early, with a field name in the error."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from spc.core.constants import ALPHA_3SIGMA
from spc.profile import merge_target_table, resolve_analysis
from spc.report import ReportMeta
from spc.service import AnalysisRequest


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuspectsBody(Strict):
    method: Literal["mad", "tukey", "grubbs"] = "mad"
    threshold: float | None = Field(default=None, gt=0)


class MarkBody(Strict):
    """Who marks is not sent: the server takes it from the login."""

    positions: list[int] = Field(min_length=1, max_length=100_000)
    reason: str = Field(max_length=2000)


class RestartBody(MarkBody):
    new_limits: bool = False  # also start a new phase: centre line and limits are calculated again from here


class LoginBody(Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class PasswordBody(Strict):
    current: str = Field(max_length=1024)
    new: str = Field(max_length=1024)


class NewUserBody(Strict):
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024)
    role: Literal["viewer", "engineer", "admin"] = "engineer"
    display_name: str = Field(default="", max_length=200)
    must_change: bool = True


class UpdateUserBody(Strict):
    role: Literal["viewer", "engineer", "admin"] | None = None
    active: bool | None = None
    display_name: str | None = Field(default=None, max_length=200)


class ResetPasswordBody(Strict):
    password: str = Field(max_length=1024)
    must_change: bool = True


class AnalyzeBody(Strict):
    stage: Literal["machine", "preliminary", "production"] = "production"
    chart: Literal["auto", "xbar-s", "xbar-r", "median-r", "imr"] = "auto"
    subgroup_size: int | None = Field(default=None, ge=1, le=1000)
    lsl: float | None = None
    usl: float | None = None
    alpha: float = Field(default=ALPHA_3SIGMA, gt=0, lt=1)
    rules: dict[str, Any] = Field(default_factory=dict)
    stability_mode: Literal["strict", "random_range"] = "random_range"
    stability_confidence: float = Field(default=0.99, gt=0, lt=1)
    model: Literal["A1", "A2", "B", "C1", "C2", "C3", "C4", "D"] | None = None
    controlled_stable: bool = False
    characteristic_class: Literal["critical", "major", "minor", "others"] | None = None
    edition: Literal["draft", "final"] = "draft"
    estimate_confidence: float = Field(default=0.95, gt=0, lt=1)
    target_confidence: float = Field(default=0.9999, gt=0, lt=1)
    incomplete: Literal["error", "drop"] = "drop"
    customer: str | None = Field(default=None, max_length=200)
    distribution: Literal["normal", "auto", "lognormal", "weibull", "gamma", "johnson_su", "box_cox", "mixture", "empirical"] = "normal"
    method: Literal["G", "Z"] = "G"
    bootstrap_n: int = Field(default=200, ge=0, le=2000)
    seed: int = Field(default=20260701, ge=0, le=2**32 - 1)
    moving_n: int = Field(default=1, ge=1, le=10)
    profile_id: int | None = Field(default=None, ge=1)  # customer profile: fills what is not set here, see spc.profile
    target_table: dict | None = None

    def resolve(self, profile: dict | None = None) -> tuple[AnalysisRequest, list[str]]:
        """The effective request and the names of the fields that differ from the profile on purpose.
        Fields not set in the body take the profile's value. A field set to another value is a deviation."""
        data = self.model_dump(exclude={"profile_id"})
        explicit = {k: v for k, v in data.items() if k in self.model_fields_set}
        values, deviations, table = resolve_analysis(explicit, data, profile["analysis"] if profile else {})
        if profile and "customer" not in explicit:
            values["customer"] = profile["name"]
        if table is None and values.get("target_table") is not None:
            table = merge_target_table(values["target_table"])
        values["target_table"] = table
        return AnalysisRequest(**values), deviations

    def to_request(self) -> AnalysisRequest:
        return self.resolve(None)[0]


class TargetBody(Strict):
    stage: Literal["machine", "preliminary", "production"]
    characteristic_class: Literal["critical", "major", "minor", "others"]
    n: int = Field(ge=2, le=1_000_000)
    confidence: float = Field(default=0.9999, gt=0, lt=1)
    edition: Literal["draft", "final"] = "draft"


class ArlBody(Strict):
    shift: float = Field(gt=0, le=10)
    n: int = Field(ge=1, le=1000)
    alpha: float = Field(default=ALPHA_3SIGMA, gt=0, lt=1)
    max_arl: float | None = Field(default=None, ge=1)


class AttributeBody(Strict):
    kind: Literal["p", "np", "c", "u"]
    counts: list[float] = Field(min_length=2, max_length=100_000)
    sizes: list[float] | float | None = None
    alpha: float = Field(default=ALPHA_3SIGMA, gt=0, lt=1)


class ReportMetaBody(Strict):
    process: str = Field(default="", max_length=2000)
    machine: str = Field(default="", max_length=2000)
    site: str = Field(default="", max_length=2000)
    process_ref: str = Field(default="", max_length=2000)
    machine_ref: str = Field(default="", max_length=2000)
    persons: str = Field(default="", max_length=2000)
    period_text: str = Field(default="", max_length=2000)
    part_name: str = Field(default="", max_length=2000)
    part_number: str = Field(default="", max_length=2000)
    characteristic: str = Field(default="", max_length=2000)
    unit: str = Field(default="", max_length=2000)
    target: float | None = None
    technical_conditions: str = Field(default="", max_length=2000)
    deviations: str = Field(default="", max_length=2000)
    sampling_frequency: str = Field(default="", max_length=2000)
    recommendations: str = Field(default="", max_length=2000)
    uncertainty: float | None = Field(default=None, gt=0)
    coverage_factor: float = Field(default=2.0, ge=1)
    guard_band_risk: float = Field(default=0.05, gt=0, lt=0.5)
    extra: dict[str, str] = Field(default_factory=dict, max_length=12)  # values of the customer profile's extra fields

    def to_meta(self) -> ReportMeta:
        return ReportMeta(**self.model_dump())


class ReportBody(Strict):
    analysis: AnalyzeBody
    meta: ReportMetaBody = Field(default_factory=ReportMetaBody)
    language: Literal["zh-TW", "en"] = "en"  # not set: the profile's language, else English
    profile_id: int | None = Field(default=None, ge=1)  # also applies to the analysis unless that names its own


class ProfileBody(Strict):
    """Name, analysis settings and report layout of a customer. The parts are checked in spc.profile."""

    name: str = Field(min_length=1, max_length=100)
    analysis: dict = Field(default_factory=dict)
    report: dict = Field(default_factory=dict)


class MonitorCreateBody(Strict):
    """config: see spc.monitor.model.validate_config. source: {type: dataset|parameters, ...} for the first limits."""

    config: dict
    source: dict


class MonitorConfigBody(Strict):
    config: dict


class LimitsBody(Strict):
    source: dict
    reason: str = Field(max_length=2000)


class PointBody(Strict):
    values: list[float] = Field(min_length=1, max_length=100)
    part: str | None = Field(default=None, max_length=40)  # the product of a short-run (Z-MR) monitor
    label: str = Field(default="", max_length=100)
    tags: dict[str, str] = Field(default_factory=dict, max_length=6)
    taken_at: str | None = Field(default=None, max_length=40)


class ReasonBody(Strict):
    reason: str = Field(max_length=2000)


class EventBody(Strict):
    kind: Literal["ack", "action", "observation", "escalation"]
    step: str = Field(default="", max_length=40)
    text: str = Field(default="", max_length=2000)


class CloseBody(Strict):
    outcome: Literal["recovered", "invalid_sample", "escalated"]
    text: str = Field(default="", max_length=2000)


class OngoingReportBody(Strict):
    window: int = Field(default=125, ge=10, le=2000)
    language: Literal["zh-TW", "en"] = "en"
    meta: ReportMetaBody = Field(default_factory=ReportMetaBody)

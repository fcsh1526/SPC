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


class StateTestsBody(Strict):
    """Tests for a process with several states (ISO 22514-8)."""

    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    by: str | None = Field(default=None, max_length=100)  # a tag column; without it the subgroup labels are the states


class MultistateBody(Strict):
    """Machine performance of a multi-state process, ISO 22514-8 clause 7. None for widths_equal and locations_equal: the tests decide."""

    lsl: float
    usl: float
    by: str | None = Field(default=None, max_length=100)
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    resolution: float | None = Field(default=None, gt=0)
    location: Literal["mean", "median"] = "mean"
    widths_equal: bool | None = None
    locations_equal: bool | None = None
    delta_m_variable: bool = False
    delta_m_star: float | None = Field(default=None, gt=0)
    outlier_physical: bool = False
    outlier_direction: Literal["negative", "positive", "both"] = "both"


class MultistageScopeBody(Strict):
    """Scope of inspection of a multi-stage machine, draft 8.5.1.2."""

    components_per_carrier: int = Field(default=1, ge=1, le=1000)
    carriers: int = Field(default=1, ge=1, le=1000)
    spindles: int = Field(default=1, ge=1, le=1000)
    machines: int = Field(default=1, ge=1, le=1000)
    geometrically_identical: int = Field(default=0, ge=0, le=100_000)
    measured_carriers: int | None = Field(default=None, ge=1, le=1000)
    per_combination: int = Field(default=5, ge=1, le=1000)
    minimum_total: int = Field(default=50, ge=1, le=100_000)


class MultistageBody(Strict):
    """The combinations of a stored data set: the factors are tag names or "subgroup"."""

    factors: list[str] = Field(min_length=1, max_length=6)
    lsl: float | None = None
    usl: float | None = None
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    per_combination: int = Field(default=5, ge=1, le=1000)
    minimum_total: int = Field(default=50, ge=1, le=100_000)


class CavityBody(Strict):
    """Equipment with several cavities, stations or clamping devices (draft 8.2.6): the factor is a tag name or "subgroup"."""

    factor: str = Field(max_length=100)
    lsl: float | None = None
    usl: float | None = None
    alpha: float = Field(default=0.05, gt=0, lt=0.5)


class DoeBody(Strict):
    """Process characterization (draft 6.4): the response of each run and one column per factor."""

    y: list[float] = Field(min_length=4, max_length=5000)
    factors: dict[str, list[float | str]] = Field(min_length=1, max_length=12)
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    max_order: int | None = Field(default=None, ge=1, le=12)


class DoeDesignBody(Strict):
    """A plan of an experiment (draft 6.4): the factors are the letters A, B, ... and the levels are coded."""

    kind: Literal["full", "fractional", "central_composite", "box_behnken"]
    k: int = Field(ge=2, le=8)
    p: int | None = Field(default=None, ge=0, le=6)
    generators: list[str] | None = Field(default=None, max_length=6)
    axial: Literal["rotatable", "face", "spherical"] = "rotatable"
    center: int | None = Field(default=None, ge=1, le=30)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)


class DoeLimitBody(Strict):
    """A linear limit on coded factors: sum(coefficient_i * x_i) <= (or >=) limit."""

    coefficients: list[float] = Field(min_length=2, max_length=8)
    op: Literal["<=", ">="] = "<="
    limit: float


class DoeOptimalBody(Strict):
    """A D-optimal design of n runs for a model, chosen from the allowed points of coded factors."""

    k: int = Field(ge=2, le=8)
    model: Literal["main", "interaction", "quadratic"] = "main"
    n_runs: int | None = Field(default=None, ge=2, le=200)
    levels: Literal[2, 3, 5] | None = None
    constraints: list[DoeLimitBody] = Field(default_factory=list, max_length=10)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 2)
    starts: int = Field(default=30, ge=1, le=100)


class MixtureDesignBody(Strict):
    """A plan for a mixture experiment: components are proportions that add up to 1."""

    kind: Literal["lattice", "centroid", "d_optimal"]
    q: int = Field(ge=2, le=8)
    degree: int = Field(default=2, ge=1, le=6)
    model: Literal["linear", "quadratic", "special_cubic"] = "quadratic"
    n_runs: int | None = Field(default=None, ge=2, le=200)
    lower: list[float] | None = Field(default=None, max_length=8)
    upper: list[float] | None = Field(default=None, max_length=8)
    center: int = Field(default=0, ge=0, le=30)
    axial: bool = False
    m: int | None = Field(default=None, ge=2, le=40)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 2)
    starts: int = Field(default=30, ge=1, le=100)


class MixtureBody(Strict):
    y: list[float] = Field(min_length=4, max_length=5000)
    components: dict[str, list[float]] = Field(min_length=2, max_length=8)
    model: Literal["linear", "quadratic", "special_cubic"] = "quadratic"
    alpha: float = Field(default=0.05, gt=0, lt=0.5)


class AnchorBody(Strict):
    entries: int = Field(ge=0, le=10**9)
    last_hash: str = Field(pattern="^[0-9a-f]{64}$")


class RandomPlanBody(Strict):
    """A random sampling plan (draft 9.2): subgroups spread over the period, the levels of each factor dealt out evenly."""

    subgroups: int = Field(ge=1, le=1000)
    size: int = Field(ge=1, le=1000)
    factors: dict[str, list[str]] = Field(default_factory=dict, max_length=6)
    seed: int | None = Field(default=None, ge=0, lt=2**32)
    position: Literal["random", "middle"] = "random"


class CoverageBody(Strict):
    factors: list[str] | None = Field(default=None, max_length=20)
    expected: dict[str, list[str]] | None = None
    thin_share: float = Field(default=0.5, gt=0, le=1)


class MultivariateBody(Strict):
    """Pm and Pmk of a multidimensional characteristic (draft 8.5.2): one row per part, one column per characteristic."""

    data: list[list[float]] = Field(min_length=10, max_length=100_000)
    lower: list[float] = Field(min_length=2, max_length=20)
    upper: list[float] = Field(min_length=2, max_length=20)
    names: list[str] | None = Field(default=None, max_length=20)


class NestedBody(Strict):
    """Nested variance components of a stored data set: tag names or "subgroup", outermost level first."""

    levels: list[str] = Field(min_length=1, max_length=5)
    alpha: float = Field(default=0.05, gt=0, lt=0.5)


class TrendBody(Strict):
    """Regression control chart of a stored data set. `cycle` counts samples between dressings or tool changes."""

    cycle: int | None = Field(default=None, ge=3, le=100_000)
    subgroup_size: int | None = Field(default=None, ge=2, le=25)
    lsl: float | None = None
    usl: float | None = None
    distribution: str = Field(default="auto", max_length=30)
    method: Literal["G", "Z"] = "G"


class SpecialChartBody(Strict):
    """Charts of figure 10-5 beyond the plain Shewhart charts, for one series: counts and sizes (Laney, standardised), event gaps (G, T), values (percentile,
    UWMA), values with products and targets (delta to target) or with streams (Levey-Jennings)."""

    kind: Literal["laney-p", "laney-u", "z-p", "z-u", "g", "t", "percentile", "uwma", "delta-target", "levey-jennings", "box-cox", "johnson"]
    counts: list[float] | None = Field(default=None, max_length=100_000)
    sizes: list[float] | None = Field(default=None, max_length=100_000)
    values: list[float] | None = Field(default=None, max_length=100_000)
    labels: list[str] | None = Field(default=None, max_length=100_000)  # the product or the stream of each value
    targets: dict[str, float] | None = None
    span: int | None = Field(default=None, ge=2, le=50)
    alpha: float = Field(default=ALPHA_3SIGMA, gt=0, lt=0.5)
    reference_n: int | None = Field(default=None, ge=2, le=100_000)


class ChartGuideBody(Strict):
    """The answers given so far in the control chart selection guide (draft figure 10-5), in order."""

    answers: list[str] = Field(default_factory=list, max_length=6)


class GdtBody(Strict):
    """Assembly clearance of a size feature with a position tolerance under MMR/LMR, draft 8.5.3. Give xp, or dx and dy."""

    xd: list[float] = Field(min_length=5, max_length=100_000)
    xp: list[float] | None = Field(default=None, max_length=100_000)
    dx: list[float] | None = Field(default=None, max_length=100_000)
    dy: list[float] | None = Field(default=None, max_length=100_000)
    kind: Literal["bore", "pin"] = "bore"
    requirement: Literal["mmc", "lmc"] = "mmc"
    lower: float
    upper: float
    position_tolerance: float = Field(gt=0)
    distribution: str = Field(default="auto", max_length=30)
    method: Literal["G", "Z"] = "G"
    bootstrap_n: int = Field(default=200, ge=0, le=2000)
    confidence: float = Field(default=0.95, gt=0.5, lt=1)
    seed: int = 20260701


class ReportMultistateBody(Strict):
    """The machine performance of the states for a report. The limits default to those of the analysis."""

    lsl: float | None = None
    usl: float | None = None
    by: str | None = Field(default=None, max_length=100)
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    resolution: float | None = Field(default=None, gt=0)
    location: Literal["mean", "median"] = "mean"
    widths_equal: bool | None = None
    locations_equal: bool | None = None
    delta_m_variable: bool = False
    delta_m_star: float | None = Field(default=None, gt=0)
    outlier_physical: bool = False
    outlier_direction: Literal["negative", "positive", "both"] = "both"


class TimeModelBody(Strict):
    """Optional help for the suggestion of the time-dependent model."""

    subgroup_size: int | None = Field(default=None, ge=2, le=25)  # only for data without subgroups: the size of the blocks
    hints: dict[str, bool] = Field(default_factory=dict, max_length=10)


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
    distribution: Literal["normal", "auto", "lognormal", "weibull", "gamma", "johnson_su", "box_cox", "mixture", "empirical", "weibull2", "rayleigh", "folded_normal"] = "normal"
    method: Literal["G", "Z"] = "G"
    bootstrap_n: int = Field(default=200, ge=0, le=2000)
    seed: int = Field(default=20260701, ge=0, le=2**32 - 1)
    moving_n: int = Field(default=1, ge=1, le=10)
    limit_method: Literal["draft", "iso7870"] = "draft"  # draft: exact limits of the draft; iso7870: the factors of ISO 7870-2
    fit_check: bool = False  # draft 9.4: correlation of the probability plot, overall and in the 25 % nearest to the limit
    pmk_excluded: str = Field(default="", max_length=500)  # draft 8.2.4: the agreement with the customer to leave Pmk out (machine studies)
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
    max_parts: int | None = Field(default=None, ge=1, le=10_000_000)  # parts that may be made after the shift before it is found (sampling interval, draft 10.4)
    parts_per_hour: float | None = Field(default=None, gt=0, le=10_000_000)


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


class ReportSpecialBody(Strict):
    """Special-case results for annex E of a report; each is the request of its own route. The result is made again on the data of the report."""

    scope: MultistageScopeBody | None = None
    multistage: MultistageBody | None = None
    nested: NestedBody | None = None
    trend: TrendBody | None = None
    gdt: GdtBody | None = None
    multivariate: MultivariateBody | None = None
    cavities: CavityBody | None = None


class ReportBody(Strict):
    analysis: AnalyzeBody
    meta: ReportMetaBody = Field(default_factory=ReportMetaBody)
    language: Literal["zh-TW", "en"] = "en"  # not set: the profile's language, else English
    profile_id: int | None = Field(default=None, ge=1)  # also applies to the analysis unless that names its own
    multistate: ReportMultistateBody | None = None  # the machine performance of the states (ISO 22514-8) goes into the report and the archive
    special: ReportSpecialBody | None = None  # annex E: multi-stage, nested, trend, GD&T, multivariate results
    control_plan_id: int | None = Field(default=None, ge=1)  # a released plan: its snapshot goes into the report and the archive
    measurement_system_id: int | None = Field(default=None, ge=1)  # the MSA gate applies, and U and the guard band come from its studies
    machine_study_id: int | None = Field(default=None, ge=1)  # draft 9.3: the machine performance study of the equipment must be closed; it is named in the report


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
    cycle: str | None = Field(default=None, max_length=200)  # trend monitors: this sample starts a new cycle (the tool was changed ...); the note says what happened


class ReasonBody(Strict):
    reason: str = Field(max_length=2000)


class ImprovementBody(Strict):
    """An improvement cycle (checked in spc.improvement.service.validate_record)."""

    record: dict


class ImportTemplateBody(Strict):
    """An import template (checked in spc.data.templates.validate_template)."""

    record: dict


class ImprovementTextBody(Strict):
    text: str = Field(max_length=2000)


class LotBody(Strict):
    """A lot: the quantity, and the monitor with the range of points that stands for its production (checked in spc.disposition.service.validate_record)."""

    record: dict


class LotDecisionBody(Strict):
    decision: Literal["release", "concession", "sort", "rework", "scrap"]
    reason: str = Field(default="", max_length=2000)
    customer_ref: str = Field(default="", max_length=300)
    sorted: dict | None = None


class ValidationCaseBody(Strict):
    """A reference case of the user; checked in spc.validation.custom."""

    record: dict


class EquipmentBody(Strict):
    """An OPC UA link; checked in spc.equipment.model."""

    record: dict


class SignatureBody(Strict):
    """A signature made outside, with the certificate or public key (PEM) that checks it."""

    signature: str = Field(max_length=4096)
    key: str = Field(max_length=20000)
    scheme: str | None = None
    note: str = Field(default="", max_length=300)


class SignerBody(Strict):
    name: str = Field(max_length=80)
    key: str = Field(max_length=20000)


class MsaBody(Strict):
    """A measurement system: name, resolution, tolerance and policy; checked in spc.msa.service.validate_record."""

    record: dict


class LinearityMonitorBody(Strict):
    """Readings of standards measured after the linearity study (ISO 22514-7, 11.2): one reference value and its readings per standard."""

    references: list[float] = Field(min_length=2, max_length=20)
    readings: list[list[float]] = Field(min_length=2, max_length=20)
    epsilon: float = Field(default=0.05, gt=0, lt=0.5)


class GpcBody(Strict):
    """Gage performance curve of a variable system (AIAG MSA, chapter IV F)."""

    lsl: float | None = None
    usl: float | None = None
    bias: float = 0.0
    sigma: float = Field(gt=0)
    reference_values: list[float] = Field(default_factory=list, max_length=200)
    points: int = Field(default=121, ge=11, le=2001)


class MultipleReadingsBody(Strict):
    current: float = Field(gt=0)
    target: float = Field(gt=0)


class ClassificationBody(Strict):
    u_mp: float = Field(ge=0)
    class_width: float = Field(gt=0)


class CpImpactBody(Strict):
    cp: float = Field(gt=0)
    grr: float = Field(ge=0, description="a fraction: 0.3 is 30 %")
    basis: Literal["process", "tolerance"] = "process"
    given: Literal["observed", "actual"] = "observed"


class PvCorrectionBody(Strict):
    range_of_part_averages: float = Field(gt=0)
    n_parts: int = Field(ge=2, le=20)
    ev: float = Field(ge=0)
    appraisers: int = Field(ge=1, le=20)
    trials: int = Field(ge=1, le=50)


class MsaStudyBody(Strict):
    kind: str = Field(max_length=20)
    date: str = Field(max_length=10)
    note: str = Field(default="", max_length=2000)
    input: dict


class PlanBody(Strict):
    """The record of a control plan; checked in spc.plan.model.validate_record."""

    record: dict


class PlanApproveBody(Strict):
    role: str = Field(max_length=40)
    note: str = Field(default="", max_length=2000)


class PeopleRolesBody(Strict):
    roles: list[str] = Field(max_length=7)


class CompetenceBody(Strict):
    competence: str = Field(max_length=40)
    level: int = Field(ge=0, le=2)
    date: str = Field(max_length=10)
    note: str = Field(default="", max_length=1000)


class StudyBody(Strict):
    """The descriptive part of a machine performance study; checked in spc.study.checklist.validate_record."""

    record: dict


class StudyItemBody(Strict):
    status: str = Field(max_length=20)
    note: str = Field(default="", max_length=2000)


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

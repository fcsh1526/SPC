"""What the author types into the report. Everything else comes from the data and the analysis."""

from __future__ import annotations

from dataclasses import dataclass, field, fields

TEXT_LIMIT = 2000


@dataclass(frozen=True)
class ReportMeta:
    process: str = ""  # element 1
    machine: str = ""
    site: str = ""
    process_ref: str = ""  # element 2
    machine_ref: str = ""
    persons: str = ""  # element 3
    period_text: str = ""  # element 4, used when the data has no timestamps
    part_name: str = ""  # element 5
    part_number: str = ""
    characteristic: str = ""  # element 6
    unit: str = ""
    target: float | None = None  # target value Tm of the characteristic
    technical_conditions: str = ""  # element 7
    deviations: str = ""  # element 8
    sampling_frequency: str = ""  # element 10, used when the data has no timestamps
    recommendations: str = ""  # element 20: actions and notes of the author
    uncertainty: float | None = None  # element 22: expanded measurement uncertainty U
    coverage_factor: float = 2.0
    guard_band_risk: float = 0.05
    extra: dict = field(default_factory=dict)  # values of the customer's extra fields: key -> text

    def validate(self) -> "ReportMeta":
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, str) and len(v) > TEXT_LIMIT:
                raise ValueError(f"{f.name} is longer than {TEXT_LIMIT} characters")
        if not isinstance(self.extra, dict) or len(self.extra) > 12:
            raise ValueError("extra must hold at most 12 entries")
        for k, v in self.extra.items():
            if not isinstance(k, str) or not isinstance(v, str) or len(v) > TEXT_LIMIT:
                raise ValueError(f"extra field {k!r} must be a text of at most {TEXT_LIMIT} characters")
        if self.uncertainty is not None and not self.uncertainty > 0:
            raise ValueError("uncertainty must be positive")
        if not self.coverage_factor >= 1:
            raise ValueError("coverage_factor must be at least 1")
        if not 0 < self.guard_band_risk < 0.5:
            raise ValueError("guard_band_risk must be between 0 and 0.5")
        return self

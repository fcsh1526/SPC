"""What the author types into the report. Everything else comes from the data and the analysis."""

from __future__ import annotations

from dataclasses import dataclass, fields

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

    def validate(self) -> "ReportMeta":
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, str) and len(v) > TEXT_LIMIT:
                raise ValueError(f"{f.name} is longer than {TEXT_LIMIT} characters")
        if self.uncertainty is not None and not self.uncertainty > 0:
            raise ValueError("uncertainty must be positive")
        if not self.coverage_factor >= 1:
            raise ValueError("coverage_factor must be at least 1")
        if not 0 < self.guard_band_risk < 0.5:
            raise ValueError("guard_band_risk must be between 0 and 0.5")
        return self

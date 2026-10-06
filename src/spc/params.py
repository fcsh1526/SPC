"""All analysis parameters in one record.

The manual asks for parameter transparency (chapter 11) and for archiving the parameters
with the results (chapter 13). Store this record next to every analysis result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from spc import __version__
from spc.core.constants import ALPHA_3SIGMA
from spc.core.rules import RuleSet

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AnalysisParams:
    alpha: float = ALPHA_3SIGMA
    estimate_confidence: float = 0.95
    target_confidence: float = 0.9999
    edition: str = "draft"
    rules: RuleSet = field(default_factory=RuleSet)
    stability_mode: str = "random_range"
    stability_confidence: float = 0.99
    customer: str | None = None
    limit_method: str = "draft"  # "iso7870": the factors of ISO 7870-2 instead of the exact limits of the draft

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.limit_method == "draft":  # the default is not written: results and archives made before the setting existed stay reproducible
            del data["limit_method"]
        data["schema_version"] = SCHEMA_VERSION
        data["engine_version"] = __version__
        return data

    def fingerprint(self) -> str:
        """Stable hash of the parameters. Two runs with equal parameters give equal fingerprints."""
        text = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()[:16]

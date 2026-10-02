"""Notes that the core attaches to results: a stable code plus parameters.

A user interface translates the code. `str(note)` gives an English sentence for logs and tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

_TEXT = {
    "subgroup_not_contiguous": "subgroup {label!r} is not contiguous in the file",
    "time_not_ordered": "timestamps are not in time order; subgroup order follows the file order",
    "rows_skipped": "{count} row(s) with an empty value were skipped",
    "small_sample": "sample size at or below {limit}: small changes may stay unseen",
    "size_varies": "sample size varies by 25 % or more: limits change point by point",
}


@dataclass(frozen=True)
class Note:
    code: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        template = _TEXT.get(self.code)
        return template.format(**self.params) if template else self.code

    def to_dict(self) -> dict:
        return {"code": self.code, "params": dict(self.params)}

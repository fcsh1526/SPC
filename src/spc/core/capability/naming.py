"""Index naming gate (AIAG-VDA SPC draft, Table 7-1 and 7-4).

The index name states the boundary conditions, not a different formula:

* Machine study            : Pm / Pmk. Stability is not tested.
* Preliminary process study: Pp / Ppk. Stability cannot be proven yet.
* Production study         : Cp / Cpk only when stability is proven. Otherwise Pp / Ppk.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from spc.core.stability import Stability


class Stage(str, Enum):
    MACHINE = "machine"
    PRELIMINARY = "preliminary"
    PRODUCTION = "production"


@dataclass(frozen=True)
class IndexNames:
    p: str
    pk: str
    reason: str


def index_names(stage: Stage, stability: Stability = Stability.UNKNOWN) -> IndexNames:
    stage = Stage(stage)
    if stage is Stage.MACHINE:
        return IndexNames("Pm", "Pmk", "machine performance study: stability is not tested")
    if stage is Stage.PRELIMINARY:
        return IndexNames("Pp", "Ppk", "preliminary study: stability cannot be proven yet")
    if stability in (Stability.STATISTICAL_CONTROL, Stability.IN_CONTROL):
        return IndexNames("Cp", "Cpk", f"stability proven ({stability.value})")
    return IndexNames("Pp", "Ppk", f"stability not proven ({Stability(stability).value})")

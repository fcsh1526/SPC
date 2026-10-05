"""The seven roles in SPC and their competencies (draft 6.8.1 and table 6-1).

Level of a competence: 1 = basic, 2 = expert; None = not required for the role. The matrix is table 6-1 of the draft as printed.
The roles are not the login roles of the software (viewer, operator, engineer, admin): a person holds one or several SPC roles
for the work they do, and the login role only decides what the program lets them change.
"""

from __future__ import annotations

from typing import Mapping

ROLES = ("quality_planning", "product_developer", "process_planner", "inspection_planner", "equipment_procurer", "line_supervisor", "line_operator")

COMPETENCES = ("qms", "statistics", "machine_performance", "process_performance", "critical_parameters", "define_charts", "use_charts")

# Table 6-1: rows are the competences, in the order of COMPETENCES; the columns of this table follow ROLES
_TABLE = {
    #                         quality_planning product_dev process_planner inspection equipment supervisor operator
    "qms":                    (2, 1, 1, 1, 1, 1, 1),
    "statistics":             (2, 1, 1, 1, 1, None, 1),
    "machine_performance":    (2, None, 1, None, 2, None, None),
    "process_performance":    (2, 1, 2, None, 1, None, None),
    "critical_parameters":    (1, None, 2, 1, 2, None, None),
    "define_charts":          (2, None, 2, 2, None, None, None),
    "use_charts":             (1, None, 1, None, None, 2, 1),
}

MATRIX: dict[str, dict[str, int | None]] = {role: {c: _TABLE[c][i] for c in COMPETENCES} for i, role in enumerate(ROLES)}

# The roles whose coordination the draft asks for (6.8.1: "attune tolerances, inherent manufacturing process variation and
# measurement uncertainty"), and the owner of the SPC process: they approve a control plan.
APPROVERS = ("quality_planning", "product_developer", "process_planner", "inspection_planner")

# What the login role allows, as a suggestion for who gets which login role. Nothing here is enforced.
LOGIN_SUGGESTION = {"quality_planning": "engineer", "product_developer": "engineer", "process_planner": "engineer", "inspection_planner": "engineer",
                    "equipment_procurer": "viewer", "line_supervisor": "operator", "line_operator": "operator"}

LEVELS = (0, 1, 2)  # 0 = no competence recorded or none


def requirements(role: str) -> dict[str, int]:
    return {c: lvl for c, lvl in MATRIX[role].items() if lvl}


def qualification(roles: list[str], competences: Mapping[str, Mapping]) -> dict[str, dict]:
    """For each role of a person: the gaps between table 6-1 and the recorded levels, and whether the person is qualified."""
    out = {}
    for role in roles:
        gaps = []
        for comp, need in requirements(role).items():
            have = int((competences.get(comp) or {}).get("level", 0))
            if have < need:
                gaps.append({"competence": comp, "required": need, "achieved": have})
        out[role] = {"qualified": not gaps, "gaps": gaps}
    return out

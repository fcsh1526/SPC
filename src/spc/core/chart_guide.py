"""The control chart selection guide (AIAG-VDA SPC draft figure 10-5, with the text of 10.3.2 and 10.3.4) as a decision tree.

The tree follows the figure: attribute data (defectives: p, np; defects: c, u), variable data by the size of the rational subgroup (5 and more: X-bar and s;
2 to 4: X-bar and R; 1 and a homogeneous process: I-MR) and, when Shewhart charts do not work, the special situations of the box at the bottom of the figure.
The figure also names charts that this program does not have; the result says so (`support`), it never hides them:
    full     the chart is available as a monitor (`kinds`) and/or in the tools
    partial  something close is available; `note` says what
    none     not available in this program

A step is asked with `next_step(answers)`: `answers` is the list of values chosen so far. Texts are in the web files (i18n keys guide.*).
"""

from __future__ import annotations

from typing import Any

# chart id -> section of the draft, monitor kinds, support, where else the program has it
CHARTS: dict[str, dict[str, Any]] = {
    "xbar_s": {"ref": "10.3.3.2", "kinds": ["xbar-s"], "support": "full", "analysis": True},
    "xbar_r": {"ref": "10.3.3.3", "kinds": ["xbar-r"], "support": "full", "analysis": True},
    "imr": {"ref": "10.3.3.5", "kinds": ["imr"], "support": "full", "analysis": True},
    "median": {"ref": "10.3.3.4", "kinds": ["median-r"], "support": "full", "analysis": True},
    "p": {"ref": "10.3.6.2", "kinds": ["p"], "support": "full", "tool": "attribute"},
    "np": {"ref": "10.3.6.3", "kinds": ["np"], "support": "full", "tool": "attribute"},
    "c": {"ref": "10.3.6.5", "kinds": ["c"], "support": "full", "tool": "attribute"},
    "u": {"ref": "10.3.6.4", "kinds": ["u"], "support": "full", "tool": "attribute"},
    "laney": {"ref": "10.3.2 (figure 10-5)", "kinds": [], "support": "none"},  # Laney p' and u' charts for over- and underdispersion
    "pre_control": {"ref": "10.3.2.7", "kinds": ["pre"], "support": "full"},
    "preliminary_acceptance": {"ref": "10.3.2.7", "kinds": ["acc-xbar", "acc-median", "acc-x"], "support": "full"},
    "acceptance": {"ref": "10.3.4", "kinds": ["acc-xbar", "acc-median", "acc-x"], "support": "full"},
    "shewhart_transformed": {"ref": "10.3.2.6", "kinds": [], "support": "partial"},  # transformations exist for capability indices, not for the charts
    "pearson": {"ref": "10.3.5.2", "kinds": ["pearson"], "support": "full"},
    "extended": {"ref": "10.3.5.3", "kinds": ["ext-xbar"], "support": "full"},
    "percentile": {"ref": "10.3.2.6 (figure 10-5)", "kinds": [], "support": "none"},
    "levey_jennings": {"ref": "10.3.2.1 (figure 10-5)", "kinds": ["multistream"], "support": "partial"},  # the program charts the mean over the streams and the worst stream
    "cusum": {"ref": "10.3.5.4", "kinds": ["cusum"], "support": "full"},
    "ewma": {"ref": "10.3.5.5", "kinds": ["ewma"], "support": "full"},
    "uwma": {"ref": "10.3.2.5 (figure 10-5)", "kinds": ["ewma"], "support": "partial"},  # the EWMA chart is there, the uniformly weighted one is not
    "ar_residual": {"ref": "10.3.2.6", "kinds": ["ar"], "support": "full"},
    "regression": {"ref": "10.3.2 (figure 10-5)", "kinds": [], "support": "partial", "tool": "trend"},  # the regression control chart of the Special cases tab is an analysis tool
    "zmr": {"ref": "10.3.2.9", "kinds": ["zmr"], "support": "full"},
    "delta_target": {"ref": "10.3.2.9 (figure 10-5)", "kinds": ["zmr"], "support": "partial"},  # Z-MR standardises with target and standard deviation; plain delta to target is not offered
    "hotelling": {"ref": "10.3.2.8", "kinds": ["t2", "mewma", "mcusum"], "support": "full"},
    "g_chart": {"ref": "10.3.2 (figure 10-5)", "kinds": [], "support": "none"},
    "t_chart": {"ref": "10.3.2 (figure 10-5)", "kinds": [], "support": "none"},
}

# step id -> question key and options {value: next step id, or a result: list of chart ids and notes}
TREE: dict[str, dict[str, Any]] = {
    "start": {"options": {"attribute": "attribute_kind", "variable": "subgroup"}},
    "attribute_kind": {"options": {"defectives": "defectives_constant", "defects": "defects_constant"}},
    "defectives_constant": {"options": {
        "yes": {"charts": ["np", "p"], "notes": ["overdispersion"]},
        "no": {"charts": ["p"], "notes": ["overdispersion"]},
    }},
    "defects_constant": {"options": {
        "yes": {"charts": ["c", "u"], "notes": ["overdispersion"]},
        "no": {"charts": ["u"], "notes": ["overdispersion"]},
    }},
    "subgroup": {"options": {
        "five_or_more": {"charts": ["xbar_s"], "notes": ["xbar_s_first"]},
        "two_to_four": {"charts": ["xbar_r"], "notes": []},
        "one": "homogeneous",
    }},
    "homogeneous": {"options": {"yes": {"charts": ["imr"], "notes": ["imr_weak"]}, "no": "special"}},
    "special": {"options": {  # "when Shewhart charts do not work"
        "start_up": {"charts": ["pre_control", "preliminary_acceptance"], "notes": ["preliminary_only"]},
        "tolerance_related": {"charts": ["acceptance"], "notes": ["acceptance_limits"]},
        "non_normal": {"charts": ["shewhart_transformed", "median", "pre_control", "pearson", "extended", "percentile"], "notes": ["non_normal_pearson", "non_normal_extended"]},
        "batch": {"charts": ["levey_jennings", "extended"], "notes": ["batch_sov"]},
        "autocorrelation": {"charts": ["cusum", "uwma", "regression", "ar_residual"], "notes": ["autocorrelation_ar"]},
        "small_shift": {"charts": ["cusum", "ewma"], "notes": ["small_shift"]},
        "short_run": {"charts": ["zmr", "delta_target"], "notes": []},
        "multivariate": {"charts": ["hotelling"], "notes": ["multivariate_cause"]},
        "rare_events": {"charts": ["g_chart", "t_chart"], "notes": []},
    }},
}


def _check_tree() -> None:
    for step, spec in TREE.items():
        for value, target in spec["options"].items():
            if isinstance(target, str):
                assert target in TREE, (step, value)
            else:
                assert all(c in CHARTS for c in target["charts"]), (step, value)


_check_tree()


def next_step(answers: list[str]) -> dict[str, Any]:
    """Walk the tree with the answers given so far. Returns {"step", "options"} for the next question or {"result"} when the answers lead to charts."""
    step = "start"
    for i, value in enumerate(answers):
        options = TREE[step]["options"]
        if value not in options:
            raise ValueError(f"answer {i + 1} ({value!r}) is not one of {sorted(options)} for the step {step!r}")
        target = options[value]
        if not isinstance(target, str):
            if i != len(answers) - 1:
                raise ValueError("there are more answers than questions")
            return {"answers": list(answers), "result": {"charts": [{"chart": c, **CHARTS[c]} for c in target["charts"]], "notes": list(target["notes"])}}
        step = target
    return {"answers": list(answers), "step": step, "options": list(TREE[step]["options"])}

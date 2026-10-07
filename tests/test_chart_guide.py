"""The control chart selection guide of draft figure 10-5."""

import itertools
import json
from pathlib import Path

import pytest

from spc.core import chart_guide as g
from tests.conftest import logged_in_client, make_app

ROOT = Path(__file__).resolve().parent.parent / "src" / "spc"


def charts(*answers):
    return [c["chart"] for c in g.next_step(list(answers))["result"]["charts"]]


def test_the_tree_is_figure_10_5():
    assert g.next_step([]) == {"answers": [], "step": "start", "options": ["attribute", "variable"]}
    # attribute data
    assert charts("attribute", "defectives", "yes") == ["np", "p"] and charts("attribute", "defectives", "no") == ["p"]
    assert charts("attribute", "defects", "yes") == ["c", "u"] and charts("attribute", "defects", "no") == ["u"]
    # variable data: subgroup of 5 or more, 2 to 4, one and homogeneous
    assert charts("variable", "five_or_more") == ["xbar_s"] and charts("variable", "two_to_four") == ["xbar_r"] and charts("variable", "one", "yes") == ["imr"]
    # when Shewhart charts do not work: the box of the figure
    assert g.next_step(["variable", "one", "no"])["options"] == ["start_up", "tolerance_related", "non_normal", "batch", "autocorrelation", "small_shift", "short_run", "multivariate", "rare_events"]
    assert charts("variable", "one", "no", "non_normal") == ["shewhart_transformed", "median", "pre_control", "pearson", "extended", "percentile"]
    assert charts("variable", "one", "no", "batch") == ["levey_jennings", "extended"]
    assert charts("variable", "one", "no", "autocorrelation") == ["cusum", "uwma", "regression", "ar_residual"]
    assert charts("variable", "one", "no", "short_run") == ["zmr", "delta_target"] and charts("variable", "one", "no", "multivariate") == ["hotelling"]
    assert charts("variable", "one", "no", "rare_events") == ["g_chart", "t_chart"] and charts("variable", "one", "no", "start_up") == ["pre_control", "preliminary_acceptance"]


def test_every_path_ends_in_charts_and_the_result_says_what_the_program_has():
    def walk(answers):
        out = g.next_step(answers)
        if "result" in out:
            yield answers, out["result"]
            return
        for o in out["options"]:
            yield from walk(answers + [o])

    results = list(walk([]))
    assert len(results) == 4 + 2 + 1 + 9  # attribute 4, subgroup of 5 or more and of 2 to 4, individuals in a homogeneous process, the nine special situations
    from spc.monitor.model import KINDS

    seen = set()
    for _, res in results:
        for c in res["charts"]:
            seen.add(c["chart"])
            assert c["support"] in ("full", "partial", "none")
            for kind in c["kinds"]:
                assert kind in KINDS, (c["chart"], kind)  # every monitor kind the guide names exists in the program
            if c["support"] == "full":
                assert c["kinds"] or c.get("tool"), c["chart"]
    assert not [c for c in g.CHARTS if g.CHARTS[c]["support"] == "none"]  # every chart that figure 10-5 names is at least partly in the program
    assert {c for c in g.CHARTS if g.CHARTS[c]["support"] == "partial"} == {"shewhart_transformed", "regression"}
    assert {"laney", "g_chart", "t_chart", "percentile"} <= {c for c in g.CHARTS if g.CHARTS[c].get("tool") == "special"}


def test_bad_answers_are_refused():
    for bad in (["maybe"], ["attribute", "defectives", "yes", "yes"], ["variable", "five_or_more", "x"], ["variable", "one", "no", "nope"]):
        with pytest.raises(ValueError):
            g.next_step(bad)


def test_the_api_walks_the_tree(client=None):
    client = logged_in_client(make_app())
    r = client.post("/api/chart-guide", json={"answers": []}).json()
    assert r["step"] == "start"
    r = client.post("/api/chart-guide", json={"answers": ["variable", "one", "no", "autocorrelation"]}).json()
    assert [c["chart"] for c in r["result"]["charts"]] == ["cusum", "uwma", "regression", "ar_residual"] and "autocorrelation_ar" in r["result"]["notes"]
    assert client.post("/api/chart-guide", json={"answers": ["nope"]}).json()["error"]["code"] == "invalid_input"


def test_every_question_option_chart_and_note_has_a_text_in_both_languages():
    en = json.loads((ROOT / "web" / "static" / "i18n" / "en.json").read_text(encoding="utf-8"))
    zh = json.loads((ROOT / "web" / "static" / "i18n" / "zh-TW.json").read_text(encoding="utf-8"))
    for lang in (en, zh):
        for step, spec in g.TREE.items():
            assert f"guide.q_{step}" in lang, step
            for value, target in spec["options"].items():
                assert f"guide.o_{step}_{value}" in lang, (step, value)
                if not isinstance(target, str):
                    for note in target["notes"]:
                        assert f"guide.note_{note}" in lang, note
        for chart, spec in g.CHARTS.items():
            assert f"guide.chart_{chart}" in lang, chart
            if spec["support"] != "full":
                assert f"guide.chartnote_{chart}" in lang, chart

import json

import numpy as np
import pytest

from spc.data import Dataset, IncompleteSubgroupsError
from spc.service import AnalysisRequest, analyze


def labelled(k=25, n=5, seed=1, drift=0.0, mean=10.0, sd=0.1):
    rng = np.random.default_rng(seed)
    base = mean + np.linspace(0, drift, k)[:, None]
    values = (base + rng.normal(0, sd, (k, n))).ravel()
    return Dataset.from_values(values, subgroup=np.repeat([f"S{i + 1}" for i in range(k)], n))


def codes(result):
    return {w["code"] for w in result["warnings"]}


def test_result_is_json_serializable_and_complete():
    r = analyze(labelled(), AnalysisRequest(lsl=9.0, usl=11.0, characteristic_class="major", model="A1"))
    json.dumps(r)  # no numpy types left
    assert r["chart"]["kind"] == "xbar-s" and r["chart"]["n"] == 5 and r["chart"]["k"] == 25
    assert set(r) >= {"counts", "chart", "stability", "names", "indices", "targets", "normality", "params", "warnings"}
    assert r["params"]["fingerprint"]
    assert r["counts"]["n_total"] == 125 and r["counts"]["n_used"] == 125


def test_stable_process_with_model_a1_gets_cp_cpk_and_a_verdict():
    r = analyze(labelled(), AnalysisRequest(lsl=9.0, usl=11.0, characteristic_class="major", model="A1"))
    assert (r["names"]["p"], r["names"]["pk"]) == ("Cp", "Cpk")
    assert r["stability"]["class"] == "statistical_control"
    assert r["indices"]["pk"] > 3
    assert r["targets"]["verdict_pk"] == "meets" and r["targets"]["pk"] == 1.33
    assert not r["targets"]["adjusted"]


def test_without_a_model_the_indices_are_named_pp_ppk():
    r = analyze(labelled(), AnalysisRequest(lsl=9.0, usl=11.0))
    assert r["names"]["pk"] == "Ppk"
    assert r["stability"]["class"] == "unknown"
    assert "named_pp_not_cp" in codes(r)


def test_drifting_process_is_unstable_and_cwk_exceeds_ppk():
    r = analyze(labelled(drift=0.8, seed=3), AnalysisRequest(lsl=9.0, usl=11.0, model="A1",
                                                              rules={"run_length": 7}))
    assert r["stability"]["chart_stable"] is False
    assert r["names"]["pk"] == "Ppk"
    assert r["chart"]["location"]["alarms"]
    assert r["diagnosis"]["pk"] > r["indices"]["pk"]
    assert r["diagnosis"]["not_for_reports"] is True


def test_machine_study_uses_individuals_and_pm_names():
    rng = np.random.default_rng(8)
    d = Dataset.from_values(rng.normal(10, 0.05, 50))
    r = analyze(d, AnalysisRequest(stage="machine", lsl=9.5, usl=10.5, characteristic_class="major"))
    assert (r["names"]["p"], r["names"]["pk"]) == ("Pm", "Pmk")
    assert r["chart"]["kind"] == "imr"
    assert r["stability"]["assessed"] is False
    assert r["targets"]["p"] == 2.0 and r["targets"]["pk"] == 1.67


def test_reduced_machine_sample_raises_the_target_and_critical_is_blocked():
    rng = np.random.default_rng(9)
    d = Dataset.from_values(rng.normal(10, 0.05, 30))
    major = analyze(d, AnalysisRequest(stage="machine", lsl=9.5, usl=10.5, characteristic_class="major"))
    assert major["targets"]["adjusted"] and major["targets"]["pk"] == pytest.approx(1.96, abs=0.01)
    critical = analyze(d, AnalysisRequest(stage="machine", lsl=9.5, usl=10.5, characteristic_class="critical"))
    assert critical["targets"]["blocked"] is True
    assert "target_not_allowed" in codes(critical)
    assert "sample_below_base" in codes(critical)


def test_marked_values_leave_the_calculation_and_their_subgroup_is_reported():
    d = labelled()
    d = d.mark_invalid([7], "sensor glitch", "A")
    r = analyze(d, AnalysisRequest(lsl=9.0, usl=11.0, incomplete="drop"))
    assert r["chart"]["k"] == 24 and r["counts"]["n_used"] == 120 and r["counts"]["n_invalid"] == 1
    warn = next(w for w in r["warnings"] if w["code"] == "dropped_subgroups")
    assert warn["params"]["labels"] == ["S2"]
    with pytest.raises(IncompleteSubgroupsError):
        analyze(d, AnalysisRequest(lsl=9.0, usl=11.0, incomplete="error"))


def test_missing_spec_limits_gives_chart_only():
    r = analyze(labelled(), AnalysisRequest())
    assert r["indices"] is None and "spec_missing" in codes(r)
    assert r["chart"]["location"]["values"]


def test_non_normal_data_is_flagged():
    rng = np.random.default_rng(5)
    d = Dataset.from_values(rng.lognormal(0, 0.8, 125), subgroup=np.repeat([f"S{i}" for i in range(25)], 5))
    r = analyze(d, AnalysisRequest(lsl=0.0, usl=20.0))
    assert "non_normal" in codes(r)
    assert r["normality"]["p_value"] < 0.05


def test_one_sided_specification():
    r = analyze(labelled(), AnalysisRequest(usl=11.0, characteristic_class="minor"))
    assert r["indices"]["p"] is None and "one_sided_no_p" in codes(r)
    assert r["targets"]["verdict_p"] is None
    assert r["targets"]["verdict_pk"] == "meets"


def test_chart_selection_follows_subgroup_size():
    assert analyze(labelled(n=3), AnalysisRequest(lsl=9, usl=11))["chart"]["kind"] == "xbar-r"
    assert analyze(labelled(n=5), AnalysisRequest(lsl=9, usl=11))["chart"]["kind"] == "xbar-s"
    assert analyze(labelled(n=5), AnalysisRequest(lsl=9, usl=11, chart="xbar-r"))["chart"]["kind"] == "xbar-r"
    plain = Dataset.from_values(np.random.default_rng(2).normal(10, 0.1, 100))
    assert analyze(plain, AnalysisRequest(lsl=9, usl=11))["chart"]["kind"] == "imr"
    chunked = analyze(plain, AnalysisRequest(lsl=9, usl=11, subgroup_size=5))
    assert chunked["chart"]["kind"] == "xbar-s" and chunked["chart"]["k"] == 20
    with pytest.raises(ValueError):
        analyze(labelled(n=5), AnalysisRequest(chart="imr"))
    with pytest.raises(ValueError):
        analyze(plain, AnalysisRequest(chart="xbar-s"))


def test_chart_points_trace_back_to_the_dataset():
    d = labelled(k=10)
    r = analyze(d, AnalysisRequest(lsl=9, usl=11))
    loc = r["chart"]["location"]
    assert loc["labels"][0] == "S1"
    assert loc["positions"][1] == [5, 6, 7, 8, 9]
    imr_r = analyze(d, AnalysisRequest(lsl=9, usl=11, stage="machine"))
    assert imr_r["chart"]["variation"]["positions"][0] == [0, 1]


def test_input_validation():
    d = labelled()
    with pytest.raises(ValueError):
        analyze(d, AnalysisRequest(stage="beta"))
    with pytest.raises(ValueError):
        analyze(d, AnalysisRequest(lsl=5, usl=1))
    with pytest.raises(ValueError):
        analyze(d, AnalysisRequest(rules={"nonsense": True}))
    with pytest.raises(ValueError):
        analyze(d, AnalysisRequest(model="Z9", lsl=9, usl=11))


def test_edition_final_adds_a_warning():
    r = analyze(labelled(), AnalysisRequest(lsl=9, usl=11, edition="final", characteristic_class="major"))
    assert "edition_final_unverified" in codes(r)


def test_stable_processes_are_not_flagged_unstable_by_chance():
    flagged = 0
    runs = 150
    for seed in range(runs):
        r = analyze(labelled(k=60, seed=1000 + seed), AnalysisRequest(lsl=9, usl=11, model="A1"))
        flagged += r["stability"]["chart_stable"] is False
    assert flagged / runs < 0.04  # strict mode would flag about 30 % of these runs

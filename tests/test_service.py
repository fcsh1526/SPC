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


# ------------------------------------------------------------------ the fit check (draft 9.4) and Pmk left out by agreement (draft 8.2.4)

def test_the_fit_check_is_only_in_the_result_when_asked_for_and_looks_at_the_limit_that_matters():
    import numpy as np

    from spc.data import Dataset
    from spc.service import AnalysisRequest, analyze

    rng = np.random.default_rng(51)
    ds = Dataset.from_values(rng.lognormal(0, 0.3, 200) + 5)
    plain = analyze(ds, AnalysisRequest(stage="machine", lsl=4.0, usl=9.0))
    assert "fit_check" not in plain  # nothing changes for the archives made before
    r = analyze(ds, AnalysisRequest(stage="machine", lsl=4.0, usl=7.5, fit_check=True))
    assert r["fit_check"]["distribution"] == "normal" and r["fit_check"]["n_tail"] == 50 and 0.9 < r["fit_check"]["all"] < 1
    # the upper limit is the nearer one (1.5 against 2.0 from the mean), so Pmk belongs to the upper tail and the tail is judged there
    assert r["fit_check"]["side"] == "upper"
    assert analyze(ds, AnalysisRequest(stage="machine", lsl=4.0, usl=9.0, fit_check=True))["fit_check"]["side"] == "lower"  # here the lower limit is the nearer
    low = analyze(ds, AnalysisRequest(stage="machine", lsl=4.0, fit_check=True))
    assert low["fit_check"]["side"] == "lower"
    fitted = analyze(ds, AnalysisRequest(stage="machine", lsl=4.0, usl=7.5, distribution="lognormal", fit_check=True, bootstrap_n=0))
    assert fitted["fit_check"]["distribution"] == "lognormal" and 0.9 < fitted["fit_check"]["tail"] <= 1 and fitted["fit_check"]["side"] == "upper"


def test_pmk_can_be_left_out_by_agreement_in_a_machine_study_only():
    import numpy as np

    from spc.data import Dataset
    from spc.service import AnalysisRequest, analyze

    rng = np.random.default_rng(52)
    ds = Dataset.from_values(rng.normal(10, 0.05, 50))
    base = dict(stage="machine", lsl=9.5, usl=10.5, characteristic_class="major")
    normal = analyze(ds, AnalysisRequest(**base))
    left_out = analyze(ds, AnalysisRequest(**base, pmk_excluded="Customer agreed on 2026-03-02: the tool lasts 4 parts"))
    assert normal["targets"]["verdict_pk"] is not None and normal["targets"]["verdict_p"] is not None and "pmk_excluded" not in normal
    assert left_out["targets"]["verdict_pk"] is None and left_out["targets"]["verdict_p"] == normal["targets"]["verdict_p"]
    assert left_out["indices"]["pk"] == normal["indices"]["pk"]  # still calculated, shown for information
    assert left_out["pmk_excluded"]["agreement"].startswith("Customer agreed")
    for bad in (dict(stage="production", pmk_excluded="agreed"), dict(stage="machine", pmk_excluded="x"), dict(stage="machine", pmk_excluded="y" * 501)):
        with pytest.raises(ValueError):
            analyze(ds, AnalysisRequest(**{**base, **bad}))


def test_the_report_and_the_archive_carry_the_fit_check_and_the_agreement(tmp_path):
    from tests.conftest import logged_in_client, make_app
    from tests.test_api import REPORT_BODY, csv_text, upload

    client = logged_in_client(make_app())
    ds = upload(client, csv_text(k=25, n=5)).json()
    analysis = {**REPORT_BODY["analysis"], "stage": "machine", "characteristic_class": "major", "fit_check": True, "distribution": "folded_normal",
                "bootstrap_n": 0, "pmk_excluded": "Agreed with the customer: the tool is replaced every 3 parts"}
    out = client.post(f"/api/datasets/{ds['id']}/reports", json={**REPORT_BODY, "analysis": analysis})
    assert out.status_code == 200, out.text
    html = client.get(out.json()["urls"]["html"]).text
    assert "Pmk is left out of the evaluation by agreement with the customer" in html and "the tool is replaced every 3 parts" in html
    assert "Fit of the distribution Folded normal" in html
    check = client.post("/api/archive/check", content=client.get(out.json()["urls"]["archive"]).content).json()
    assert check == {"integrity_ok": True, "reproduced": True, "same_engine_version": True, "differences": []}

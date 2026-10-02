import json

import numpy as np
import pytest
from scipy.stats import norm

from spc.core.charts.variable import imr, imr_moving
from spc.core.constants import ALPHA_3SIGMA, d2, w_quantile
from spc.core.rules import RuleSet
from spc.data import Dataset
from spc.data.serialize import dataset_from_dict, dataset_to_dict
from spc.report import generate, reproduce
from spc.service import AnalysisRequest, analyze
from tests.conftest import logged_in_client, make_app
from tests.test_api import err, upload


def stepped(n1=30, n2=30, shift=2.0, seed=1):
    rng = np.random.default_rng(seed)
    return np.r_[rng.normal(10, 0.1, n1), rng.normal(10 + shift, 0.1, n2)]


# ------------------------------------------------------------------ core chart

@pytest.mark.parametrize("m", [1, 2])
def test_without_restarts_and_with_size_one_or_two_it_is_the_plain_imr_chart(m):
    x = np.random.default_rng(3).normal(10, 0.2, 60)
    a, b = imr(x), imr_moving(x, moving_n=m)
    assert b.mu_hat == pytest.approx(a.mu_hat) and b.sigma_hat == pytest.approx(a.sigma_hat)
    if m == 1:
        assert b.location_values == pytest.approx(a.location_values)
        assert b.location.ucl == pytest.approx(np.full(60, a.location.ucl))
    assert b.variation_values == pytest.approx(a.variation_values)
    assert b.variation.ucl == pytest.approx(np.full(59, a.variation.ucl))
    assert b.variation.lcl == pytest.approx(np.full(59, a.variation.lcl))
    assert b.variation.center == pytest.approx(a.variation.center)


def test_a_moving_range_never_spans_a_restart_and_sigma_comes_from_inside_the_segments():
    x = stepped()
    plain = imr(x)
    cut = imr_moving(x, restarts=[30])
    assert cut.variation_values.size == 58  # 59 ranges minus the one across the restart
    within = np.r_[np.abs(np.diff(x[:30])), np.abs(np.diff(x[30:]))]
    assert cut.sigma_hat == pytest.approx(within.mean() / d2(2))
    assert plain.sigma_hat > 1.3 * cut.sigma_hat  # one big range among 59 inflates the plain estimate
    assert np.max(plain.variation_values) > plain.variation.ucl  # the plain chart alarms on the jump
    assert not (cut.variation_values > cut.variation.ucl).any()
    assert cut.segment_starts == (0, 30) and list(cut.variation_end[28:30]) == [29, 31]


def test_location_limits_follow_the_size_of_the_moving_sample_and_start_again_after_a_restart():
    x = np.random.default_rng(5).normal(10, 0.2, 40)
    c = imr_moving(x, moving_n=3, restarts=[20])
    assert list(c.location_window[:5]) == [1, 2, 3, 3, 3]
    assert list(c.location_window[18:23]) == [3, 3, 1, 2, 3]
    assert c.location_values[0] == x[0] and c.location_values[1] == pytest.approx(x[:2].mean())
    assert c.location_values[20] == x[20] and c.location_values[21] == pytest.approx(x[20:22].mean())
    assert c.location_values[22] == pytest.approx(x[20:23].mean())
    u = norm.ppf(1 - ALPHA_3SIGMA / 2)
    half = (c.location.ucl - c.mu_hat)
    assert half == pytest.approx(u * c.sigma_hat / np.sqrt(c.location_window))
    assert c.location.lcl == pytest.approx(c.mu_hat - half)
    # the variation chart has ranges of 2, then 3 values; the first value of a segment has no range
    assert list(c.variation_window[:3]) == [2, 3, 3] and c.variation_values.size == 38
    assert c.variation.ucl[0] == pytest.approx(w_quantile(2, 1 - ALPHA_3SIGMA / 2) * c.sigma_hat)
    assert c.variation.ucl[1] == pytest.approx(w_quantile(3, 1 - ALPHA_3SIGMA / 2) * c.sigma_hat)
    full = c.variation_window == 3
    assert c.sigma_hat == pytest.approx(c.variation_values[full].mean() / d2(3))
    assert c.variation.center == pytest.approx(c.variation_values[full].mean())


def test_every_plotted_point_has_the_stated_false_alarm_rate_in_the_start_up_too():
    """Independent normal data, 3-sigma risk. The rate is checked separately for each size of moving sample."""
    rng = np.random.default_rng(2026)
    n, seg = 1_200_000, 10
    x = rng.normal(0, 1, n)
    c = imr_moving(x, moving_n=3, restarts=list(range(seg, n, seg)))
    loc_alarm = (c.location_values > c.location.ucl) | (c.location_values < c.location.lcl)
    var_alarm = (c.variation_values > c.variation.ucl) | (c.variation_values < c.variation.lcl)
    for t in (1, 2, 3):
        rate = loc_alarm[c.location_window == t].mean()
        assert rate == pytest.approx(ALPHA_3SIGMA, rel=0.15), (t, rate)
    for t in (2, 3):
        rate = var_alarm[c.variation_window == t].mean()
        assert rate == pytest.approx(ALPHA_3SIGMA, rel=0.25), (t, rate)  # the range of 3 is rougher: fewer independent values


@pytest.mark.parametrize("kwargs", [dict(moving_n=0), dict(moving_n=11), dict(restarts=[0]), dict(restarts=[60]),
                                    dict(restarts=[-1]), dict(moving_n=5, restarts=[2, 4, 6, 8, 10])])
def test_bad_settings(kwargs):
    with pytest.raises(ValueError):
        imr_moving(np.random.default_rng(1).normal(size=60) if "restarts" not in kwargs or kwargs["restarts"][0] != 2 else np.arange(12.0), **kwargs)


def test_duplicate_restart_indices_count_once():
    x = np.random.default_rng(1).normal(size=50)
    assert imr_moving(x, restarts=[20, 20]).segment_starts == (0, 20)


# ------------------------------------------------------------------ dataset

def test_restarts_are_marks_with_reason_and_person_and_keep_the_values():
    ds = Dataset.from_values(np.arange(10.0))
    with pytest.raises(ValueError):
        ds.add_restart([3], "", "A")
    with pytest.raises(ValueError):
        ds.add_restart([3], "tool change", "")
    with pytest.raises(ValueError, match="first value"):
        ds.add_restart([0], "tool change", "A")
    r = ds.add_restart([3, 7], "tool change", "A", at="2026-10-01T08:00:00Z")
    assert r.restart_info() == {3: ("tool change", "A", "2026-10-01T08:00:00Z"), 7: ("tool change", "A", "2026-10-01T08:00:00Z")}
    assert r.n_valid == 10 and r.n_invalid == 0 and r.summary()["n_restarts"] == 2
    assert "n_restarts" not in ds.summary()
    with pytest.raises(ValueError, match="already"):
        r.add_restart([3], "again", "B")
    back = r.remove_restart([3], "was a mistake", "B")
    assert list(back.restart_info()) == [7] and [e.action for e in back.log] == ["restart", "unrestart"]
    with pytest.raises(ValueError, match="no restart"):
        back.remove_restart([3], "x", "B")


def test_a_restart_never_changes_whether_a_value_is_valid():
    ds = Dataset.from_values(np.arange(10.0)).mark_invalid([5], "wrong part", "A")
    r = ds.add_restart([5], "tool change", "A").remove_restart([5], "no", "A")
    assert list(r.invalid_info()) == [5] and r.n_valid == 9  # an "unrestart" must not act like a "restore"
    r2 = ds.restore([5], "ok", "A").add_restart([5], "tool change", "A")
    assert r2.n_invalid == 0 and list(r2.restart_info()) == [5]


def test_restarts_survive_serialisation():
    ds = Dataset.from_values(np.arange(10.0)).add_restart([4], "tool change", "A")
    back = dataset_from_dict(json.loads(json.dumps(dataset_to_dict(ds))))
    assert back.restart_info() == ds.restart_info()


def test_csv_export_still_works_with_restarts_in_the_log():
    from spc.data import to_csv

    ds = Dataset.from_values(np.arange(10.0)).add_restart([4], "tool change", "A")
    assert len(to_csv(ds).splitlines()) == 11


# ------------------------------------------------------------------ analysis service

def req(**kw):
    base = dict(stage="production", lsl=8.0, usl=14.0, rules=RuleSet().__dict__ | {})
    base.pop("rules")
    base.update(kw)
    return AnalysisRequest(**base)


def test_the_restart_from_the_data_removes_the_false_alarm_after_a_planned_change():
    x = stepped(shift=0.6)  # a tool change moved the level by 6 sigma; the process is fine afterwards
    plain = analyze(Dataset.from_values(x), req())
    cut = analyze(Dataset.from_values(x).add_restart([30], "tool change", "A"), req())
    assert plain["chart"]["variation"]["alarms"] and not cut["chart"]["variation"]["alarms"]
    assert cut["chart"]["variation"]["restarts"] == [29] and cut["chart"]["location"]["restarts"] == [30]
    assert cut["chart"]["moving_n"] == 1
    assert len(cut["chart"]["variation"]["values"]) == 58 and isinstance(cut["chart"]["variation"]["ucl"], list)
    # the indices describe all the data, with the level change in them, restart or not
    assert cut["indices"]["pk"] == plain["indices"]["pk"] and cut["counts"]["n_restarts"] == 1


def test_variation_points_name_the_values_they_were_computed_from():
    x = stepped()
    r = analyze(Dataset.from_values(x).add_restart([30], "tool change", "A"), req(moving_n=3))
    var = r["chart"]["variation"]
    assert var["positions"][0] == [0, 1] and var["positions"][1] == [0, 1, 2]
    assert var["positions"][28] == [27, 28, 29]  # the last range of the first segment
    first_after = var["restarts"][0]
    assert var["positions"][first_after] == [30, 31] and var["labels"][first_after] == "32"
    loc = r["chart"]["location"]
    assert loc["positions"][30] == [30] and loc["positions"][31] == [30, 31] and loc["positions"][32] == [30, 31, 32]


def test_runs_do_not_continue_over_a_restart():
    rng = np.random.default_rng(1)
    x = np.r_[np.full(7, 10.5) + np.arange(7) * 0.001, 10.0 + rng.normal(0, 0.05, 33)]
    rules = RuleSet(beyond_limits=False, run_length=7).__dict__
    plain = analyze(Dataset.from_values(x), req(rules=rules))
    assert any(a["rule"] == "run" and a["index"] == 6 for a in plain["chart"]["location"]["alarms"])
    split = analyze(Dataset.from_values(x).add_restart([4], "tool change", "A"), req(rules=rules))
    # 4 values before and 3 after the restart: neither part is a run of 7
    assert not any(a["index"] < 7 and a["rule"] == "run" for a in split["chart"]["location"]["alarms"])


def test_restarts_have_no_effect_when_the_values_around_them_are_marked_invalid():
    ds = Dataset.from_values(np.random.default_rng(1).normal(10, 0.1, 40))
    ds = ds.mark_invalid([0, 1, 2, 3], "wrong part", "A").add_restart([3], "tool change", "A")
    r = analyze(ds, req())
    assert any(w["code"] == "restart_without_effect" for w in r["warnings"])
    assert "moving_n" not in r["chart"]  # nothing to restart: the plain chart


def test_restarts_are_not_used_by_subgroup_charts_and_a_moving_size_needs_individuals():
    rng = np.random.default_rng(1)
    ds = Dataset.from_values(rng.normal(10, 0.1, 100), subgroup=[f"L{i // 5}" for i in range(100)]).add_restart([10], "tool change", "A")
    r = analyze(ds, req())
    assert any(w["code"] == "restarts_not_used" for w in r["warnings"])
    with pytest.raises(ValueError, match="moving sample"):
        analyze(ds, req(moving_n=3))
    with pytest.raises(ValueError):
        analyze(Dataset.from_values(rng.normal(10, 0.1, 40)), req(moving_n=11))


def test_default_individuals_chart_is_unchanged():
    r = analyze(Dataset.from_values(np.random.default_rng(1).normal(10, 0.1, 40)), req())
    assert "moving_n" not in r["chart"] and isinstance(r["chart"]["location"]["ucl"], float)
    assert "restarts" not in r["chart"]["location"]


def test_machine_stage_uses_restarts_too():
    ds = Dataset.from_values(stepped(shift=0.6)).add_restart([30], "tool change", "A")
    r = analyze(ds, req(stage="machine"))
    assert r["chart"]["moving_n"] == 1


# ------------------------------------------------------------------ report

def test_report_shows_moving_sample_restarts_and_step_limits():
    ds = Dataset.from_values(stepped(shift=0.6)).add_restart([30], "tool change T-07", "A. Chen")
    g = generate(ds, req(stage="preliminary", moving_n=3), None, "en", now="2026-10-01T00:00:00+00:00", report_id="r1")
    assert "Moving sample" in g.html and "tool change T-07" in g.html and "A. Chen" in g.html
    assert "Restarts of the individuals chart" in g.html and "Cw" not in g.html
    assert 'stroke-dasharray="2 3"' in g.html  # the restart line in the chart
    zh = generate(ds, req(stage="preliminary", moving_n=3), None, "zh-TW", now="2026-10-01T00:00:00+00:00", report_id="r2")
    assert "移動樣本" in zh.html and "個別值圖的重啟" in zh.html
    res = reproduce(g.archive)
    assert res.integrity_ok and res.reproduced, res.differences
    assert g.archive["request"]["moving_n"] == 3 and g.archive["dataset"]["log"][0]["action"] == "restart"


def test_report_of_a_plain_chart_has_no_moving_text():
    g = generate(Dataset.from_values(np.random.default_rng(1).normal(10, 0.1, 40)), req(stage="preliminary"), None, "en")
    assert "Moving sample" not in g.html and "Restarts of the individuals" not in g.html


# ------------------------------------------------------------------ API

def csv_individuals(n=60):
    rng = np.random.default_rng(2)
    return ("v\n" + "\n".join(f"{v:.4f}" for v in np.r_[rng.normal(10, 0.1, n // 2), rng.normal(10.6, 0.1, n // 2)])).encode()


@pytest.fixture
def client():
    return logged_in_client(make_app(max_upload=200_000))


def test_restart_endpoints_audit_and_errors(client):
    ds = client.post("/api/datasets", params={"value": "v"}, content=csv_individuals()).json()
    base = f"/api/datasets/{ds['id']}"
    assert err(client.post(f"{base}/restarts", json={"positions": [30], "reason": " "}))["code"] == "reason_required"
    assert err(client.post(f"{base}/restarts", json={"positions": [0], "reason": "x"}))["code"] == "restart_at_start"
    assert err(client.post(f"{base}/restarts", json={"positions": [999], "reason": "x"}))["code"] == "positions_out_of_range"
    ok = client.post(f"{base}/restarts", json={"positions": [30], "reason": "tool change"})
    assert ok.status_code == 200 and ok.json()["restarts"] == [
        {"pos": 30, "reason": "tool change", "by": "Eva Engineer (eng)", "at": ok.json()["restarts"][0]["at"]}]
    assert ok.json()["summary"]["n_restarts"] == 1 and ok.json()["summary"]["n_invalid"] == 0
    assert err(client.post(f"{base}/restarts", json={"positions": [30], "reason": "again"}))["code"] == "already_restart"
    rows = client.get(f"{base}/rows", params={"offset": 29, "limit": 3}).json()["rows"]
    assert [bool(r["restart"]) for r in rows] == [False, True, False] and rows[1]["restart"]["reason"] == "tool change"
    out = client.post(f"{base}/analyze", json={"moving_n": 3}).json()
    assert out["chart"]["moving_n"] == 3 and out["chart"]["location"]["restarts"] == [30]
    assert client.get(f"{base}/export.csv").status_code == 200
    assert err(client.post(f"{base}/restarts/remove", json={"positions": [31], "reason": "x"}))["code"] == "not_restart"
    assert client.post(f"{base}/restarts/remove", json={"positions": [30], "reason": "wrong row"}).json()["restarts"] == []


def test_a_viewer_cannot_restart_and_the_audit_trail_has_it():
    app = make_app(max_upload=200_000)
    eng, view, admin = (logged_in_client(app, u) for u in ("eng", "view", "admin"))
    ds = eng.post("/api/datasets", params={"value": "v"}, content=csv_individuals()).json()
    assert view.post(f"/api/datasets/{ds['id']}/restarts", json={"positions": [30], "reason": "r"}).status_code == 403
    eng.post(f"/api/datasets/{ds['id']}/restarts", json={"positions": [30], "reason": "tool change"})
    entries = admin.get("/api/audit").json()["entries"]
    e = next(x for x in entries if x["action"] == "dataset_restart_added")
    assert e["username"] == "eng" and e["detail"]["reason"] == "tool change" and e["detail"]["positions"] == [30]
    assert admin.get("/api/audit/verify").json()["ok"]


def test_moving_size_is_validated(client):
    ds = client.post("/api/datasets", params={"value": "v"}, content=csv_individuals()).json()
    r = client.post(f"/api/datasets/{ds['id']}/analyze", json={"moving_n": 11})
    assert r.status_code == 422

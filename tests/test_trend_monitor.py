"""The monitor for a process with a trend that cannot be removed (tool wear): the chart of the residuals about the line of the cycle.
A new cycle starts when a person says so (the tool was changed) and writes down what happened."""

import numpy as np
import pytest
from scipy import stats

from spc.core.charts import trend
from tests.test_api import err
from tests.test_monitor import enter, env, make_monitor  # noqa: F401 (env is a fixture)

CONFIG = {"name": "Bore diameter", "process": "honing", "characteristic": "diameter", "unit": "mm", "line": "L2", "kind": "trend", "warn_alpha": 0.05, "require_ack": False,
          "ocap": {"default": {"operator_action": "Measure again, then call the setter", "responsible": "Setter", "escalate_to": "", "escalate_after_min": 0}}}
A, B, SIGMA = 10.000, -0.010, 0.02  # the line: 10.000 mm at the start of a cycle, 0.010 mm less with each sample
PARAMS = {"type": "parameters", "intercept": A, "slope": B, "sigma": SIGMA}


def on_line(rng, pos, level=0.0, sigma=SIGMA):
    return float(A + B * pos + level + rng.normal(0, sigma))


def run_cycle(op, mid, rng, length, first_note=None, offset=0.0, sigma=SIGMA):
    out = []
    for i in range(length):
        body = {"values": [on_line(rng, i, offset, sigma)]}
        if i == 0 and first_note:
            body["cycle"] = first_note
        out.append(enter(op, mid, **body))
    return out


# ------------------------------------------------------------------ the fit with cycles of any length

def test_a_fit_with_positions_equals_the_regression_on_those_positions_and_tells_if_the_cycles_differ():
    rng = np.random.default_rng(1)
    lengths = [14, 9, 20, 11]
    t = np.concatenate([np.arange(m) for m in lengths])
    g = np.concatenate([np.full(m, i) for i, m in enumerate(lengths)])
    y = 10 - 0.01 * t + rng.normal(0, 0.02, t.size)
    f = trend.fit(y, positions=t, groups=g)
    ref = stats.linregress(t, y)
    assert f.slope == pytest.approx(ref.slope) and f.intercept == pytest.approx(ref.intercept) and f.slope_p == pytest.approx(ref.pvalue)
    res = y - (ref.intercept + ref.slope * t)
    assert f.sigma == pytest.approx(np.sqrt(res @ res / (t.size - 2))) and f.n_cycles == 4 and f.df == t.size - 2
    assert f.cycle_level_p > 0.01  # the same line serves every cycle
    shifted = y + np.where(g % 2 == 0, 0.15, -0.15)  # the cycles start at different levels
    assert trend.fit(shifted, positions=t, groups=g).cycle_level_p < 1e-6
    for bad in (dict(positions=t[:-1]), dict(positions=-t - 1), dict(positions=t, groups=g[:-1])):
        with pytest.raises(ValueError):
            trend.fit(y, **bad)
    assert trend.fit(y, cycle=10).n_cycles == 6  # the old way, a fixed length, is unchanged


# ------------------------------------------------------------------ the monitor

def test_the_residual_chart_follows_the_line_and_a_new_cycle_starts_the_line_again(env):
    app, c = env
    mid = make_monitor(c["eng"], CONFIG, PARAMS)
    rng = np.random.default_rng(2)
    first = run_cycle(c["oper"], mid, rng, 12)
    assert all(r["status"] != "alarm" for r in first)
    p5 = first[5]["point"]["trend"]
    assert p5["position"] == 5 and p5["cycle"] == 1 and p5["expected"] == pytest.approx(A + 5 * B) and p5["new_cycle"] == ""
    assert first[5]["point"]["loc"] == pytest.approx(first[5]["point"]["values"][0] - (A + 5 * B))  # what is plotted is the residual
    # the tool is changed: the operator says so with the first sample of the new tool
    second = run_cycle(c["oper"], mid, rng, 8, first_note="Honing stone 7 fitted")
    t0 = second[0]["point"]
    assert t0["cycle"] == "Honing stone 7 fitted" and t0["trend"]["position"] == 0 and t0["trend"]["cycle"] == 2 and t0["trend"]["expected"] == pytest.approx(A)
    assert second[0]["status"] != "alarm" and second[7]["point"]["trend"]["position"] == 7
    view = c["view"].get(f"/api/monitors/{mid}").json()
    by_seq = {p["seq"]: p for p in view["points"]}
    assert by_seq[13]["trend"]["new_cycle"] == "Honing stone 7 fitted" and by_seq[13]["trend"]["position"] == 0 and by_seq[12]["trend"]["position"] == 11
    assert view["limits"]["slope"] == B and view["limits"]["intercept"] == A
    audit = [r["action"] for r in app.state.db.all("SELECT action FROM audit WHERE action LIKE 'monitor_cycle%'")]
    assert audit == ["monitor_cycle_started"]


def test_a_forgotten_tool_change_is_an_alarm_until_somebody_marks_the_new_cycle(env):
    app, c = env
    mid = make_monitor(c["eng"], CONFIG, PARAMS)
    rng = np.random.default_rng(3)
    run_cycle(c["oper"], mid, rng, 12)
    # a new tool is fitted, but nobody says so: the line expects 9.88 and the value is near 10.00 (6 sigma above)
    r = enter(c["oper"], mid, values=[on_line(rng, 0)])
    assert r["status"] == "alarm" and r["incident"]["status"] == "open" and {a["rule"] for a in r["point"]["alarms"]} >= {"beyond_limits"}
    assert r["point"]["trend"]["position"] == 12 and r["point"]["trend"]["cycle"] == 1
    # the person finds out why and says so with the next sample: the line starts again
    nxt = enter(c["oper"], mid, values=[on_line(rng, 0)], cycle="Tool changed at 14:05, not noted")
    assert nxt["point"]["trend"]["position"] == 0 and nxt["point"]["trend"]["cycle"] == 2 and nxt["status"] != "alarm"
    assert nxt["point"]["var"] is None  # no moving range across the change of the tool: that chart starts again


def test_a_cycle_needs_a_note_and_only_a_trend_monitor_has_cycles(env):
    app, c = env
    mid = make_monitor(c["eng"], CONFIG, PARAMS)
    for note in ("", "   "):
        r = c["oper"].post(f"/api/monitors/{mid}/points", json={"values": [10.0], "cycle": note})
        assert r.status_code == 400 and err(r)["code"] == "invalid_input"
    assert c["oper"].post(f"/api/monitors/{mid}/points", json={"values": [10.0], "cycle": "x" * 201}).status_code in (400, 422)
    assert c["view"].post(f"/api/monitors/{mid}/points", json={"values": [10.0], "cycle": "tool"}).status_code == 403
    imr = make_monitor(c["eng"], {**CONFIG, "name": "Plain", "kind": "imr"}, {"type": "parameters", "mu": 10.0, "sigma": 0.1})
    r = c["oper"].post(f"/api/monitors/{imr}/points", json={"values": [10.0], "cycle": "tool"})
    assert r.status_code == 400 and err(r)["code"] == "invalid_input"
    assert err(c["oper"].post(f"/api/monitors/{mid}/points", json={"values": [10.0, 10.1]}))["code"] == "wrong_value_count"
    cfg = {**CONFIG, "n": 5}
    assert err(c["eng"].post("/api/monitors", json={"config": cfg, "source": PARAMS}))["code"] == "invalid_input"  # one value per sample
    cfg = {**CONFIG, "specs": {"lsl": 9, "usl": 11}}
    assert err(c["eng"].post("/api/monitors", json={"config": cfg, "source": PARAMS}))["code"] == "invalid_input"  # no tolerance on the residuals


def test_limits_from_the_points_of_the_monitor_use_its_cycle_marks(env):
    app, c = env
    mid = make_monitor(c["eng"], CONFIG, PARAMS)
    rng = np.random.default_rng(4)
    run_cycle(c["oper"], mid, rng, 15, sigma=0.03)
    run_cycle(c["oper"], mid, rng, 12, first_note="Stone 2", sigma=0.03)
    run_cycle(c["oper"], mid, rng, 18, first_note="Stone 3", sigma=0.03)
    r = c["eng"].post(f"/api/monitors/{mid}/limits", json={"source": {"type": "points", "seq_from": 1, "seq_to": 45}, "reason": "limits from three cycles"})
    assert r.status_code == 200, r.text
    lim = r.json()["limits"]
    # independent: the same fit on the positions that the marks give
    pos = np.concatenate([np.arange(15), np.arange(12), np.arange(18)])
    y = np.array([p["values"][0] for p in c["view"].get(f"/api/monitors/{mid}", params={"limit": 100}).json()["points"]])
    ref = stats.linregress(pos, y)
    assert lim["intercept"] == pytest.approx(ref.intercept) and lim["slope"] == pytest.approx(ref.slope) and lim["diagnostics"]["n_cycles"] == 3
    res = y - (ref.intercept + ref.slope * pos)
    assert lim["sigma"] == pytest.approx(np.sqrt(res @ res / (45 - 2))) and lim["diagnostics"]["longest_cycle"] == 18 and lim["diagnostics"]["slope_p"] < 1e-10
    assert lim["revision"] == 2 and lim["diagnostics"]["cycle_level_p"] > 0.001
    # the new limits are used for the next sample, and the position is not reset by new limits
    nxt = enter(c["oper"], mid, values=[on_line(rng, 18, sigma=0.03)])
    assert nxt["point"]["trend"]["position"] == 18 and nxt["point"]["limits_rev"] == 2
    assert nxt["point"]["trend"]["expected"] == pytest.approx(lim["intercept"] + lim["slope"] * 18)


def test_limits_from_a_data_set_use_its_restarts_as_the_starts_of_the_cycles(env):
    app, c = env
    rng = np.random.default_rng(5)
    lengths = [16, 10, 14]
    y = np.concatenate([[on_line(rng, i, sigma=0.025) for i in range(m)] for m in lengths])
    y = np.round(y, 5)  # the file holds five decimals
    csv = "value\n" + "\n".join(f"{v:.5f}" for v in y)
    ds = c["eng"].post("/api/datasets", params={"value": "value", "filename": "cycles.csv"}, content=csv.encode()).json()
    starts = [16, 26]
    r = c["eng"].post(f"/api/datasets/{ds['id']}/restarts", json={"positions": starts, "reason": "tool changed"})
    assert r.status_code == 200, r.text
    # one value is marked invalid: it still takes its place in time, so the positions after it do not move
    assert c["eng"].post(f"/api/datasets/{ds['id']}/invalid", json={"positions": [3], "reason": "gauge error"}).status_code == 200
    mid = make_monitor(c["eng"], CONFIG, {"type": "dataset", "dataset_id": ds["id"]})
    lim = c["view"].get(f"/api/monitors/{mid}").json()["limits"]
    t = np.concatenate([np.arange(m) for m in lengths])
    keep = np.arange(y.size) != 3
    ref = stats.linregress(t[keep], y[keep])
    assert lim["intercept"] == pytest.approx(ref.intercept) and lim["slope"] == pytest.approx(ref.slope) and lim["diagnostics"]["n_cycles"] == 3
    src = c["view"].get(f"/api/monitors/{mid}").json()["limits_history"][0]["source"] if "limits_history" in c["view"].get(f"/api/monitors/{mid}").json() else None
    assert src is None or src["n_cycles"] == 3
    # without restarts it is one long cycle
    ds2 = c["eng"].post("/api/datasets", params={"value": "value", "filename": "one.csv"}, content=csv.encode()).json()
    mid2 = make_monitor(c["eng"], {**CONFIG, "name": "One cycle"}, {"type": "dataset", "dataset_id": ds2["id"]})
    assert c["view"].get(f"/api/monitors/{mid2}").json()["limits"]["diagnostics"]["n_cycles"] == 1


def test_bad_sources_are_refused_with_the_reason(env):
    app, c = env
    for source in ({"type": "parameters", "intercept": 10, "slope": -0.01, "sigma": 0}, {"type": "parameters", "intercept": "a", "slope": 0, "sigma": 1},
                   {"type": "parameters", "mu": 10, "sigma": 0.1}, {"type": "tolerance"}):
        r = c["eng"].post("/api/monitors", json={"config": CONFIG, "source": source})
        assert r.status_code == 400 and err(r)["code"] == "bad_source", source
    ds = c["eng"].post("/api/datasets", params={"value": "v"}, content=b"v\n1\n2\n3\n").json()
    r = c["eng"].post("/api/monitors", json={"config": CONFIG, "source": {"type": "dataset", "dataset_id": ds["id"]}})
    assert r.status_code == 400 and err(r)["code"] == "bad_source"  # too few values for a line


def test_a_trend_monitor_has_no_capability_report_and_exports_the_cycle_notes(env):
    app, c = env
    mid = make_monitor(c["eng"], CONFIG, PARAMS)
    rng = np.random.default_rng(6)
    run_cycle(c["oper"], mid, rng, 6)
    run_cycle(c["oper"], mid, rng, 6, first_note="=HYPERLINK(\"x\")")
    r = c["view"].get(f"/api/monitors/{mid}/ongoing")
    assert r.status_code == 400 and err(r)["code"] == "report_not_for_trend"
    import csv
    import io

    text = c["view"].get(f"/api/monitors/{mid}/export.csv").content.decode("utf-8-sig")
    head, *rows = list(csv.reader(io.StringIO(text)))
    assert head[-1] == "new_cycle" and len(rows) == 12
    assert [r[-1] for r in rows[:6]] == [""] * 6 and rows[6][-1] == "'=HYPERLINK(\"x\")" and [r[-1] for r in rows[7:]] == [""] * 5  # a note that starts like a formula is made safe

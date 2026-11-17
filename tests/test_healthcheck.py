"""The SPC health check (spc.healthcheck): reader, checks, texts, report and command line."""

import io
import json
import re
from dataclasses import fields
from pathlib import Path

import numpy as np
import pytest

from spc.data.csv_io import DataImportError
from spc.healthcheck import Columns, Settings, check_all, check_series, read_file
from spc.healthcheck import checks as checks_module
from spc.healthcheck.__main__ import main
from spc.healthcheck.checks import REFS, longest_run, resolution, step
from spc.healthcheck.reader import Series, read_header
from spc.healthcheck.report import finding_text, render_html, summary_text, to_json
from spc.healthcheck.texts import LANGUAGES, TEXTS, T

K, N = 60, 5
XBAR = (10 - 3 * 0.1 / np.sqrt(N), 10 + 3 * 0.1 / np.sqrt(N))  # the right X-bar limits of N(10, 0.1) in subgroups of 5
SPEC = {"lsl": 9.4, "usl": 10.6}


def rng(seed=3):
    return np.random.default_rng(seed)


def subgroups(matrix, limits=None, name="x", alarm=None, action=None):
    k, n = matrix.shape
    return Series(name, matrix.ravel(), np.repeat(np.arange(1, k + 1).astype(object), n), None, np.arange(k * n) + 2, alarm, action, dict(limits or {}), {})


def individuals(values, limits=None, name="x"):
    return Series(name, np.asarray(values, dtype=float), None, None, np.arange(len(values)) + 2, None, None, dict(limits or {}), {})


def codes(result, at_least="medium"):
    order = ("high", "medium", "low", "info")
    return [f["code"] for f in result["findings"] if order.index(f["severity"]) <= order.index(at_least)]


def good():
    return rng().normal(10, 0.1, (K, N))


# ------------------------------------------------------------------ reader

LONG = ("特性,子組,時間,量測值,LSL,USL,LCL,UCL,處置\n"
        "金厚,1,2026-01-01 08:00,0.061,0.03,0.1,0.05,0.07,\n"
        "金厚,1,2026-01-01 08:00,0.059,0.03,0.1,0.05,0.07,已調整\n"
        "金厚,2,2026-01-01 10:00,0.060,0.03,0.1,0.055,0.065,\n"
        "金厚,2,2026-01-01 10:00,,0.03,0.1,0.055,0.065,\n"
        "鎳厚,1,2026-01-01 08:00,4.5,3,6,,,\n")
LONG_COLUMNS = Columns(value=("量測值",), characteristic="特性", subgroup="子組", time="時間", lsl="LSL", usl="USL", lcl="LCL", ucl="UCL", action="處置")


def test_long_layout_in_big5_splits_the_characteristics():
    series, info = read_file(LONG.encode("big5"), LONG_COLUMNS)
    gold, nickel = series
    assert [s.name for s in series] == ["金厚", "鎳厚"] and info.encoding == "cp950" and info.rows == 5
    assert gold.values.tolist() == [0.061, 0.059, 0.060] and gold.subgroup.tolist() == ["1", "1", "2"] and gold.lines.tolist() == [2, 3, 4]
    assert gold.limits == {"lsl": 0.03, "usl": 0.1, "lcl": 0.055, "cl": None, "ucl": 0.065}       # the last value of a limit column counts
    assert gold.limit_changes["lcl"] == 2 and gold.action.tolist() == [False, True, False] and gold.skipped_rows == 1
    assert nickel.limits["lcl"] is None and str(nickel.time[0]) == "2026-01-01T08:00:00"
    assert read_header(LONG.encode("big5")) == ["特性", "子組", "時間", "量測值", "LSL", "USL", "LCL", "UCL", "處置"]


def test_wide_layout_takes_a_row_as_a_subgroup_and_limits_as_numbers():
    text = "date;x1;x2;x3\n2026-01-01;1,25;1,50;1,75\n2026-01-02;1,00;1,25;1,50\n"
    [s], _ = read_file(text.encode(), Columns(value=("x1", "x2", "x3"), time="date"), constants={"lsl": 0.5, "usl": 2.5}, decimal=",", default_name="thickness")
    assert s.name == "thickness" and s.values.tolist() == [1.25, 1.5, 1.75, 1.0, 1.25, 1.5]
    assert s.subgroup.tolist() == ["row 2"] * 3 + ["row 3"] * 3 and s.limits["usl"] == 2.5


def test_an_xlsx_sheet_is_read():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "金厚"
    ws.append(["subgroup", "value"])
    for i in range(6):
        ws.append([i // 2 + 1, 0.06 + i / 1000])
    buf = io.BytesIO()
    wb.save(buf)
    [s], info = read_file(buf.getvalue(), Columns(value=("value",), subgroup="subgroup"), sheet="金厚")
    assert len(s.values) == 6 and info.encoding == "xlsx [金厚]"


def test_all_problems_of_a_file_are_reported_with_their_lines():
    text = "c,v,t\nA,1.0,2026-01-01\nA,abc,2026-01-02\n,2.0,2026-01-03\nA,3.0,tomorrow\n"
    with pytest.raises(DataImportError) as exc:
        read_file(text.encode(), Columns(value=("v",), characteristic="c", time="t"))
    assert sorted((i.line, i.code) for i in exc.value.issues) == [(3, "not_a_number"), (4, "empty_characteristic"), (5, "bad_time")]
    with pytest.raises(DataImportError, match="not found"):
        read_file(text.encode(), Columns(value=("nope",)))
    with pytest.raises(DataImportError, match="no value"):
        read_file(b"v\n\n", Columns(value=("v",)))


def test_wrong_options_are_refused():
    with pytest.raises(ValueError, match="choose one"):
        read_file(b"v,l\n1,0\n", Columns(value=("v",), lsl="l"), constants={"lsl": 0.0})
    with pytest.raises(ValueError, match="subgroup"):
        read_file(b"a,b,g\n1,2,1\n", Columns(value=("a", "b"), subgroup="g"))
    with pytest.raises(ValueError, match="unknown limit"):
        read_file(b"v\n1\n", Columns(value=("v",)), constants={"xsl": 1.0})


# ------------------------------------------------------------------ helpers

def test_resolution_run_and_step():
    assert resolution([1.25, 1.5, 2.0]) == 0.25 and resolution([0.061, 0.059]) == pytest.approx(0.002)
    assert resolution([3, 3, 3]) is None and resolution([np.pi, np.e]) is None
    assert longest_run([1, 1, 2, 2, 2, 3]) == 3 and longest_run([]) == 0
    x = np.r_[np.zeros(30), np.ones(20)]
    assert step(x, 5) == (30, 0.0, 1.0) and step(x[:8], 5) is None


# ------------------------------------------------------------------ checks

def test_a_healthy_chart_has_no_finding():
    r = check_series(subgroups(good(), {**SPEC, "lcl": XBAR[0], "ucl": XBAR[1]}))
    assert r["worst"] == "ok" and codes(r) == [] and r["reference"]["method"] == "xbar-s"
    assert r["index"]["pk"] > 1.67 and r["k"] == K and r["n"] == N


def test_healthy_charts_are_seldom_flagged():
    flagged = sum(check_series(subgroups(rng(seed).normal(10, 0.1, (40, N)), {**SPEC, "lcl": XBAR[0], "ucl": XBAR[1]}))["worst"] in ("high", "medium")
                  for seed in range(200))
    assert flagged <= 10  # at most 5 %


def test_specification_limits_used_as_control_limits():
    r = check_series(subgroups(good(), {**SPEC, "lcl": 9.4, "ucl": 10.6}))
    assert codes(r) == ["limits_are_spec"] and r["worst"] == "high"
    assert r["findings"][0]["params"] == {"alarms": 0, "k": K} and r["findings"][0]["ref"] == "10.3.2.7"


def test_limits_too_wide_too_narrow_and_off_centre():
    wide = check_series(subgroups(good(), {**SPEC, "lcl": 9.7, "ucl": 10.3}))
    assert codes(wide) == ["limits_too_wide"] and wide["findings"][0]["params"]["ratio"] == pytest.approx(0.6 / (XBAR[1] - XBAR[0]), rel=0.15)
    narrow = check_series(subgroups(good(), {**SPEC, "lcl": 9.95, "ucl": 10.05}))
    assert codes(narrow) == ["limits_too_narrow"]            # the many alarms are part of that finding, not a second one
    off = check_series(subgroups(good(), {**SPEC, "lcl": XBAR[0] + 0.1, "ucl": XBAR[1] + 0.1}))
    assert "limits_off_centre" in codes(off)


def test_nested_sampling_is_one_finding_with_limits_that_hold_the_lot_variation():
    nested = rng().normal(10, 0.05, (K, N)) + rng(4).normal(0, 0.15, (K, 1))
    r = check_series(subgroups(nested, {**SPEC, "lcl": 10 - 3 * 0.05 / np.sqrt(N), "ucl": 10 + 3 * 0.05 / np.sqrt(N)}))
    assert codes(r, "high") == ["between_subgroups"] and "limits_too_narrow" not in codes(r) and "too_many_alarms" not in codes(r)
    p = r["findings"][0]["params"]
    assert p["ratio"] > 3 and p["share_between"] > 0.8 and p["model"].startswith("C") and p["alarms"] > 10
    ref = r["reference"]
    assert ref["method"] == "imr-of-means" and ref["ucl"] - ref["lcl"] > 4 * (6 * 0.05 / np.sqrt(N))   # far wider than the X-bar limits
    assert "index_from_unstable_process" in codes(r)
    # with limits that fit the lot-to-lot variation the limits in use are not criticised
    fit = check_series(subgroups(nested, {**SPEC, "lcl": ref["lcl"], "ucl": ref["ucl"]}))
    assert not {"limits_too_wide", "limits_too_narrow", "limits_off_centre"} & set(codes(fit))


def test_a_level_shift_is_a_change_of_the_process_not_a_fault_of_the_chart():
    m = good()
    m[40:] += 0.15
    r = check_series(subgroups(m, {**SPEC, "lcl": XBAR[0], "ucl": XBAR[1]}))
    assert "level_shift" in codes(r) and "between_subgroups" not in codes(r) and "too_many_alarms" not in codes(r)
    shift = next(f for f in r["findings"] if f["code"] == "level_shift")["params"]
    assert shift["at"] == "41" and shift["after"] - shift["before"] == pytest.approx(0.15, abs=0.03) and shift["points_after"] == 20
    assert r["reference"]["method"] == "xbar-after-shift" and "limits_off_centre" in codes(r)   # the old limits no longer fit
    x = rng().normal(5, 0.2, 150)
    x[90:] += 0.5
    assert "level_shift" in codes(check_series(individuals(x)))


def test_individuals_healthy_and_drifting():
    healthy = check_series(individuals(rng(5).normal(5, 0.2, 120), {"lsl": 4.0, "usl": 6.0}))
    assert codes(healthy) == [] and healthy["reference"]["method"] == "imr"
    flagged = sum(check_series(individuals(rng(seed).normal(5, 0.2, 120), {"lsl": 4.0, "usl": 6.0}))["worst"] in ("high", "medium") for seed in range(200))
    assert flagged <= 10  # at most 5 %
    drift = check_series(individuals(np.cumsum(rng().normal(0, 0.05, 150)) + 5))
    assert "individuals_not_independent" in codes(drift) and drift["layout"] == "individuals"


def test_alarms_and_their_actions():
    m = good()
    lim = {**SPEC, "lcl": 9.99, "ucl": 10.01}                       # limits far too narrow: many alarms
    none = np.zeros(K * N, dtype=bool)
    r = check_series(subgroups(m, lim, action=none))
    assert "alarms_without_action" in codes(r, "high")
    p = next(f for f in r["findings"] if f["code"] == "alarms_without_action")["params"]
    assert p["without"] == p["alarms"] == r["alarms"] and r["alarm_source"] == "limits"
    acted = np.ones(K * N, dtype=bool)
    assert "alarms_without_action" not in codes(check_series(subgroups(m, lim, action=acted)))
    # without a column of actions it cannot be checked: a note, not a finding
    assert "no_action_column" in codes(check_series(subgroups(m, lim)), "info") and "alarms_without_action" not in codes(check_series(subgroups(m, lim)))
    # the alarm flag of the other system counts when it is there
    flags = np.zeros(K * N, dtype=bool)
    flags[: 10 * N] = True
    by_file = check_series(subgroups(m, SPEC, alarm=flags, action=none))
    assert by_file["alarm_source"] == "file" and by_file["alarms"] == 10 and "too_many_alarms" in codes(by_file)


def test_capability():
    poor = check_series(subgroups(good(), {"lsl": 9.8, "usl": 10.2}))
    assert "capability_poor" in codes(poor, "high") and poor["index"]["name"] in ("Ppk", "Cpk")
    below = check_series(subgroups(good(), {"lsl": 9.65, "usl": 10.35}))
    assert "capability_below_target" in codes(below) and "capability_poor" not in codes(below)
    assert "capability_below_target" not in codes(check_series(subgroups(good(), {"lsl": 9.65, "usl": 10.35}), Settings(target_pk=1.0)))
    assert "no_specification" in codes(check_series(subgroups(good())), "info")


def test_measurement():
    coarse = check_series(subgroups(np.round(rng().normal(10, 0.04, (K, N)), 1), SPEC))
    assert {"resolution_vs_tolerance", "resolution_vs_variation"} <= set(codes(coarse)) and coarse["resolution"] == pytest.approx(0.1)
    assert "unstable" not in codes(coarse) and "repeated_values" not in codes(coarse)     # the coarse gauge explains them
    x = rng().normal(5, 0.2, 120)
    x[30:40] = x[30]
    assert "repeated_values" in codes(check_series(individuals(x)))


def test_structure_notes():
    few = check_series(individuals(rng().normal(5, 0.2, 12)))
    assert codes(few, "info") == ["too_few_data", "no_limits_in_file", "no_specification"] and few["worst"] == "ok" and few["reference"] == {}
    m = good()
    s = subgroups(m, SPEC)
    s.values, s.subgroup, s.lines = s.values[:-2], s.subgroup[:-2], s.lines[:-2]              # the last subgroup lost two values
    r = check_series(s)
    assert "subgroup_sizes_differ" in codes(r, "low") and r["k"] == K - 1 and r["dropped_subgroups"] == 1
    one = Series("x", m[:, 0], np.arange(K).astype(object), None, np.arange(K) + 2, None, None, {}, {})
    assert "subgroups_of_one" in codes(check_series(one), "low")
    assert "limits_incomplete" in codes(check_series(subgroups(m, {"ucl": 10.2})), "low")


def demo_series():
    nested = rng().normal(10, 0.05, (K, N)) + rng(4).normal(0, 0.15, (K, 1))
    return [subgroups(good(), {**SPEC, "lcl": XBAR[0], "ucl": XBAR[1]}, "healthy"),
            subgroups(good(), {**SPEC, "lcl": 9.4, "ucl": 10.6}, "spec <b>limits</b>"),
            subgroups(nested, {**SPEC, "lcl": 9.93, "ucl": 10.07}, "nested", action=np.zeros(K * N, dtype=bool)),
            individuals(rng().normal(5, 0.2, 12), name="few")]


def test_the_summary_counts_what_the_charts_can_and_cannot_do():
    res = check_all(demo_series())
    s = res["summary"]
    assert s["characteristics"] == 4 and s["by_worst"] == {"high": 2, "medium": 0, "low": 0, "ok": 2}
    assert s["blind"] == 1 and s["noisy"] == 1 and s["too_few_data"] == 1 and s["with_limits"] == 3
    assert s["alarms"] == s["alarms_without_action"] > 10 and s["action_records"] is True
    assert set(res["settings"]) == {f.name for f in fields(Settings)}
    json.dumps(res)  # the whole result is plain data


# ------------------------------------------------------------------ texts

def test_both_languages_have_the_same_keys_and_placeholders():
    zh, en = TEXTS["zh-TW"], TEXTS["en"]
    assert set(zh) == set(en)
    for key in zh:
        assert zh[key].strip() and en[key].strip(), key
        assert set(re.findall(r"\{(\w+)\}", zh[key])) == set(re.findall(r"\{(\w+)\}", en[key])), key


def test_every_finding_setting_and_method_has_its_texts():
    source = Path(checks_module.__file__).read_text(encoding="utf-8")
    found = set(re.findall(r'add\("(\w+)"', source))
    assert len(found) >= 20 and set(REFS) <= found
    for lang in LANGUAGES:
        table = TEXTS[lang]
        for code in found:
            assert {f"f.{code}", f"f.{code}.why", f"f.{code}.do"} <= set(table), code
        assert {f"set.{f.name}" for f in fields(Settings)} <= set(table)
        for method in ("xbar-s", "xbar-r", "imr", "imr-of-means", "xbar-after-shift", "imr-after-shift"):
            assert f"method.{method}" in table


def test_the_text_of_every_finding_can_be_filled_in():
    for lang in LANGUAGES:
        for r in check_all(demo_series())["results"] + [check_series(individuals(np.cumsum(rng().normal(0, 0.05, 150)) + 5, {"lsl": 4, "usl": 6}))]:
            for f in r["findings"]:
                title, why, do = finding_text(f, T(lang))
                assert title and "{" not in why and "{" not in do


# ------------------------------------------------------------------ report

def test_the_report_is_one_safe_file_in_either_language():
    res = check_all(demo_series())
    info = {"name": "export.csv", "sha256": "ab" * 32, "rows": 1234}
    zh = render_html(res, info, "zh-TW", made="2026-11-10 08:00 UTC")
    en = render_html(res, info, "en", made="2026-11-10 08:00 UTC")
    assert "SPC 健檢報告" in zh and "資料沒有傳送到任何地方" in zh and "管制界限就是規格界限" in zh and "ab" * 32 in zh
    assert "SPC health check" in en and "The control limits are the specification limits" in en
    for html in (zh, en):
        assert "<script" not in html.lower() and "http://www.w3.org/2000/svg" in html and "src=" not in html and "href=" not in html
        assert "spec &lt;b&gt;limits&lt;/b&gt;" in html and "<b>limits</b>" not in html        # names from the file are escaped
        assert html.count("<svg") == 4   # one chart per characteristic
    assert render_html(res, info, "zh-TW", made="x") == render_html(res, info, "zh-TW", made="x")   # the same input gives the same report
    with pytest.raises(ValueError):
        render_html(res, info, "de")


def test_every_setting_is_printed_in_the_report():
    res = check_all(demo_series(), Settings(target_pk=1.67))
    html = render_html(res, {"name": "f", "sha256": "", "rows": 1}, "en")
    for f in fields(Settings):
        assert TEXTS["en"][f"set.{f.name}"].replace("'", "&#x27;") in html, f.name
    assert ">1.67<" in html


def test_summary_text_and_json():
    res = check_all(demo_series())
    text = summary_text(res, "zh-TW")
    assert text.startswith("- 共檢查 4 個特性") and "看不到製程變化" in text and "沒有處置紀錄" in text
    clean = summary_text(check_all(demo_series()[:1]), "en")
    assert "No problem to deal with was found." in clean
    data = json.loads(to_json(res, {"name": "f", "sha256": "", "rows": 1}))
    assert data["program"] == "spc-check" and len(data["results"]) == 4


# ------------------------------------------------------------------ command line

def write_demo(path):
    rows = ["特性,子組,量測值,LSL,USL,LCL,UCL,處置"]
    m = good()
    for i in range(K):
        for j in range(N):
            rows.append(f"銅厚,{i + 1},{m[i, j]:.4f},9.4,10.6,9.4,10.6,")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8-sig")


def test_command_line(tmp_path, capsys):
    f = tmp_path / "export.csv"
    write_demo(f)
    assert main([str(f), "--columns"]) == 0
    assert capsys.readouterr().out.split() == ["特性", "子組", "量測值", "LSL", "USL", "LCL", "UCL", "處置"]
    args = [str(f), "--value", "量測值", "--characteristic", "特性", "--subgroup", "子組", "--lsl-col", "LSL", "--usl-col", "USL",
            "--lcl-col", "LCL", "--ucl-col", "UCL", "--action", "處置"]
    assert main(args + ["--json", str(tmp_path / "r.json")]) == 0
    out = capsys.readouterr().out
    report = tmp_path / "export.csv.spc-check.html"
    assert "看不到製程變化" in out and str(report) in out and "管制界限就是規格界限" in report.read_text(encoding="utf-8")
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["file"]["name"] == "export.csv"
    assert main(args + ["--lang", "en", "--out", str(tmp_path / "en.html")]) == 0
    assert "SPC health check" in (tmp_path / "en.html").read_text(encoding="utf-8")


def test_command_line_reports_problems_and_returns_2(tmp_path, capsys):
    f = tmp_path / "bad.csv"
    f.write_text("v\n1.0\nabc\n", encoding="utf-8")
    assert main([str(f), "--value", "v"]) == 2
    assert "line 3" in capsys.readouterr().err
    assert main([str(f)]) == 2 and "--value" in capsys.readouterr().err
    assert main([str(tmp_path / "missing.csv"), "--value", "v"]) == 2
    assert main([str(f), "--value", "v", "--lsl-col", "v", "--lsl", "1"]) == 2

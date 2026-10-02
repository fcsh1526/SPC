import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from spc.data import Dataset
from spc.report import ReportError, ReportMeta, generate, reproduce, verify_archive
from spc.report import svg
from spc.report.archive import build_archive, canonical, dataset_from_dict, dataset_to_dict
from spc.report.texts import EN, LANGUAGES, TEXTS, ZH
from spc.service import AnalysisRequest, analyze

NOW = "2026-10-02T09:00:00+00:00"
SRC = Path(__file__).resolve().parent.parent / "src" / "spc" / "report"


def make_data(k=25, n=5, seed=42, drift=0.0, spike=None):
    rng = np.random.default_rng(seed)
    v = 10 + np.linspace(0, drift, k * n) + rng.normal(0, 0.1, k * n)
    if spike is not None:
        v[spike] += 1.2
    stamps = (np.datetime64("2026-10-02T08:00:00") + np.arange(k * n) * np.timedelta64(120, "s")).astype("datetime64[s]")
    return Dataset.from_values(
        v, subgroup=np.repeat([f"B{i + 1:02d}" for i in range(k)], n), timestamp=stamps, tags={"machine": ["M1"] * (k * n)}
    )


REQ = AnalysisRequest(lsl=9.5, usl=10.5, model="A1", characteristic_class="major")
META = ReportMeta(
    process="turning", machine="CNC-07", site="plant 1", process_ref="P-120", machine_ref="M-07", persons="A. Chen",
    part_name="shaft", part_number="DS-1024", characteristic="outer diameter", unit="mm", target=10.0,
    technical_conditions="lot L2310", deviations="insert changed at group 12", uncertainty=0.0211, coverage_factor=2.0,
    recommendations="keep sampling",
)


def text_of(html: str) -> str:
    return re.sub(r"<style.*?</style>|<svg.*?</svg>|<[^>]+>", " ", html, flags=re.S)


# ------------------------------------------------------------------ figures

def test_all_figures_are_valid_xml_with_the_expected_size():
    rng = np.random.default_rng(1)
    x = rng.normal(10, 0.1, 125)
    labels = {k: k for k in ("title", "x", "y", "target", "bars", "fit", "spec", "used", "invalid", "unused", "data",
                             "ucl", "cl", "lcl", "point", "alarm")}
    labels["title"] = 'T <&> "q"'
    part = {"values": list(x[:25]), "labels": [str(i) for i in range(25)], "lcl": 9.8, "center": 10.0, "ucl": 10.2,
            "alarms": [{"index": 3, "rule": "beyond_limits"}]}
    figures = {
        "hist": (svg.histogram(x, 9.5, 10.5, 10.0, x.mean(), x.std(ddof=1), labels), (430, 300)),
        "run": (svg.run_chart(x, np.r_[np.zeros(120), np.ones(3), np.full(2, 2)], 9.5, 10.5, 10.0, labels), (640, 260)),
        "prob": (svg.probability_plot(x, x.mean(), x.std(ddof=1), 9.5, 10.5, labels), (430, 300)),
        "control": (svg.control_chart(part, labels), (640, 250)),
    }
    for name, (text, (w, h)) in figures.items():
        root = ET.fromstring(text)  # raises when the SVG is broken
        assert root.get("viewBox") == f"0 0 {w} {h}", name  # catches a size argument that got shadowed
        assert "&lt;&amp;&gt;" in text and "<&>" not in text, name  # the title is escaped


def test_nice_ticks_are_round_and_inside_the_range():
    ticks, decimals = svg.nice_ticks(9.874, 10.116)
    assert all(9.874 <= t <= 10.116 for t in ticks) and decimals == 2
    assert svg.nice_ticks(0.0005, 0.1764)[1] >= 2
    ticks, decimals = svg.nice_ticks(0, 1000)
    assert decimals == 0 and ticks[0] == 0


# ------------------------------------------------------------------ texts

def test_report_texts_have_the_same_keys_and_placeholders():
    assert set(EN) == set(ZH)
    for key in EN:
        assert set(re.findall(r"\{(\w+)\}", EN[key])) == set(re.findall(r"\{(\w+)\}", ZH[key])), key
        assert EN[key].strip() and ZH[key].strip(), key
    assert set(TEXTS) == set(LANGUAGES)


def test_every_text_key_used_by_the_code_exists():
    code = (SRC / "builder.py").read_text(encoding="utf-8") + (SRC / "render.py").read_text(encoding="utf-8")
    literal = set(re.findall(r"""\bL\(\s*["']([\w.\-]+)["']""", code)) | set(re.findall(r"""\bT\(lang,\s*["']([\w.\-]+)["']""", code))
    literal = {k for k in literal if not k.endswith(("_", "."))}  # prefixes of dynamic keys are checked below
    assert literal
    assert sorted(k for k in literal if k not in EN) == []
    dynamic = (
        [f"el.{n}" for n in range(1, 23)]
        + [f"v.class_{c}" for c in ("unknown", "statistical_control", "in_control", "unstable", "not_tested")]
        + [f"c.stability_{c}" for c in ("statistical_control", "in_control", "unstable", "unknown", "not_tested")]
        + [f"v.verdict_{c}" for c in ("meets", "meets_estimate_only", "fails", "none")]
        + [f"v.chart_{c}" for c in ("xbar-s", "xbar-r", "imr")]
        + [f"v.model_{c}" for c in ("unknown", "A1", "A2", "B", "C1", "C2", "C3", "C4", "D")]
        + [f"v.class_{c}" for c in ("critical", "major", "minor", "others")]
        + [f"v.rule_{c}" for c in ("beyond_limits", "run", "trend", "middle_third", "two_of_three_beyond_2s",
                                   "four_of_five_beyond_1s", "fifteen_within_1s")]
        + [f"v.mode_{c}" for c in ("random_range", "strict")]
        + [f"doc.stage_{c}" for c in ("machine", "preliminary", "production")]
        + ["fig.series.mean", "fig.series.individual", "fig.series.s", "fig.series.r", "fig.series.mr"]
    )
    assert sorted(k for k in dynamic if k not in EN) == []


# ------------------------------------------------------------------ the report itself

@pytest.fixture(scope="module")
def good():
    ds = make_data(spike=58).mark_invalid([58], "typed 1.2 too much", "A. Chen", at="2026-10-02T08:30:00+00:00")
    return ds, generate(ds, REQ, META, "en", now=NOW, report_id="r-good")


def test_all_22_elements_are_present(good):
    html = good[1].html
    for n in list(range(1, 11)) + list(range(15, 23)):
        assert re.search(rf'<span class="no">{n}</span>', html), f"element {n}"
    assert 'class="no">11–14</span>' in html
    for n in (11, 12, 13, 14):
        assert f"{n}. " in html
    assert html.count("<svg") == 5  # histogram, run chart, probability plot, two control charts
    assert "Annex A" in html and "Annex B" in html


def test_numbers_in_the_report_equal_the_analysis(good):
    ds, g = good
    r = analyze(ds, REQ)
    text = text_of(g.html)
    ix = r["indices"]
    assert f"{ix['pk']:.2f}" in text and f"{ix['p']:.2f}" in text
    assert f"{ix['ci_pk'][0]:.2f} – {ix['ci_pk'][1]:.2f}" in text
    assert f"{r['targets']['pk']:.2f}" in text
    assert f"{ix['ppm']:.2f} ppm" in text
    assert "Cpk.G" in text and "Cp.G" in text
    assert str(r["counts"]["n_used"]) in text


def test_within_subgroup_indices_are_never_in_the_report(good):
    text = text_of(good[1].html) + text_of(generate(good[0], REQ, META, "zh-TW", now=NOW, report_id="z").html)
    assert not re.search(r"\bCw\b|\bCwk\b", text)


def test_report_has_no_script_and_no_external_resource(good):
    html = good[1].html
    assert "<script" not in html.lower() and "javascript:" not in html.lower()
    assert not re.search(r'(src|href)="https?:', html)


def test_user_text_is_escaped_everywhere():
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    ds = make_data().mark_invalid([3], evil, evil)
    meta = ReportMeta(process=evil, machine=evil, persons=evil, technical_conditions=evil, deviations=evil,
                      recommendations=evil, characteristic=evil, unit=evil, part_name=evil)
    html = generate(ds, REQ, meta, "en", now=NOW, report_id="x").html
    assert "<script" not in html.lower() and "<img" not in html.lower()
    assert "&lt;script&gt;" in html and html.count("&lt;script&gt;") >= 8


def test_marked_values_are_listed_with_reason_person_and_time(good):
    html = good[1].html
    assert "typed 1.2 too much" in html and "A. Chen" in html and "2026-10-02T08:30:00+00:00" in html
    assert "Subgroups left out: B12" in text_of(html)


def test_guard_band_follows_the_draft_example(good):
    # Draft example: U = 0.0211, k = 2 gives u = 0.01055 and a guard band of about 0.0174.
    text = text_of(good[1].html)
    assert "0.01055" in text
    assert re.search(r"1\.645 \* u", text)
    assert "9.51735" in text and "10.4826" in text  # LSL + g and USL - g


def test_time_data_fills_elements_4_and_10(good):
    text = text_of(good[1].html)
    assert "2026-10-02 08:00:00" in text and "04:08:00" in text  # 124 steps of 2 min
    assert "10.0 min" in text  # five values of 2 min per subgroup


def test_both_languages(good):
    zh = generate(good[0], REQ, META, "zh-TW", now=NOW, report_id="r-good").html
    assert "製程研究報告" in zh and "Process study report" not in zh
    assert 'lang="zh-TW"' in zh and 'lang="en"' in good[1].html
    with pytest.raises(ValueError):
        generate(good[0], REQ, META, "fr")


def test_same_input_gives_the_same_report(good):
    again = generate(good[0], REQ, META, "en", now=NOW, report_id="r-good")
    assert again.html == good[1].html
    assert again.archive == good[1].archive


def test_report_names_the_archive(good):
    assert good[1].archive["integrity"]["digest"] in good[1].html


def test_conclusions_for_a_stable_process_that_meets_the_targets(good):
    text = text_of(good[1].html)
    assert "meets the target" in text and "Overall: all requirements are met" in text
    assert "Cp.G and Cpk.G: stability is proven" in text
    assert "keep sampling" in text


def test_conclusions_for_an_unstable_process_below_target():
    ds = make_data(drift=0.6, seed=3)
    req = AnalysisRequest(lsl=9.6, usl=10.6, model="A1", characteristic_class="critical",
                          rules={"run_length": 7, "trend_length": 7})
    g = generate(ds, req, META, "en", now=NOW, report_id="bad")
    text = text_of(g.html)
    assert "Pp.G" in text and "Ppk.G" in text and "Cpk.G" not in text
    assert "does not reach the target" in text and "not all requirements are met" in text
    assert "stability requirements are not met" in text.lower()
    assert re.search(r"alarm point", text)


def test_machine_study_has_no_control_chart_and_no_stability_claim():
    rng = np.random.default_rng(8)
    ds = Dataset.from_values(rng.normal(10, 0.05, 50))
    g = generate(ds, AnalysisRequest(stage="machine", lsl=9.5, usl=10.5, characteristic_class="major"), META, "en",
                 now=NOW, report_id="m")
    text = text_of(g.html)
    assert "Pm.G" in text and "Pmk.G" in text
    assert g.html.count("<svg") == 3
    assert "does not test stability" in text
    assert "Stability was not tested" in text


def test_small_machine_sample_and_blocked_critical_target():
    rng = np.random.default_rng(9)
    ds = Dataset.from_values(rng.normal(10, 0.05, 30))
    major = generate(ds, AnalysisRequest(stage="machine", lsl=9.5, usl=10.5, characteristic_class="major"), META, "en", now=NOW, report_id="a")
    assert "≥ 1.96" in text_of(major.html) and "below the base sample size 50" in text_of(major.html)
    crit = generate(ds, AnalysisRequest(stage="machine", lsl=9.5, usl=10.5, characteristic_class="critical"), META, "en", now=NOW, report_id="b")
    assert "No reduced target exists" in text_of(crit.html)


def test_report_without_targets_or_guard_band():
    g = generate(make_data(), AnalysisRequest(lsl=9.5, usl=10.5), ReportMeta(), "en", now=NOW, report_id="n")
    text = text_of(g.html)
    assert "No target values were given" in text
    assert text.count("not given") >= 8  # empty fields say so instead of staying blank


def test_one_sided_specification():
    g = generate(make_data(), AnalysisRequest(usl=10.5), META, "en", now=NOW, report_id="o")
    text = text_of(g.html)
    assert "One-sided specification" in text and "Above USL" in text and "Below LSL" not in text


def test_report_needs_a_specification_limit():
    with pytest.raises(ReportError) as err:
        generate(make_data(), AnalysisRequest(), META, "en")
    assert err.value.code == "spec_missing"


def test_meta_is_validated():
    with pytest.raises(ValueError):
        generate(make_data(), REQ, ReportMeta(process="x" * 2001), "en")
    with pytest.raises(ValueError):
        ReportMeta(uncertainty=-1).validate()
    with pytest.raises(ValueError):
        ReportMeta(coverage_factor=0.5).validate()


# ------------------------------------------------------------------ archive

def test_dataset_survives_the_archive_round_trip():
    ds = make_data().mark_invalid([3], "wrong part", "A", at="2026-10-02T08:00:00+00:00")
    back = dataset_from_dict(json.loads(json.dumps(dataset_to_dict(ds))))
    assert np.array_equal(back.values, ds.values) and np.array_equal(back.subgroup, ds.subgroup)
    assert np.array_equal(back.timestamp, ds.timestamp) and back.tags["machine"].tolist() == ds.tags["machine"].tolist()
    assert back.invalid_info() == ds.invalid_info() and back.log == ds.log


def test_archive_is_verified_and_reproduced(good):
    archive = good[1].archive
    assert archive["format"] == "spc-archive" and archive["integrity"]["algorithm"] == "sha256"
    json.dumps(archive, allow_nan=False)
    assert verify_archive(archive)
    r = reproduce(json.loads(json.dumps(archive)))  # as if read back from a file
    assert r.integrity_ok and r.reproduced and r.same_engine_version and r.differences == ()


@pytest.mark.parametrize(
    "tamper",
    [
        lambda a: a["dataset"]["values"].__setitem__(0, a["dataset"]["values"][0] + 1e-9),
        lambda a: a["dataset"]["log"][0].__setitem__("reason", "changed"),
        lambda a: a["request"].__setitem__("usl", 11.0),
        lambda a: a["report_meta"].__setitem__("persons", "someone else"),
        lambda a: a["result"]["indices"].__setitem__("pk", 9.99),
        lambda a: a.pop("integrity"),
    ],
)
def test_any_change_to_the_archive_is_noticed(good, tamper):
    archive = json.loads(json.dumps(good[1].archive))
    assert verify_archive(archive)
    tamper(archive)
    assert not verify_archive(archive)


def test_reproduction_finds_a_result_that_does_not_follow_from_the_data(good):
    import hashlib

    archive = json.loads(json.dumps(good[1].archive))
    archive["result"]["indices"]["pk"] = 9.99
    body = {k: v for k, v in archive.items() if k != "integrity"}
    archive["integrity"]["digest"] = hashlib.sha256(canonical(body)).hexdigest()  # re-signed on purpose
    r = reproduce(archive)
    assert r.integrity_ok and not r.reproduced
    assert any("indices.pk" in d for d in r.differences)


def test_archive_of_non_json_input_is_not_valid():
    assert not verify_archive({})
    assert not verify_archive({"format": "other", "integrity": {"algorithm": "sha256", "digest": "x"}})

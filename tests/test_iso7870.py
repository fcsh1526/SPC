import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from spc.core import estimators as E
from spc.core import multistate as ms
from spc.core.charts.variable import imr, median_r, xbar_r, xbar_s
from spc.data import Dataset
from spc.service import AnalysisRequest, analyze
from tests.conftest import logged_in_client, make_app

ROOT = Path(__file__).resolve().parent.parent


def sub(seed=1, k=40, n=5):
    return np.random.default_rng(seed).normal(10, 0.2, (k, n))


def test_the_factors_of_iso_7870_2_are_met_by_the_limits():
    x = sub()
    s = x.std(axis=1, ddof=1).mean()
    r = np.ptp(x, axis=1).mean()
    c = xbar_s(x, method="iso7870")
    # table 1 of ISO 7870-2 for n = 5: A3 = 1,427, B3 = 0, B4 = 2,089
    assert (c.location.ucl - c.location.center) / s == pytest.approx(1.427, rel=5e-4)
    assert c.variation.lcl == 0.0 and c.variation.ucl / s == pytest.approx(2.089, rel=5e-4)
    cr = xbar_r(x, method="iso7870")  # A2 = 0,577, D3 = 0, D4 = 2,114
    assert (cr.location.ucl - cr.location.center) / r == pytest.approx(0.577, rel=1e-3)
    assert cr.variation.lcl == 0.0 and cr.variation.ucl / r == pytest.approx(2.114, rel=1e-3)
    cm = median_r(x, method="iso7870")  # the tabulated factor of the median chart: 0,691
    assert (cm.location.ucl - cm.location.center) / r == pytest.approx(0.691, rel=2e-3)
    v = x.ravel()
    ci = imr(v, method="iso7870")  # table 3: 2,660 and 3,267
    mr = np.abs(np.diff(v)).mean()
    assert (ci.location.ucl - ci.location.center) / mr == pytest.approx(2.660, rel=1e-3)
    assert ci.variation.ucl / mr == pytest.approx(3.267, rel=1e-3)


def test_the_draft_method_is_the_default_and_unchanged():
    x = sub()
    a, b = xbar_s(x), xbar_s(x, method="draft")
    assert a.location == b.location and a.variation == b.variation
    assert xbar_s(x, method="iso7870").variation.ucl != a.variation.ucl
    for fn in (xbar_s, xbar_r, median_r, imr):
        with pytest.raises(ValueError):
            fn(x if fn is not imr else x.ravel(), method="nonsense")


def test_the_analysis_takes_the_method_and_records_it_only_when_it_is_not_the_default():
    ds = Dataset.from_values(sub().ravel(), subgroup=[str(i // 5) for i in range(200)])
    d = analyze(ds, AnalysisRequest(lsl=9.0, usl=11.0))
    i = analyze(ds, AnalysisRequest(lsl=9.0, usl=11.0, limit_method="iso7870"))
    assert "limit_method" not in d["params"] and i["params"]["limit_method"] == "iso7870"  # archives made before the setting stay reproducible
    assert d["params"]["fingerprint"] != i["params"]["fingerprint"]
    assert i["chart"]["variation"]["ucl"] != d["chart"]["variation"]["ucl"] and i["indices"]["p"] == d["indices"]["p"]
    one = Dataset.from_values(sub().ravel())
    with pytest.raises(ValueError):
        analyze(one, AnalysisRequest(chart="imr", subgroup_size=1, lsl=9.0, usl=11.0, limit_method="iso7870", moving_n=3))


def test_the_method_goes_through_the_api_the_profile_and_the_archive():
    from spc.report import generate, reproduce

    app = make_app()
    c = logged_in_client(app, "admin")
    key = app.state.store.add(Dataset.from_values(sub().ravel(), subgroup=[str(i // 5) for i in range(200)]), 1, "iso")
    r = c.post(f"/api/datasets/{key}/analyze", json={"lsl": 9, "usl": 11, "limit_method": "iso7870"})
    assert r.status_code == 200 and r.json()["params"]["limit_method"] == "iso7870"
    assert c.post(f"/api/datasets/{key}/analyze", json={"lsl": 9, "usl": 11, "limit_method": "other"}).status_code == 422
    p = c.post("/api/profiles", json={"name": "Customer ISO", "analysis": {"limit_method": "iso7870"}})
    assert p.status_code == 200, p.text
    pid = p.json()["id"]
    r2 = c.post(f"/api/datasets/{key}/analyze", json={"lsl": 9, "usl": 11, "profile_id": pid})
    assert r2.json()["params"]["limit_method"] == "iso7870"
    assert c.post("/api/profiles", json={"name": "Bad", "analysis": {"limit_method": "x"}}).status_code in (400, 422)
    g = generate(Dataset.from_values(sub().ravel(), subgroup=[str(i // 5) for i in range(200)]), AnalysisRequest(stage="preliminary", lsl=9.0, usl=11.0, limit_method="iso7870"), language="en",
                 now="2026-01-01T00:00:00+00:00", report_id="iso")
    assert reproduce(g.archive).reproduced and g.archive["request"]["limit_method"] == "iso7870"


def test_the_estimators_of_iso_22514_2():
    x = sub(3, 30, 5)
    e = E.estimators(x)
    flat = x.ravel()
    assert e["l1"] == pytest.approx(flat.mean()) and e["l2"] == pytest.approx(np.median(flat)) and e["l3"] == pytest.approx(x.mean(axis=1).mean())
    assert e["l4"] == pytest.approx(np.median(x, axis=1).mean())
    assert e["d2"] == pytest.approx(np.sqrt(x.var(axis=1, ddof=1).mean())) and e["d5"] == pytest.approx(flat.std(ddof=1))
    assert e["d3"] == pytest.approx(x.std(axis=1, ddof=1).mean() / 0.94, rel=2e-4) and e["d4"] == pytest.approx(np.ptp(x, axis=1).mean() / 2.326, rel=1e-4)
    assert e["d1"] == pytest.approx(6 * e["d5"], rel=1e-4)  # the 99,73 % interval of the normal distribution is 6 sigma wide
    assert e["Rm"] == pytest.approx(np.abs(np.diff(flat)).mean()) and e["Rtotal"] == pytest.approx(np.ptp(flat))


def test_type_1_capability_of_a_multi_state_process():
    states = {"a": [10.0, 10.2, 9.8, 10.1], "b": [12.0, 12.1, 11.9, 12.2]}
    t = ms.type1_capability(states, 8.0, 15.0)
    assert t["pm"] == pytest.approx((t["pmk_l"] + t["pmk_u"]) / 2) and t["pmk"] == min(t["pmk_l"], t["pmk_u"])
    assert t["pmk_l"] == pytest.approx((10.025 - 8.0) / (3 * t["sigma"])) and t["pmk_u"] == pytest.approx((15.0 - 12.05) / (3 * t["sigma"]))
    with pytest.raises(ValueError):
        ms.type1_capability({"a": [1.0, 2.0]}, 0, 3)


@pytest.mark.skipif(shutil.which("pdftotext") is None or not (ROOT / "docs" / "757270310-ISO-TR-11462-3-2020.pdf").exists(), reason="needs pdftotext and the PDF of the standard")
def test_the_data_file_is_what_the_extraction_tool_reads_from_the_pdf(tmp_path):
    out = tmp_path / "iso.json"
    subprocess.run([sys.executable, str(ROOT / "tools" / "extract_iso11462_3.py"), str(ROOT / "docs" / "757270310-ISO-TR-11462-3-2020.pdf"), str(out)], check=True, capture_output=True)
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads((ROOT / "src" / "spc" / "validation" / "data" / "iso_tr_11462_3.json").read_text(encoding="utf-8"))

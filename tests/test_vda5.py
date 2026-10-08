"""VDA 5, capability of measurement processes: the spread of the type 1 study (policy), the classified parts, and the examples of the guideline."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from spc.core import iso22514_7 as iso
from spc.core import msa
from spc.msa import gate
from spc.validation import vda5
from tests.conftest import logged_in_client, make_app
from tests.test_api import err


def test_the_examples_of_the_guideline_are_reproduced():
    bad = [c.id for c in vda5.scenarios() if c.level == "must" and not c.ok]
    assert bad == []
    assert len(vda5.scenarios()) >= 45


def test_the_type_1_study_spreads_4_s_as_vda_5_and_6_s_as_company_guidelines():
    x = np.random.default_rng(3).normal(0.0, 1.0, 50)
    x = (x - x.mean()) / x.std(ddof=1) * 0.0375  # Q_MS = 4 s / T = 15 % with T = 1
    four, six = msa.type1(x, 0.0, 1.0), msa.type1(x, 0.0, 1.0, spread=6)
    assert four["cg"] == pytest.approx(1.3333, abs=1e-3) and four["verdict"] == "pass" and four["spread"] == 4
    assert six["cg"] == pytest.approx(0.8889, abs=1e-3) and six["verdict"] == "fail"  # the same data against the stricter spread


def test_the_policy_holds_the_spread_and_the_system_uses_it():
    assert gate.validate_policy(None)["cg_spread"] == 4
    assert gate.validate_policy({"cg_spread": 6})["cg_spread"] == 6
    for bad in (5, True, "4", 0):
        with pytest.raises(ValueError):
            gate.validate_policy({"cg_spread": bad})
    eng = logged_in_client(make_app(), "eng")
    values = (np.random.default_rng(4).normal(0, 1, 50))
    values = ((values - values.mean()) / values.std(ddof=1) * 0.0375).tolist()
    out = {}
    for spread in (4, 6):
        r = eng.post("/api/msa", json={"record": {"name": f"spread {spread}", "characteristic": "bore", "unit": "mm", "kind": "variable", "resolution": 0.001, "tolerance": 1.0,
                                                  "policy": {"cg_spread": spread}}})
        assert r.status_code == 200, r.text
        sid = r.json()["system"]["id"]
        s = eng.post(f"/api/msa/{sid}/studies", json={"kind": "type1", "date": date.today().isoformat(), "input": {"values": values, "reference": 0.0}})
        assert s.status_code == 200, s.text
        out[spread] = s.json()["system"]["studies"][0]["result"]
    assert out[4]["cg"] == pytest.approx(1.3333, abs=1e-3) and out[6]["cg"] == pytest.approx(0.8889, abs=1e-3)


def test_classified_parts_follow_clause_8_2():
    ok = iso.classification(0.004, 0.01)
    assert ok["ok"] and ok["ratio"] == pytest.approx(0.4) and ok["adjacent_max"] == 2 and ok["adjacent"] == pytest.approx(1.8)
    edge = iso.classification(0.005, 0.01)
    assert edge["ok"] and edge["adjacent_max"] == 2 and edge["u_mp_max"] == pytest.approx(0.005)
    over = iso.classification(0.008, 0.01)
    assert not over["ok"] and over["adjacent_max"] == 3 and over["adjacent"] == pytest.approx(2.6)
    for bad in (lambda: iso.classification(-1, 1), lambda: iso.classification(1, 0)):
        with pytest.raises(msa.MsaError):
            bad()
    eng = logged_in_client(make_app(), "eng")
    r = eng.post("/api/msa-calc/classification", json={"u_mp": 0.008, "class_width": 0.01})
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["adjacent_max"] == 3
    assert eng.post("/api/msa-calc/classification", json={"u_mp": 0.008, "class_width": 0}).status_code == 422

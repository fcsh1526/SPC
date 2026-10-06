"""Draft 8.5: multi-stage machining (8.5.1) and GD&T with MMR/LMR (8.5.3)."""

import numpy as np
import pytest
from scipy import stats

from spc.core import gdt
from spc.core import multistage as ms
from tests.conftest import logged_in_client, make_app
from tests.test_api import csv_text, upload


@pytest.fixture
def client():
    return logged_in_client(make_app(max_upload=200_000))

# ------------------------------------------------------------------ 8.5.1 scope of inspection


def test_scope_reproduces_the_draft_example_2():
    # 3 identical machines and 7 pallets, one part per pallet: the draft counts 3 * 7 * 50 = 1050 parts for a complete acceptance
    s = ms.inspection_scope(1, 7, 1, 3)
    assert s["combinations"] == 21 and s["full_acceptance_parts"] == 1050
    assert s["parts_per_combination"] == 5 and s["total_parts"] == 105 and s["saving"] == 945
    assert s["minimum_total_binding"] is False


def test_scope_minimum_total_of_50_raises_the_parts_per_combination():
    s = ms.inspection_scope(1, 2, 2, 1)  # 4 combinations: 5 each gives 20, the total of 50 needs 13 each
    assert s["combinations"] == 4 and s["parts_per_combination"] == 13 and s["total_parts"] == 52
    assert s["minimum_total_binding"] is True
    one = ms.inspection_scope()  # a single combination
    assert one["parts_per_combination"] == 50 and one["total_parts"] == 50


def test_scope_identical_combinations_and_fewer_carriers():
    base = ms.inspection_scope(1, 7, 1, 3)
    waived = ms.inspection_scope(1, 7, 1, 3, geometrically_identical=6)
    assert waived["inspected_combinations"] == 15 and waived["total_parts"] == 75
    fewer = ms.inspection_scope(2, 7, 1, 3, measured_carriers=3)
    assert fewer["combinations"] == 42 and fewer["combinations_in_study"] == 18
    assert fewer["parts_in_study"] == 90 and fewer["parts_other_carriers"] == (7 - 3) * 2 * 5
    assert fewer["total_parts"] == 90 + 40 and base["total_parts"] == 105
    for bad in (dict(carriers=0), dict(machines=-1), dict(geometrically_identical=21), dict(measured_carriers=8)):
        with pytest.raises(ValueError):
            ms.inspection_scope(**{"components_per_carrier": 1, "carriers": 7, "spindles": 1, "machines": 3, **bad})


def test_form_parts_are_spread_evenly_over_the_spindles():
    d = ms.spindle_distribution(50, 3)
    assert d["per_spindle"] == [17, 17, 16] and sum(d["per_spindle"]) == 50


# ------------------------------------------------------------------ 8.5.1 analysis of the combinations


def shifted(seed=3, shift=0.25):
    rng = np.random.default_rng(seed)
    x = rng.normal(10, 0.1, 120)
    pallet = np.tile(["P1", "P2", "P3"], 40)
    spindle = np.repeat(["S1", "S2"], 60)
    x[pallet == "P3"] += shift
    return x, pallet, spindle


def test_a_deviating_pallet_is_found_and_the_numbers_match_independent_formulas():
    x, pallet, spindle = shifted()
    r = ms.analyse_combinations(x, {"pallet": pallet, "spindle": spindle}, 9.5, 10.5)
    assert r["n"] == 120 and r["mean"] == pytest.approx(x.mean()) and r["sd"] == pytest.approx(x.std(ddof=1))
    assert r["indices"]["pk"] == pytest.approx(min(10.5 - x.mean(), x.mean() - 9.5) / (3 * x.std(ddof=1)))
    groups = [x[(pallet == p) & (spindle == s)] for p in ("P1", "P2", "P3") for s in ("S1", "S2")]
    f, p = stats.f_oneway(*groups)
    assert r["between"]["anova"]["f"] == pytest.approx(f) and r["between"]["anova"]["p_value"] == pytest.approx(p)
    sst = ((x - x.mean()) ** 2).sum()
    ssb = sum(g.size * (g.mean() - x.mean()) ** 2 for g in groups)
    assert r["between"]["eta_squared"] == pytest.approx(ssb / sst)
    swithin = sum(((g - g.mean()) ** 2).sum() for g in groups)
    assert r["between"]["pooled_sd"] == pytest.approx(np.sqrt(swithin / (120 - 6)))
    lev = stats.levene(*groups, center="median")
    assert r["between"]["variances"]["p_value"] == pytest.approx(lev.pvalue)
    by = {(c["labels"]["pallet"], c["labels"]["spindle"]): c for c in r["combinations"]}
    g = x[(pallet == "P3") & (spindle == "S1")]
    rest = np.delete(x, np.where((pallet == "P3") & (spindle == "S1"))[0])
    assert by[("P3", "S1")]["p_value"] == pytest.approx(stats.ttest_ind(g, rest, equal_var=False).pvalue)
    assert all(by[("P3", s)]["different"] for s in ("S1", "S2"))
    assert r["bonferroni_alpha"] == pytest.approx(0.05 / 6)
    pal = next(f for f in r["by_factor"] if f["factor"] == "pallet")
    assert pal["different"] and pal["balanced"]
    assert next(f for f in r["by_factor"] if f["factor"] == "spindle")["different"] is False


def test_identical_combinations_are_not_flagged_and_the_coverage_is_complete():
    x, pallet, spindle = shifted(shift=0.0, seed=11)
    r = ms.analyse_combinations(x, {"pallet": pallet, "spindle": spindle}, 9.5, 10.5)
    assert r["between"]["anova"]["different"] is False
    assert r["coverage"]["complete"] and r["coverage"]["missing_count"] == 0 and r["coverage"]["expected_combinations"] == 6


def test_missing_and_short_combinations_and_too_few_parts_are_reported():
    x = np.array([10.0, 10.1, 9.9, 10.05, 10.02, 9.98, 10.01, 10.03, 9.97, 10.0, 10.2, 9.8])
    pallet = ["A"] * 5 + ["B"] * 5 + ["C"] * 2
    machine = ["m1"] * 5 + ["m2"] * 5 + ["m1"] * 2
    r = ms.analyse_combinations(x, {"pallet": pallet, "machine": machine}, expected={"machine": ["m1", "m2"]})
    cov = r["coverage"]
    assert cov["expected_combinations"] == 6 and cov["combinations"] == 3 and cov["missing_count"] == 3
    assert cov["short_count"] == 1 and cov["short"] == [{"pallet": "C", "machine": "m1"}]
    assert cov["enough_total"] is False and cov["complete"] is False
    assert r["indices"] is None


def test_analysis_refuses_bad_input():
    with pytest.raises(ValueError):
        ms.analyse_combinations([1, 2], {"a": ["x", "y"]})
    with pytest.raises(ValueError):
        ms.analyse_combinations([1, 2, 3, 4], {})
    with pytest.raises(ValueError):
        ms.analyse_combinations([1, 2, 3, 4], {"a": ["x", "y"]})
    with pytest.raises(ValueError):
        ms.analyse_combinations([1, 2, 3, 4], {"a": list("xyxy")}, lsl=5, usl=1)


# ------------------------------------------------------------------ 8.5.3 GD&T

BORE = dict(lower=20.0, upper=20.2, position_tolerance=0.2)
BORE_ARGS = (20.0, 20.2, 0.2)


def test_virtual_sizes_of_the_draft_example_and_the_other_cases():
    assert gdt.virtual_size("bore", "mmc", 20.0, 20.2, 0.2) == (20.0, pytest.approx(19.8))  # MMS = LD, MMVS = LD - TP
    assert gdt.virtual_size("bore", "lmc", 20.0, 20.2, 0.2) == (20.2, pytest.approx(20.4))
    assert gdt.virtual_size("pin", "mmc", 19.8, 20.0, 0.1) == (20.0, pytest.approx(20.1))
    assert gdt.virtual_size("pin", "lmc", 19.8, 20.0, 0.1) == (19.8, pytest.approx(19.7))
    with pytest.raises(ValueError):
        gdt.virtual_size("bore", "mmc", 20.2, 20.0, 0.2)
    with pytest.raises(ValueError):
        gdt.virtual_size("bore", "mmc", 20.0, 20.2, 0)


def test_position_deviation_is_the_diameter_of_the_circle():
    assert gdt.position_deviation(0.03, 0.04) == pytest.approx(0.1)  # 2 * sqrt(0.03^2 + 0.04^2)


@pytest.mark.parametrize("kind,req,lower,upper,tp", [("bore", "mmc", 20.0, 20.2, 0.2), ("bore", "lmc", 20.0, 20.2, 0.2),
                                                     ("pin", "mmc", 19.8, 20.0, 0.1), ("pin", "lmc", 19.8, 20.0, 0.1)])
def test_clearance_is_zero_exactly_at_the_bonus_boundary(kind, req, lower, upper, tp):
    # the position tolerance grows by the departure of the size from the material limit; C = 0 where x_P equals that allowance
    for xd in np.linspace(lower, upper, 6):
        limit = lower if (kind, req) in (("bore", "mmc"), ("pin", "lmc")) else upper
        bonus = abs(xd - limit)
        allowed = tp + bonus
        assert gdt.clearance(xd, allowed, kind, req, lower, upper, tp) == pytest.approx(0.0, abs=1e-12)
        assert gdt.clearance(xd, allowed - 0.01, kind, req, lower, upper, tp) > 0
        assert gdt.clearance(xd, allowed + 0.01, kind, req, lower, upper, tp) < 0


def test_the_sign_printed_in_the_draft_would_reward_a_position_error():
    # draft example: a bore at the minimum size 20.0 with the full position deviation 0.2 just assembles (C = 0)
    assert gdt.clearance(20.0, 0.2, "bore", "mmc", *BORE_ARGS) == pytest.approx(0.0, abs=1e-12)
    assert gdt.clearance(20.0, 0.0, "bore", "mmc", *BORE_ARGS) == pytest.approx(0.2)  # a perfect position leaves 0.2
    # the printed "+ x_P" would give 20.0 + 0.2 - 19.8 = 0.4 for the bad hole: better than the perfect one (0.2)
    assert 20.0 + 0.2 - 19.8 > 20.0 + 0.0 - 19.8


def parts(n=300, seed=5, mean_size=20.1, sd_size=0.03, sd_pos=0.03):
    rng = np.random.default_rng(seed)
    return rng.normal(mean_size, sd_size, n), rng.normal(0, sd_pos, n), rng.normal(0, sd_pos, n)


def test_clearance_capability_of_a_normal_clearance_matches_the_definition():
    xd, dx, dy = parts()
    r = gdt.analyse_clearance(xd, dx=dx, dy=dy, distribution="normal", bootstrap_n=0, **BORE)
    c = xd - gdt.position_deviation(dx, dy) - 19.8
    assert r["n"] == 300 and r["clearance"] == pytest.approx(list(c))
    mu, sd = c.mean(), c.std()
    q = stats.norm.ppf([0.00135, 0.5, 0.99865], mu, sd)
    assert r["quantiles"]["c_50"] == pytest.approx(q[1], rel=1e-9) and r["quantiles"]["c_0135"] == pytest.approx(q[0], rel=1e-6)
    assert r["indices"]["pk"] == pytest.approx((q[1] - 0) / (q[1] - q[0]), rel=1e-6)
    z = gdt.analyse_clearance(xd, dx=dx, dy=dy, distribution="normal", method="Z", bootstrap_n=0, **BORE)
    assert z["indices"]["pk"] == pytest.approx(mu / sd / 3, rel=1e-6)
    assert r["observed"]["negative"] == int(np.sum(c < 0)) and r["sign_note"] == "draft_prints_plus_for_position"


def test_a_part_that_does_not_assemble_is_counted_and_a_bad_process_has_a_low_index():
    xd, dx, dy = parts(mean_size=20.02, sd_pos=0.06)
    r = gdt.analyse_clearance(xd, dx=dx, dy=dy, bootstrap_n=0, **BORE)
    assert r["observed"]["negative"] > 0 and r["indices"]["pk"] < 1.0 and r["indices"]["ppm"] > 0


def test_auto_distribution_is_chosen_for_the_folded_position_and_the_bootstrap_is_repeatable():
    xd, dx, dy = parts(n=200)
    a = gdt.analyse_clearance(xd, dx=dx, dy=dy, bootstrap_n=40, **BORE)
    b = gdt.analyse_clearance(xd, dx=dx, dy=dy, bootstrap_n=40, **BORE)
    assert a["indices"]["ci_pk"] == b["indices"]["ci_pk"] and a["indices"]["ci_pk"] is not None
    assert a["distribution"]["name"] in ("normal", "weibull", "gamma", "lognormal", "johnson_su", "box_cox", "mixture")
    xp = gdt.position_deviation(dx, dy)
    c = gdt.analyse_clearance(xd, xp=xp, bootstrap_n=0, **BORE)  # xp given directly gives the same clearances
    assert c["clearance"] == pytest.approx(a["clearance"])


def test_gdt_input_is_checked():
    xd, dx, dy = parts(n=20)
    with pytest.raises(ValueError):
        gdt.analyse_clearance(xd, **BORE)  # neither xp nor dx, dy
    with pytest.raises(ValueError):
        gdt.analyse_clearance(xd, xp=-np.ones(20), **BORE)
    with pytest.raises(ValueError):
        gdt.analyse_clearance(xd, xp=np.zeros(19), **BORE)
    with pytest.raises(ValueError):
        gdt.analyse_clearance(xd[:4], dx=dx[:4], dy=dy[:4], **BORE)
    with pytest.raises(ValueError):
        gdt.analyse_clearance(xd, dx=dx, dy=dy, distribution="empirical", method="Z", **BORE)
    small = gdt.analyse_clearance(xd, dx=dx, dy=dy, bootstrap_n=0, distribution="normal", **BORE)
    assert any(w["code"] == "gdt_small_sample" for w in small["warnings"])


# ------------------------------------------------------------------ API


def test_scope_api(client):
    r = client.post("/api/multistage/scope", json={"carriers": 7, "machines": 3}).json()
    assert r["combinations"] == 21 and r["full_acceptance_parts"] == 1050 and r["total_parts"] == 105
    assert sum(r["spindle_form"]["per_spindle"]) == 50
    bad = client.post("/api/multistage/scope", json={"carriers": 2, "geometrically_identical": 5})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_input"
    assert client.post("/api/multistage/scope", json={"carriers": 0}).status_code == 422


def test_multistage_on_a_stored_dataset(client):
    ds = upload(client, csv_text(k=20, n=5, seed=2)).json()
    r = client.post(f"/api/datasets/{ds['id']}/multistage", json={"factors": ["machine"], "lsl": 9.5, "usl": 10.5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n"] == 100 and [c["labels"]["machine"] for c in body["combinations"]] == ["M1", "M2"]
    assert body["coverage"]["expected_combinations"] == 2 and body["indices"]["pk"] > 0
    sub = client.post(f"/api/datasets/{ds['id']}/multistage", json={"factors": ["machine", "subgroup"]}).json()
    assert sub["coverage"]["expected_combinations"] == 40
    missing = client.post(f"/api/datasets/{ds['id']}/multistage", json={"factors": ["nope"]})
    assert missing.status_code == 400 and "nope" in missing.json()["error"]["message"]
    assert client.post("/api/datasets/zzz/multistage", json={"factors": ["machine"]}).status_code == 404


def test_gdt_api(client):
    xd, dx, dy = parts(n=120)
    body = {"xd": xd.tolist(), "dx": dx.tolist(), "dy": dy.tolist(), "lower": 20.0, "upper": 20.2, "position_tolerance": 0.2, "bootstrap_n": 20}
    r = client.post("/api/gdt/clearance", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["virtual_size"] == pytest.approx(19.8) and out["n"] == 120 and out["indices"]["pk"] > 0
    assert client.post("/api/gdt/clearance", json={**body, "dx": None, "dy": None}).json()["error"]["code"] == "invalid_input"
    assert client.post("/api/gdt/clearance", json={**body, "kind": "plug"}).status_code == 422

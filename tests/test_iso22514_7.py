"""ISO 22514-7:2012, capability of measurement processes: every worked example and table of the standard, and the rules of the clauses."""

import math

import numpy as np
import pytest
from scipy import stats

from spc.core import iso22514_7 as iso
from spc.core.msa import MsaError

# Table 7 (7.1.3.4): the readings of 12 measurements on 5 standards (ISO 11095)
T7_X = [2.0, 4.0, 6.0, 8.0, 10.0]
T7_Y = [[2.7, 2.5, 2.4, 2.5, 2.7, 2.3, 2.5, 2.5, 2.4, 2.4, 2.6, 2.4],
        [5.1, 3.9, 4.2, 5.0, 3.8, 3.9, 3.9, 3.9, 3.9, 4.0, 4.1, 3.8],
        [5.8, 5.7, 5.9, 5.9, 6.0, 6.1, 6.0, 6.1, 6.4, 6.3, 6.0, 6.1],
        [7.6, 7.7, 7.8, 7.7, 7.8, 7.8, 7.8, 7.7, 7.8, 7.5, 7.6, 7.7],
        [9.1, 9.3, 9.5, 9.3, 9.4, 9.5, 9.5, 9.5, 9.6, 9.2, 9.3, 9.4]]
# Table A.1: 10 reference materials, K = 4 readings each (ISO 11095)
A1_X = [6.19, 9.17, 1.99, 7.77, 4.00, 10.77, 4.78, 2.99, 6.98, 9.98]
A1_Y = [[6.31, 6.27, 6.31, 6.28], [9.27, 9.21, 9.34, 9.23], [2.21, 2.19, 2.22, 2.20], [8.00, 7.81, 7.95, 7.84], [4.27, 4.15, 4.15, 4.15],
        [10.93, 10.73, 10.92, 10.89], [4.95, 4.87, 5.00, 5.00], [3.24, 3.17, 3.21, 3.21], [7.14, 7.07, 7.18, 7.20], [10.23, 10.02, 10.07, 10.17]]
# Table A.4: three operators, 10 parts, 3 measurements
A4 = [[[8.120, 8.435, 8.480], [7.445, 6.815, 7.490], [9.965, 10.010, 9.560], [6.140, 5.960, 6.365], [5.690, 5.600, 5.780], [2.855, 2.450, 2.585], [10.685, 10.595, 10.775],
       [6.725, 6.275, 6.545], [4.970, 5.105, 5.510], [9.875, 10.100, 9.875]],
      [[8.200, 8.290, 8.245], [7.300, 7.120, 7.075], [9.660, 9.340, 9.250], [6.095, 6.185, 6.185], [5.080, 5.340, 5.440], [2.315, 2.585, 2.315], [10.450, 10.840, 11.050],
       [6.240, 6.120, 6.300], [5.015, 5.285, 5.150], [10.080, 9.800, 9.970]],
      [[8.525, 8.435, 8.345], [7.535, 7.355, 7.085], [9.830, 9.695, 9.515], [6.140, 6.140, 6.050], [5.780, 5.735, 5.555], [2.630, 2.360, 2.585], [10.865, 11.000, 11.180],
       [6.590, 6.500, 6.725], [5.060, 5.195, 5.105], [10.190, 9.785, 9.965]]]


# ------------------------------------------------------------------ single components (Tables 1, 2, 3, 6)

def test_the_single_components_follow_the_tables():
    assert iso.u_mpe(0.3) == pytest.approx(0.3 / math.sqrt(3)) and iso.u_mpe(0.3, 0.4) == pytest.approx(math.sqrt(0.09 / 3 + 0.16 / 3))
    assert iso.u_resolution(5e-3) == pytest.approx(0.001443, abs=1e-6)  # A.3
    assert iso.u_calibration(0.01, 2.0) == pytest.approx(0.005) and iso.u_calibration(standard=0.005) == 0.005
    assert iso.u_object(0.02) == pytest.approx(0.02 / math.sqrt(3))
    t = iso.u_temperature(1.5, 11.5e-6, 100.0, 23.0, 1e-6)
    assert t["u_td"] == pytest.approx(1.5 * 11.5e-6 * 100 / math.sqrt(3)) and t["u_ta"] == pytest.approx(3.0 * 1e-6 * 100 / math.sqrt(3)) and t["u_t"] == pytest.approx(math.hypot(t["u_td"], t["u_ta"]))
    for bad in (lambda: iso.u_mpe(), lambda: iso.u_resolution(0), lambda: iso.u_calibration(1.0), lambda: iso.u_object(-1), lambda: iso.u_mpe(True)):
        with pytest.raises(MsaError):
            bad()


# ------------------------------------------------------------------ repeatability and bias on standards (7.1.2)

def test_repeatability_and_bias_on_one_standard():
    rng = np.random.default_rng(1)
    x = rng.normal(10.02, 0.05, 40)
    r = iso.repeatability_standard(x, 10.0)
    assert r["u_evr"] == pytest.approx(x.std(ddof=1)) and r["bias"] == pytest.approx(x.mean() - 10.0) and r["u_bi"] == pytest.approx(abs(x.mean() - 10.0) / math.sqrt(3))
    for bad in (lambda: iso.repeatability_standard(x[:29], 10.0), lambda: iso.repeatability_standard([1.0] * 30, 1.0), lambda: iso.repeatability_standard(list(x[:-1]) + [float("nan")], 10.0)):
        with pytest.raises(MsaError):
            bad()


def test_several_standards_use_the_largest_deviation_and_the_average_variance():
    rng = np.random.default_rng(2)
    refs = [9.0, 10.0, 11.0]
    y = [rng.normal(r + d, 0.05, 12) for r, d in zip(refs, (0.01, -0.04, 0.02))]
    r = iso.repeatability_standards(refs, y)
    assert r["bias_standard"] == 10.0 and r["bias"] == pytest.approx(np.mean(y[1]) - 10.0) and r["u_evr"] == pytest.approx(math.sqrt(np.mean([np.var(v, ddof=1) for v in y])))
    assert r["u_bi"] == pytest.approx(abs(r["bias"]) / math.sqrt(3))
    with pytest.raises(MsaError):
        iso.repeatability_standards([9.0], [list(y[0])])
    with pytest.raises(MsaError):
        iso.repeatability_standards(refs, [v[:5] for v in y])  # 15 measurements in all


# ------------------------------------------------------------------ linearity: the examples of 7.1.3 and A.1

def test_the_bias_line_of_table_7_and_the_linearity_of_table_8():
    r = iso.linearity_deviation(T7_X, T7_Y)
    assert r["intercept"] == pytest.approx(0.7367, abs=1e-4) and r["slope"] == pytest.approx(-0.1317, abs=1e-4)  # y = 0.7367 - 0.1317 x
    assert r["at"] == 10.0 and r["linearity"] == pytest.approx(0.58, abs=5e-3) and r["u_lin"] == pytest.approx(0.58 / math.sqrt(3), abs=3e-3)
    assert iso.linearity_deviation(T7_X, T7_Y, at=4.0)["linearity"] == pytest.approx(abs(0.7367 - 0.1317 * 4), abs=1e-3)
    with pytest.raises(MsaError):
        iso.linearity_deviation(T7_X[:2], T7_Y[:2])


def test_the_linearity_analysis_of_variance_reproduces_a_1_and_table_a_3():
    r = iso.linearity(A1_X, A1_Y)
    assert (r["x_mean"], r["y_mean"]) == pytest.approx((6.462, 6.614), abs=1e-9)
    assert (r["beta0"], r["beta1"]) == pytest.approx((0.2358, 0.9870), abs=1e-4)
    assert (r["ss_e"], r["ss_evr"], r["ss_lin"]) == pytest.approx((0.1462, 0.1234, 0.0228), abs=1e-4) and r["ss_evr"] == pytest.approx(0.12345, abs=1e-9)
    assert (r["df_lin"], r["df_evr"]) == (8, 30)
    assert (r["ms_lin"], r["ms_evr"]) == pytest.approx((0.0028, 0.0041), abs=5e-5)
    assert r["u_lin"] == pytest.approx(0.0533, abs=1e-4) and r["u_evr"] == pytest.approx(0.0641, abs=1e-4)
    assert r["f"] == pytest.approx(0.6918, abs=5e-5) and r["f_crit"] == pytest.approx(2.2661, abs=5e-4) and r["lack_of_fit"] is False
    assert r["residual_sd"] == pytest.approx(math.sqrt(r["ss_e"] / 38))
    assert r["sd_constant"] and r["sd_test_p"] == pytest.approx(stats.bartlett(*A1_Y).pvalue)  # the precondition of 7.1.3.2 holds in A.1
    assert not iso.linearity(T7_X, T7_Y)["sd_constant"]  # Table 7: the standard deviations are 0.12, 0.45, 0.20, 0.10 and 0.15
    # the correction of a reading with the regression function
    assert iso.correct_reading(0.2358 + 0.987 * 5.0, r["beta0"], r["beta1"]) == pytest.approx(5.0, abs=1e-3)


def test_a_lack_of_fit_is_found_and_the_study_is_checked():
    x = np.repeat([1.0, 2.0, 3.0, 4.0, 5.0], 6)
    y = (x + 0.3 * (x - 3) ** 2).reshape(5, 6) + np.random.default_rng(3).normal(0, 0.02, (5, 6))  # a curve
    r = iso.linearity([1, 2, 3, 4, 5], y)
    assert r["lack_of_fit"] and r["f"] > r["f_crit"]
    for bad in (lambda: iso.linearity([1, 2], y[:2]), lambda: iso.linearity([1, 2, 3, 4, 5], y[:, :2]), lambda: iso.linearity([1, 2, 3, 4, 5], y[:, :4]),
                lambda: iso.linearity([1, 1, 3, 4, 5], y), lambda: iso.linearity([1, 2, 3, 4, 5], np.ones((5, 6))), lambda: iso.linearity([1, 2, 3, 4, 5], y, alpha=0.7)):
        with pytest.raises(MsaError):
            bad()


# ------------------------------------------------------------------ the process: repeatability and reproducibility (A.2, Tables A.5 and A.6)

def test_the_analysis_of_variance_of_table_a_5():
    r = iso.reproducibility(A4)
    t = {row["source"]: row for row in r["table"]}
    assert (t["operator"]["df"], t["part"]["df"], t["interaction"]["df"], t["error"]["df"]) == (2, 9, 18, 60)
    assert (t["operator"]["ss"], t["part"]["ss"], t["interaction"]["ss"], t["error"]["ss"]) == pytest.approx((0.519, 526.9, 0.686, 1.917), abs=0.03)
    assert (t["operator"]["ms"], t["part"]["ms"], t["interaction"]["ms"], t["error"]["ms"]) == pytest.approx((0.260, 58.54, 0.0381, 0.0320), abs=3e-3)
    # the interaction is not significant (F = 1.193 < 1.778): it is pooled with the repeatability, Table A.6
    assert t["interaction"]["f"] == pytest.approx(1.193, abs=1e-3) and t["interaction"]["f_crit"] == pytest.approx(1.778, abs=1e-3) and r["pooled"]
    assert r["df_error"] == 78 and r["ms_pool"] == pytest.approx(0.0334, abs=1e-4)
    assert r["u_av"] == pytest.approx(0.08683, abs=1e-4) and r["u_evo"] == pytest.approx(0.1827, abs=1e-4)
    assert t["part"]["variance"] == pytest.approx(6.501, abs=2e-3) and t["operator"]["variance"] == pytest.approx(0.00754, abs=1e-4)
    assert t["operator"]["f"] == pytest.approx(7.776, abs=5e-3) and t["part"]["f"] == pytest.approx(1754, abs=1)  # Table A.6


def test_the_unpooled_model_of_table_a_5_and_the_components_of_b_2():
    r = iso.reproducibility(A4, alpha=0.4)  # a level at which the interaction counts as significant: the full model of Table B.2
    assert not r["pooled"] and r["model"] == "full"
    t = {row["source"]: row for row in r["table"]}
    assert t["operator"]["variance"] == pytest.approx(0.00738, abs=1e-4) and t["part"]["variance"] == pytest.approx(6.500, abs=2e-3) and t["interaction"]["variance"] == pytest.approx(0.00205, abs=1e-4)
    assert r["u_ia"] == pytest.approx(0.04528, abs=1e-4) and r["u_av"] == pytest.approx(0.08591, abs=1e-4) and r["u_evo"] == pytest.approx(math.sqrt(0.0320), abs=2e-3)


def test_one_operator_uses_the_model_of_table_b_3_and_the_design_is_checked():
    one = iso.reproducibility(np.array(A4)[:1])
    assert one["model"] == "parts_only" and one["u_av"] is None and one["u_evo"] > 0
    ms_evo = sum(((np.array(A4[0]) - np.array(A4[0]).mean(axis=1, keepdims=True)) ** 2).sum() for _ in [0]) / (10 * 2)
    assert one["u_evo"] == pytest.approx(math.sqrt(ms_evo))
    small = iso.reproducibility(np.array(A4)[:2, :4, :2])
    assert set(small["notes"]) >= {"few_workpieces", "few_measurements", "few_repetitions"}
    for bad in (lambda: iso.reproducibility(np.array(A4)[:, :2, :]), lambda: iso.reproducibility(np.array(A4)[:, :, :1]), lambda: iso.reproducibility(np.ones((3, 10, 3))),
                lambda: iso.reproducibility([[1, 2], [3]]), lambda: iso.reproducibility(A4, alpha=2)):
        with pytest.raises(MsaError):
            bad()


# ------------------------------------------------------------------ the combination and the capability: A.3 to A.5

def components_of_the_example():
    lin, rep, proc = iso.linearity(A1_X, A1_Y), iso.linearity(A1_X, A1_Y), iso.reproducibility(A4)
    return {"cal": 0.005, "lin": lin["u_lin"], "bi": 0.0, "evr": rep["u_evr"], "re": iso.u_resolution(5e-3), "evo": proc["u_evo"], "av": proc["u_av"]}


def test_the_worked_example_of_a_4_and_a_5():
    r = iso.combine(components_of_the_example(), 11 - 2)
    assert r["u_ms"] == pytest.approx(0.0836, abs=5e-5) and r["U_ms"] == pytest.approx(0.1672, abs=1e-4)
    assert r["u_mp"] == pytest.approx(0.2093, abs=1e-4) and r["U_mp"] == pytest.approx(0.4185, abs=2e-4)
    assert r["q_ms"] == pytest.approx(3.7, abs=0.05) and r["q_mp"] == pytest.approx(9.3, abs=0.05)
    assert r["c_ms"] == pytest.approx(5.38, abs=0.01) and r["c_mp"] == pytest.approx(4.30, abs=0.01)
    assert r["verdict"] == "pass" and r["ms_ok"] and r["mp_ok"] and r["route"] == "experimental"
    # u_EV is the largest of u_EVR, u_EVO and u_RE (A.3: the resolution is smaller than the repeatability and is not used)
    assert r["process_parts"]["ev"] == pytest.approx(0.1827, abs=1e-4) and r["system_parts"]["ev"] == pytest.approx(0.0641, abs=1e-4)
    assert r["largest"] == "evo" and "re" in r["unimportant"] and "cal" in r["unimportant"]  # below 10 % of the largest component (6.1)


def test_the_measuring_system_alone_and_the_mpe_route():
    c = components_of_the_example()
    system_only = iso.combine({k: c[k] for k in ("cal", "lin", "bi", "evr", "re")}, 9)
    assert system_only["u_mp"] is None and system_only["q_mp"] is None and system_only["mp_ok"] is None and system_only["u_ms"] == pytest.approx(0.0836, abs=5e-5)
    mpe = iso.combine({"evr": 0.0641, "re": 0.0014}, 9, mpe=[0.15])
    assert mpe["route"] == "mpe" and mpe["u_ms"] == pytest.approx(math.sqrt(0.15 ** 2 / 3 + 0.0641 ** 2))
    ia = iso.combine({**c}, 9, interactions=[0.04, 0.03])
    assert ia["u_mp"] ** 2 == pytest.approx(iso.combine(c, 9)["u_mp"] ** 2 + 0.04 ** 2 + 0.03 ** 2)
    wide = iso.combine({"evr": 0.5}, 1.0)  # Q_MS = 100 %
    assert wide["verdict"] == "fail" and not wide["ms_ok"]
    for bad in (lambda: iso.combine({"nonsense": 1}, 9), lambda: iso.combine({}, 9), lambda: iso.combine({"evr": 0.1}, 0), lambda: iso.combine({"evr": -1}, 9), lambda: iso.combine({"evr": 0.1}, 9, k=0)):
        with pytest.raises(MsaError):
            bad()


def test_the_coverage_factor_is_2_or_student_t_below_30_measurements():
    assert iso.coverage_factor(10, 3, 1, 3) == {"k": 2.0, "student": False, "nu": 60}
    small = iso.coverage_factor(3, 2, 2, 3)  # 36 measurements: still 2
    assert small["k"] == 2.0
    few = iso.coverage_factor(3, 2, 1, 4)  # 24 measurements
    assert few["student"] and few["nu"] == 3 * 2 * 1 * 3 and few["k"] == pytest.approx(stats.t.ppf(0.975, 18))
    # the standard prints t(24) = 2.11 and t(12) = 2.23 for its examples; the quantiles are 2.064 and 2.179: the formula is used
    assert iso.coverage_factor(3, 2, 2, 3, n_measurements=12)["k"] == pytest.approx(stats.t.ppf(0.975, 24)) and stats.t.ppf(0.975, 24) != pytest.approx(2.11, abs=0.01)


def test_the_resolution_rules_of_5_2():
    r = iso.resolution_check(0.005, tolerance=9.0, process_variation=2.0)
    assert r["conformity"] == {"limit": 0.45, "ok": True} and r["spc"] == {"limit": 0.4, "ok": True} and r["u_re"] == pytest.approx(0.005 / math.sqrt(12))
    assert iso.resolution_check(0.5, tolerance=9.0)["conformity"]["ok"] is False and iso.resolution_check(0.5, tolerance=9.0)["spc"] is None


# ------------------------------------------------------------------ clause 10: the production process against the measurement process

def test_table_10_observed_and_real_indices():
    table = {0.67: (0.67, 0.68, 0.70, 0.73, 0.77), 1.00: (1.01, 1.05, 1.12, 1.25, 1.51), 1.33: (1.36, 1.45, 1.66, 2.21, 18.82), 1.67: (1.72, 1.93, 2.53, None, None), 2.00: (2.10, 2.50, 4.59, None, None)}
    for c_obs, row in table.items():
        for q, expected in zip((0.1, 0.2, 0.3, 0.4, 0.5), row):
            got = iso.real_capability(c_obs, q)
            if expected is None:
                assert got is None, (c_obs, q)
            else:
                assert got == pytest.approx(expected, abs=0.011), (c_obs, q)
    # the example of 10.1: the printed 1.118 5 is 1.1198 from the formula; B.4 derives the formula, which is used
    assert iso.real_capability(1.0, 0.3) == pytest.approx(1.1198, abs=1e-4)
    # B.4: the observed index of a process with the measurement error added to its variance
    sp, smp, tol = 0.1, 0.03, 1.2
    c_obs = tol / (6 * math.sqrt(sp ** 2 + smp ** 2))
    q = 4 * smp / tol
    assert iso.real_capability(c_obs, q) == pytest.approx(tol / (6 * sp))
    assert iso.real_capability(0.9, 0.0) == pytest.approx(0.9)
    with pytest.raises(MsaError):
        iso.real_capability(0, 0.1)


def test_table_11_follows_a_different_definition_of_c_mp_than_clause_9_2():
    # Table 11 prints 1.36, 1.37, 1.39, 1.45 and 2.21 for the observed 1.33 and C_MP = 2, 1.66, 1.33, 1, 0.5. With C_MP = 0.3 (U - L) / (3 u_MP) of 9.2 (Q_MP = 0.4 / C_MP) the values
    # are those of Table 10 at Q_MP = 20 %, 24 %, 30 %, 40 %, 80 %: not the printed ones. The printed ones equal 1 / C_obs^2 - (0.3 / C_MP)^2, that is C_MP read with 6 u_MP.
    printed = {2.0: 1.36, 1.66: 1.37, 1.33: 1.39, 1.0: 1.45, 0.5: 2.21}
    for c_mp, expected in printed.items():
        assert (1 / 1.33 ** 2 - (0.3 / c_mp) ** 2) ** -0.5 == pytest.approx(expected, abs=0.011)
        assert iso.real_capability_from_cmp(1.33, c_mp) != pytest.approx(expected, abs=0.011) or c_mp == 1.0
    assert iso.real_capability_from_cmp(1.33, 1.33) == pytest.approx(iso.real_capability(1.33, 0.3), abs=5e-3)


# ------------------------------------------------------------------ monitoring: clause 11.2

def test_the_linearity_monitor_limits_and_differences():
    fit = iso.linearity(A1_X, A1_Y)
    refs = [2.0, 6.0, 10.0]
    readings = [[0.2358 + 0.987 * v + e for e in (0.01, -0.02)] for v in refs]
    m = iso.linearity_monitor(fit["beta0"], fit["beta1"], fit["residual_sd"], 38, refs, readings)
    half = fit["residual_sd"] / fit["beta1"] * stats.t.ppf(1 - 0.05 / 6, 38)
    assert m["ucl"] == pytest.approx(half) and m["lcl"] == pytest.approx(-half) and m["valid"] and m["standards"] == 3
    assert m["rows"][0]["differences"] == pytest.approx([0.01 / fit["beta1"], -0.02 / fit["beta1"]], abs=2e-3)
    drifted = iso.linearity_monitor(fit["beta0"], fit["beta1"], fit["residual_sd"], 38, refs, [[v + 0.6] for v in [0.2358 + 0.987 * r for r in refs]])
    assert not drifted["valid"] and all(r["out"] == [True] for r in drifted["rows"])
    for bad in (lambda: iso.linearity_monitor(0, 1.0, 0.1, 38, [1.0], [[1.0]]), lambda: iso.linearity_monitor(0, 0.0, 0.1, 38, refs, readings), lambda: iso.linearity_monitor(0, 1, 0.1, 0, refs, readings)):
        with pytest.raises(MsaError):
            bad()


# ------------------------------------------------------------------ clause 12: attribute measurement processes

TABLE_12 = [[7, 3, 1], [10, 4, 7], [2, 1, 5]]  # rows: operator A (class 1, 2, 3), columns: operator B


def test_the_bowker_test_of_table_12():
    r = iso.bowker(TABLE_12)
    assert r["chi2"] == pytest.approx(8.603, abs=1e-3) and r["df"] == 3 and r["critical"] == pytest.approx(7.815, abs=1e-3)
    assert r["symmetric"] is False and r["p_value"] == pytest.approx(stats.chi2.sf(8.603, 3), abs=1e-4)
    assert iso.bowker([[5, 2, 1], [2, 6, 3], [1, 3, 7]])["symmetric"] is True
    for bad in (lambda: iso.bowker([[1, 2, 3]]), lambda: iso.bowker([[1, 2], [3]]), lambda: iso.bowker([[1, -1], [0, 1]]), lambda: iso.bowker([[5, 0], [0, 5]])):
        with pytest.raises(MsaError):
            bad()


def test_the_bowker_study_builds_the_table_from_three_trials_per_part():
    assert [iso.trial_class(t) for t in ([1, 1, 1], [0, 1, 1], [0, 0, 0], [0, 1, 0])] == [1, 2, 3, 2]
    rng = np.random.default_rng(4)
    truth = rng.random(40) < 0.5
    a = np.array([np.where(rng.random(40) < 0.9, truth, ~truth) for _ in range(3)]).astype(int)
    b = np.array([np.where(rng.random(40) < 0.9, truth, ~truth) for _ in range(3)]).astype(int)
    r = iso.bowker_study({"A": a.tolist(), "B": b.tolist()})
    assert r["parts"] == 40 and r["trials"] == 3 and len(r["pairs"]) == 1 and np.array(r["pairs"][0]["table"]).sum() == 40 and r["notes"] == []
    # the table is built from the classes of the parts: operator A in class 1 and B in class 3 are counted in cell (1, 3)
    pa = np.ones((3, 40), dtype=int)
    pb = np.zeros((3, 40), dtype=int)
    assert iso.bowker_study({"A": pa.tolist(), "B": pb.tolist()})["pairs"][0]["table"][0][2] == 40
    assert "few_parts" in iso.bowker_study({"A": a[:, :20].tolist(), "B": b[:, :20].tolist()})["notes"]
    for bad in (lambda: iso.bowker_study({"A": a.tolist()}), lambda: iso.bowker_study({"A": a.tolist(), "B": b[:, :30].tolist()}), lambda: iso.bowker_study({"A": a.tolist(), "B": (b + 2).tolist()})):
        with pytest.raises(MsaError):
            bad()


# the reference values of figure 6, highest first (the part numbers are those of the figure; the copy prints 0.581457 for part 30, a typing error for the 0.561457 of step 2)
FIG6 = [0.599581, 0.587893, 0.576459, 0.570360, 0.566575, 0.566152, 0.561457, 0.559918, 0.547204, 0.545804, 0.544951, 0.543077, 0.542704, 0.531939, 0.529065, 0.523754, 0.521642,
        0.520469, 0.519694, 0.517377, 0.515573, 0.514192, 0.513779, 0.509015, 0.505850, 0.503091, 0.502436, 0.502295, 0.501132, 0.496696, 0.493441, 0.488905, 0.488184, 0.487613,
        0.486379, 0.484167, 0.483803, 0.477236, 0.476901, 0.470832, 0.465454, 0.462410, 0.454510, 0.452310, 0.449696, 0.446697, 0.437810, 0.427000, 0.424530, 0.409238]


def figure_6_results(trials=3, operators=3):
    """The pattern of figure 6: all results rejected above 0.566152, all approved from 0.542704 to 0.470832, rejected again from 0.446697, and one disagreeing result in between."""
    res = {}
    for o in range(operators):
        rows = []
        for t in range(trials):
            row = []
            for v in FIG6:
                if v >= 0.566152 or v <= 0.446697:
                    row.append(0)
                elif 0.470832 <= v <= 0.542704:
                    row.append(1)
                else:
                    row.append(1 if (o == 0 and t == 0) != (v > 0.5) else 0)  # in the two transition zones the results differ
            rows.append(row)
        res[f"op{o + 1}"] = rows
    return res


def test_the_uncertainty_range_of_12_3_reproduces_the_printed_steps():
    assert len(FIG6) == 50 and FIG6 == sorted(FIG6, reverse=True)
    r = iso.uncertainty_range(FIG6[::-1], {k: [row[::-1] for row in v] for k, v in figure_6_results().items()}, 0.45, 0.55)  # the order of the parts does not matter
    assert (r["last_rejected_above"], r["first_approved"], r["last_approved"], r["first_rejected_below"]) == (0.566152, 0.542704, 0.470832, 0.446697)
    assert r["d_ur"] == pytest.approx(0.023448, abs=1e-9) and r["d_lr"] == pytest.approx(0.024135, abs=1e-9)
    assert r["d"] == pytest.approx(0.0237915, abs=1e-9) and r["u_attr"] == pytest.approx(0.0237915 / 2, abs=1e-9)
    assert r["q_attr"] == pytest.approx(23.79, abs=0.01) and round(r["q_attr"] / 100, 2) == 0.24 and r["tolerance"] == pytest.approx(0.1) and not r["rule_ok"]  # the rule of thumb is 20 %
    assert r["parts"] == 50 and r["operators"] == 3 and r["trials"] == 3 and r["inconsistent_parts"] > 0


def test_the_uncertainty_range_needs_parts_on_both_sides():
    res = figure_6_results()
    only_low = {k: [row[:10] for row in v] for k, v in res.items()}
    for bad in (lambda: iso.uncertainty_range(FIG6[:10], only_low, 0.45, 0.55), lambda: iso.uncertainty_range(FIG6, {"a": [[0] * 50, [0] * 50], "b": [[0] * 50, [0] * 50]}, 0.45, 0.55),
                lambda: iso.uncertainty_range(FIG6, {"a": res["op1"]}, 0.45, 0.55), lambda: iso.uncertainty_range(FIG6, res, 0.55, 0.45), lambda: iso.uncertainty_range(FIG6[:5], res, 0.45, 0.55)):
        with pytest.raises(MsaError):
            bad()


def test_the_ongoing_review_of_an_attribute_process_12_4():
    u_max = iso.u_mp_max(0.30, 0.1)
    assert u_max == pytest.approx(0.015)
    ok = iso.attribute_review([0.40, 0.50, 0.60], [[0, 1, 0], [0, 1, 0], [0, 1, 0]], 0.45, 0.55, u_max)
    assert ok["accepted"] and ok["agreeing"] == 9 and ok["zones"] == ["below", "inside", "above"] and ok["has_inside"] and ok["has_below"] and ok["has_above"]
    assert ok["agreement_ci"][0] == pytest.approx(stats.beta.ppf(0.025, 9, 1)) and ok["agreement_ci"][1] == 1.0
    bad = iso.attribute_review([0.40, 0.50, 0.60], [[0, 1, 0], [0, 0, 0], [0, 1, 0]], 0.45, 0.55)
    assert not bad["accepted"] and bad["agreeing"] == 8
    with pytest.raises(MsaError):
        iso.attribute_review([0.40, 0.46, 0.60], [[0, 1, 0]], 0.45, 0.55, u_max)  # 0.46 lies inside the uncertainty range
    with pytest.raises(MsaError):
        iso.attribute_review([0.4, 0.5], [[0, 1]], 0.45, 0.55)

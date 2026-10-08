"""The examples and the guidelines of the AIAG MSA Reference Manual, 4th edition (docs/372890491-MSA-Reference-Manual-4th-Edition.pdf)."""

import numpy as np
import pytest

from spc.core import msa_attribute as MA
from spc.core import msa_more
from spc.msa import gate

AIAG_ATTRIBUTE_ROWS = """\
111 111 111 1
111 111 111 1
000 000 000 0
000 000 000 0
000 000 000 0
110 110 100 1
111 111 101 1
111 111 111 1
000 000 000 0
111 111 111 1
111 111 111 1
000 000 010 0
111 111 111 1
110 111 100 1
111 111 111 1
111 111 111 1
111 111 111 1
111 111 111 1
111 111 111 1
111 111 111 1
110 101 010 1
001 010 110 0
111 111 111 1
111 111 111 1
000 000 000 0
010 000 001 0
111 111 111 1
111 111 111 1
111 111 111 1
000 001 000 0
111 111 111 1
111 111 111 1
111 111 111 1
001 001 011 0
111 111 111 1
110 111 101 1
000 000 000 0
111 111 111 1
000 000 000 0
111 111 111 1
111 111 111 1
000 000 000 0
101 111 110 1
111 111 111 1
000 000 000 0
111 111 111 1
111 111 111 1
000 000 000 0
111 111 111 1
000 000 000 0
"""
# Table III-C 1: 50 parts, appraisers A, B and C with three trials each, and the reference decision. One line per part: the three trials of A, B, C, the reference.


def aiag_attribute():
    ratings = {"A": [[], [], []], "B": [[], [], []], "C": [[], [], []]}
    reference = []
    for line in AIAG_ATTRIBUTE_ROWS.strip().split("\n"):
        a, b, c, ref = line.split()
        for name, trials in (("A", a), ("B", b), ("C", c)):
            for t in range(3):
                ratings[name][t].append(int(trials[t]))
        reference.append(int(ref))
    return ratings, reference


def test_the_attribute_study_of_the_manual_gives_its_kappas_effectiveness_miss_and_false_alarm_rates():
    """Tables III-C 3, 5 and 7 (the cross-tab method): kappa between the appraisers and against the reference, effectiveness with its 95 % interval, miss rate, false alarm rate."""
    ratings, reference = aiag_attribute()
    r = MA.evaluate(ratings, reference)
    assert r["parts"] == 50 and r["trials"] == 3 and r["reference"] == {"conforming": 34, "nonconforming": 16}  # 102 and 48 decisions, as in Table III-C 4
    k = r["between"]["kappa"]
    assert k["A×B"] == pytest.approx(0.86, abs=0.005) and k["A×C"] == pytest.approx(0.78, abs=0.005) and k["B×C"] == pytest.approx(0.79, abs=0.005)  # Table III-C 3
    ref = r["against_reference"]
    assert [round(ref[x]["kappa"], 2) for x in "ABC"] == [0.88, 0.92, 0.77]  # the kappa of each appraiser to the reference decision
    assert [ref[x]["effectiveness"]["pct"] for x in "ABC"] == [84.0, 90.0, 80.0]  # Table III-C 5: calculated score
    assert [ref[x]["effectiveness"]["k"] for x in "ABC"] == [42, 45, 40]  # # matched
    for x, (lo, hi) in zip("ABC", ((71, 93), (78, 97), (66, 90))):  # 95 % LCI and UCI printed in whole percent
        assert round(ref[x]["effectiveness"]["ci"][0]) == lo and round(ref[x]["effectiveness"]["ci"][1]) == hi
    assert [ref[x]["miss"]["pct"] for x in "ABC"] == pytest.approx([6.3, 6.3, 12.5], abs=0.06)  # Table III-C 7 (6.25 is printed as 6.3)
    assert [ref[x]["false_alarm"]["pct"] for x in "ABC"] == pytest.approx([4.9, 2.0, 8.8], abs=0.06)
    assert (ref["A"]["miss"]["k"], ref["A"]["miss"]["n"], ref["A"]["false_alarm"]["k"], ref["A"]["false_alarm"]["n"]) == (3, 48, 5, 102)  # the cross-tab A * REF of Table III-C 4
    s = r["system"]["right"]
    assert (s["k"], s["pct"]) == (39, 78.0) and round(s["ci"][0]) == 64 and round(s["ci"][1]) == 88  # "System % Effective Score vs. Reference": 78 %, 64 % to 89 % (88.5 is printed as 89)
    assert r["between"]["agree"]["k"] == 39


def test_the_guidelines_of_the_manual_are_the_default_policy_of_the_program():
    """Table II-D 1 (GRR under 10 %, 10 to 30 %, over 30 %; ndc at least 5), Table III-C 6 (effectiveness, miss rate, false alarm rate) and the rule of thumb for kappa (0.75 and 0.40)."""
    pol = gate.POLICY_DEFAULTS
    assert (pol["grr_pass"], pol["grr_conditional"], pol["ndc_min"]) == (10.0, 30.0, 5.0)
    d = MA.POLICY_DEFAULTS
    assert {f"attr_{k}": v for k, v in d.items()}.items() <= pol.items()  # the attribute guidelines are the ones of the gate
    assert (d["eff_pass"], d["eff_conditional"]) == (90.0, 80.0)
    assert (d["miss_pass"], d["miss_conditional"]) == (2.0, 5.0)
    assert (d["fa_pass"], d["fa_conditional"]) == (5.0, 10.0)
    assert (d["kappa_pass"], d["kappa_conditional"]) == (0.75, 0.40)
    # the appraisers of the manual by those guidelines: nobody is acceptable on all three, nobody unacceptable on all three
    ratings, reference = aiag_attribute()
    r = MA.evaluate(ratings, reference)
    grades = {x: [MA._grade(r["against_reference"][x]["effectiveness"]["pct"], 90, 80, True), MA._grade(r["against_reference"][x]["miss"]["pct"], 2, 5, False),
                  MA._grade(r["against_reference"][x]["false_alarm"]["pct"], 5, 10, False)] for x in "ABC"}
    assert all(set(g) != {"pass"} for g in grades.values()) and all(set(g) != {"fail"} for g in grades.values())


LINEARITY_REFERENCE = [2, 4, 6, 8, 10]
LINEARITY_READINGS = [
    [2.70, 2.50, 2.40, 2.50, 2.70, 2.30, 2.50, 2.50, 2.40, 2.40, 2.60, 2.40],
    [5.10, 3.90, 4.20, 5.00, 3.80, 3.90, 3.90, 3.90, 3.90, 4.00, 4.10, 3.80],
    [5.80, 5.70, 5.90, 5.90, 6.00, 6.10, 6.00, 6.10, 6.40, 6.30, 6.00, 6.10],
    [7.60, 7.70, 7.80, 7.70, 7.80, 7.80, 7.80, 7.70, 7.80, 7.50, 7.60, 7.70],
    [9.10, 9.30, 9.50, 9.30, 9.40, 9.50, 9.50, 9.50, 9.60, 9.20, 9.30, 9.40],
]


def test_the_linearity_study_of_the_manual_finds_the_line_the_statistics_and_the_problem():
    """Table III-B 4 and 5, Figure III-B 3: Y = 0.736667 - 0.131667 X, R-squared 71.4 %, t of the slope 12.043, t of the intercept 10.158, t(58; .975) = 2.00172; the line bias = 0 is not inside the band."""
    r = msa_more.linearity(LINEARITY_REFERENCE, LINEARITY_READINGS)
    assert r["slope"] == pytest.approx(-0.131667, abs=5e-7) and r["intercept"] == pytest.approx(0.736667, abs=5e-7) and r["r2"] == pytest.approx(0.714, abs=5e-4)
    assert abs(r["slope"] / r["slope_se"]) == pytest.approx(12.043, abs=5e-4) and r["intercept"] / r["intercept_se"] == pytest.approx(10.158, abs=5e-4)
    assert r["df_fit"] == 58
    assert [round(p["bias"], 6) for p in r["part_results"]] == [0.491667, 0.125, 0.025, -0.291667, -0.616667]  # BIAS Avg. of Table III-B 5
    assert r["zero_in_band"] is False and r["verdict"] == "fail"  # "the bias = 0 line intersects the confidence bounds rather than being contained by them"
    from scipy import stats

    assert stats.t.ppf(0.975, 58) == pytest.approx(2.00172, abs=5e-6)
    assert abs(r["slope"] / r["slope_se"]) > stats.t.ppf(0.975, 58)  # the numerical analysis reinforces the graphical one: a linearity problem

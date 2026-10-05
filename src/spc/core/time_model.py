"""Suggestion of the time-dependent distribution model (draft 9.4, ISO 22514-2, tables 9-1 and 9-2).

The model says how the *instantaneous* distribution (the subgroup) behaves over time:

    location constant,  variation constant:   A1 normal, A2 not normal (unimodal)
    location constant,  variation changing:   B
    location changing,  variation constant:   C1 random and normal, C2 random and not normal (unimodal),
                                              C3 systematic (trend), C4 systematic and random (lot to lot, any shape)
    location changing,  variation changing:   D

The draft does not give a procedure to choose the model ("it is often possible to deduce it from the nature of the process").
This is a procedure from the tests that are standard for the questions the table asks. It is a SUGGESTION with its evidence and
a confidence: the person decides, and says so in the study.

  location changes?   one-way analysis of variance of the subgroup means against the variation within the subgroups; trend of the
                      means (Kendall tau and the share of the variation of the means that a straight line explains); what is left of the
                      location variation after the trend is taken out
  variation changes?  dispersion test of ln(s^2) of the subgroups against its expected spread (the exact value for normal data plus
                      the share of the kurtosis of the data, Layard 1973), valid for a skewed or heavy tailed shape, with a minimum effect
                      (DISPERSION_RATIO); mean-centred Levene and Bartlett flag every skewed but stable process, the median-centred Levene
                      and Fligner have no power for subgroups of 5. Weak for subgroups of 2 or 3. A skewed stable process is still taken
                      for a changing variation in about 5 % of the cases.
  shape               normality of the values within the subgroups (instantaneous) and of all values (resulting); number of modes of a
                      kernel density estimate of all values and of the subgroup means (unimodal or not)

Data without subgroups are cut into blocks of consecutive values: the suggestion is then one step less certain.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
from scipy import stats
from scipy.signal import find_peaks
from scipy.special import polygamma

ALPHA = 0.01  # a test counts as a finding below this
BORDERLINE = 0.05  # a decisive test between ALPHA and this makes the suggestion less certain
MIN_GROUPS = 8
MIN_VALUES = 30
DISPERSION_RATIO = 1.8  # the spread of ln s^2 must be at least this many times what the shape of the data explains
TREND_SHARE = 0.5  # a straight line must explain at least half of the variation of the subgroup means to call it systematic
MODELS = ("A1", "A2", "B", "C1", "C2", "C3", "C4", "D")

# what the user may know about the process (draft table 9-2 examples) and the models that go with it
HINT_MODELS = {
    "natural_limit": ("A2", "C2"),  # naturally limited characteristic: not normal, unimodal
    "different_fixtures": ("C1", "C2"),  # different centring of workholding fixtures
    "tool_wear_trend": ("C3", "C4"),  # trend caused by wear, cycle
    "tool_or_batch_changes": ("C4",),  # tool changes, change of batches
    "spindle_wear": ("B",),  # different wear of spindles, equal centring: variation changes
    "multi_stream": ("D",),  # multi-stream processes
}


def modes(x, min_prominence: float = 0.1) -> int:
    """Number of clear modes of a Gaussian kernel density estimate (Silverman bandwidth)."""
    x = np.asarray(x, dtype=float)
    if x.size < MIN_VALUES or not float(x.std()) > 0:
        return 1
    kde = stats.gaussian_kde(x, bw_method="silverman")
    pad = 3.0 * float(x.std()) * kde.factor
    grid = np.linspace(x.min() - pad, x.max() + pad, 512)
    dens = kde(grid)
    peaks, _ = find_peaks(dens, prominence=min_prominence * float(dens.max()))
    return max(1, int(peaks.size))


def excess_kurtosis(m: np.ndarray) -> float:
    """Excess kurtosis of the values within the subgroups: the unbiased fourth cumulant of each subgroup (n >= 4), pooled."""
    k, n = m.shape
    if n >= 4:
        d = m - m.mean(axis=1, keepdims=True)
        m2, m4 = (d ** 2).mean(axis=1), (d ** 4).mean(axis=1)
        k4 = n ** 2 * ((n + 1) * m4 - 3 * (n - 1) * m2 ** 2) / ((n - 1) * (n - 2) * (n - 3))
        return float(k4.mean() / float(m.var(axis=1, ddof=1).mean()) ** 2)
    d = (m - m.mean(axis=1, keepdims=True)).ravel() * math.sqrt(n / (n - 1))
    return float(stats.kurtosis(d))


def dispersion_test(m: np.ndarray) -> dict[str, float]:
    """p value and ratio of the spread of ln(s^2) over the subgroups to the spread that the shape of the data explains."""
    k, n = m.shape
    ls = np.log(np.maximum(m.var(axis=1, ddof=1), 1e-300))
    expected = float(polygamma(1, (n - 1) / 2.0)) + max(excess_kurtosis(m), 0.0) / n
    ratio = float(ls.var(ddof=1) / expected)
    return {"p": float(stats.chi2.sf(ratio * (k - 1), k - 1)), "ratio": ratio}


def _normal_p(x) -> float:
    x = np.asarray(x, dtype=float).ravel()
    if x.size > 5000:
        x = x[:: int(math.ceil(x.size / 5000))]
    return float(stats.shapiro(x).pvalue)


def _trend(y) -> dict:
    """Kendall trend of a series over time and the share of its variation that a straight line explains."""
    y = np.asarray(y, dtype=float)
    t = np.arange(y.size)
    tau, p = stats.kendalltau(t, y)
    lin = stats.linregress(t, y)
    return {"tau": float(tau), "p": float(p), "slope": float(lin.slope), "r2": float(lin.rvalue ** 2), "fitted": lin.intercept + lin.slope * t}


def analyse(matrix) -> dict[str, Any]:
    """The test results for subgroups (k, n) in time order."""
    m = np.asarray(matrix, dtype=float)
    k, n = m.shape
    means, sds = m.mean(axis=1), m.std(axis=1, ddof=1)
    s2_w = float(np.mean(sds ** 2))
    if not s2_w > 0:
        raise ValueError("the values do not vary within the subgroups")
    anova = stats.f_oneway(*m)
    s2_between = max(0.0, float(means.var(ddof=1)) - s2_w / n)
    tr_means = _trend(means)
    resid_means = means - tr_means["fitted"]
    # what is left of the location variation after a straight line: its variance against the variance expected from the noise
    f_res = float(resid_means.var(ddof=2) / (s2_w / n))
    p_res = float(stats.f.sf(f_res, k - 2, k * (n - 1)))
    disp = dispersion_test(m)
    tr_sd = _trend(sds)
    inst = (m - means[:, None]).ravel()
    return {
        "location": {"anova_p": float(anova.pvalue), "between_to_within": math.sqrt(s2_between / s2_w), "trend_p": tr_means["p"], "trend_tau": tr_means["tau"],
                     "trend_r2": tr_means["r2"], "after_trend_p": p_res, "modes_of_means": modes(means) if k >= 20 else 1},
        "variation": {"dispersion_p": disp["p"], "dispersion_ratio": disp["ratio"], "trend_p": tr_sd["p"], "sd_ratio": float(sds.max() / max(sds.min(), 1e-300))},
        "shape": {"instantaneous_normal_p": _normal_p(inst), "resulting_normal_p": _normal_p(m.ravel()),
                  "skewness": float(stats.skew(m.ravel())), "kurtosis": float(stats.kurtosis(m.ravel(), fisher=False)), "modes": modes(m.ravel())},
    }


def classify(ev: Mapping[str, Any], alpha: float = ALPHA) -> tuple[str, list[str]]:
    """(model, the reasons in the order of the decision)."""
    loc, var, sh = ev["location"], ev["variation"], ev["shape"]
    reasons: list[str] = []
    location_changes = loc["anova_p"] <= alpha or loc["trend_p"] <= alpha
    variation_changes = var["dispersion_p"] <= alpha and var["dispersion_ratio"] >= DISPERSION_RATIO
    resulting_normal = sh["resulting_normal_p"] > alpha
    inst_normal = sh["instantaneous_normal_p"] > alpha
    multimodal = sh["modes"] > 1 or loc["modes_of_means"] > 1
    if variation_changes:
        reasons.append("variation_changes")
        if location_changes:
            reasons.append("location_changes")
            return "D", reasons
        reasons.append("location_constant")
        return "B", reasons
    reasons.append("variation_constant")
    if not location_changes:
        reasons.append("location_constant")
        if multimodal:
            reasons.append("multimodal")
            return "D", reasons  # constant moments but several modes: the draft's model D covers multimodal results
        if resulting_normal:
            reasons.append("normal")
            return "A1", reasons
        reasons.append("not_normal_unimodal")
        return "A2", reasons
    reasons.append("location_changes")
    systematic = loc["trend_p"] <= alpha and loc["trend_r2"] >= TREND_SHARE
    if systematic:
        reasons.append("trend")
        if loc["after_trend_p"] <= alpha:
            reasons.append("random_remainder")
            return "C4", reasons
        return "C3", reasons
    reasons.append("random_location")
    if multimodal or not inst_normal:
        reasons.append("multimodal" if multimodal else "instantaneous_not_normal")
        return "C4", reasons
    if resulting_normal:
        reasons.append("normal")
        return "C1", reasons
    reasons.append("not_normal_unimodal")
    return "C2", reasons


def _borderline(ev: Mapping[str, Any], alpha: float) -> list[str]:
    """Decisive tests whose p value is close to the limit: a small change of the data could change the answer."""
    ps = {"anova_p": ev["location"]["anova_p"], "trend_p": ev["location"]["trend_p"], "dispersion_p": ev["variation"]["dispersion_p"],
          "resulting_normal_p": ev["shape"]["resulting_normal_p"], "instantaneous_normal_p": ev["shape"]["instantaneous_normal_p"],
          "after_trend_p": ev["location"]["after_trend_p"]}
    return [k for k, v in ps.items() if alpha / 5.0 < v < BORDERLINE]


def suggest(matrix, *, blocks: bool = False, hints: Mapping[str, bool] | None = None, alpha: float = ALPHA) -> dict[str, Any]:
    """The suggestion for subgroups (k, n) in time order. `blocks`: the subgroups were cut from a series (less certain)."""
    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2 or not np.all(np.isfinite(m)):
        raise ValueError("the suggestion needs subgroups of at least 2 values")
    k, n = m.shape
    if k < MIN_GROUPS or k * n < MIN_VALUES:
        return {"model": None, "reason": "not_enough_data", "groups": {"k": k, "n": n, "blocks": blocks}, "min_groups": MIN_GROUPS, "min_values": MIN_VALUES}
    ev = analyse(m)
    model, reasons = classify(ev, alpha)
    # alternatives: what the same data say with a looser or a stricter limit
    alternatives = []
    for a, why in ((alpha * 5.0, "looser"), (alpha / 5.0, "stricter")):
        other, other_reasons = classify(ev, a)
        if other != model and all(o["model"] != other for o in alternatives):
            alternatives.append({"model": other, "limit": a, "when": why})
    border = _borderline(ev, alpha)
    level = 2 if k >= 25 and k * n >= 100 else 1 if k >= 12 else 0
    if blocks:
        level = min(level, 1)
    if border:
        level -= 1
    if alternatives and not border:
        level -= 0  # alternatives alone only mean the borderline tests were found above
    confidence = ("low", "medium", "high")[max(0, min(2, level))]
    notes = []
    given = [h for h, on in (hints or {}).items() if on and h in HINT_MODELS]
    for h in given:
        if model not in HINT_MODELS[h]:
            notes.append({"hint": h, "models": list(HINT_MODELS[h]), "agrees": False})
        else:
            notes.append({"hint": h, "models": list(HINT_MODELS[h]), "agrees": True})
    if any(not x["agrees"] for x in notes):
        confidence = ("low", "low", "medium")[("low", "medium", "high").index(confidence)]
    return {"model": model, "reasons": reasons, "confidence": confidence, "alternatives": alternatives, "borderline": border, "evidence": ev,
            "groups": {"k": k, "n": n, "blocks": blocks}, "in_statistical_control": model in ("A1", "A2"), "hints": notes,
            "alpha": alpha}

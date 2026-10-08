"""Attribute measurement systems (go / no-go decisions): the attribute agreement study (AIAG MSA, hypothesis test analyses by the cross-tab method).

The draft asks for proof of the capability of every inspection process, and says IATF 16949 means variable and attribute ones (6.3). It gives no method;
the usual one is used here. Several appraisers (at least 2) judge the same parts (at least 20) in several trials (at least 2), without knowing the earlier
result. A reference decision for each part (1 = conforming, 0 = nonconforming) comes from a better measurement. Everything is for the decision "accept" (1) or "reject" (0).

  within an appraiser    parts on which all trials agree (percent) and Fleiss' kappa over the trials
  between appraisers     parts on which all appraisers agree in all trials, and Cohen's kappa for each pair of appraisers
                         (trial k of one with trial k of the other, so N = parts x trials decisions)
  against the reference  effectiveness of an appraiser: parts that were judged right in every trial / parts; the share of single decisions that were right;
                         miss rate: nonconforming parts judged conforming / decisions on nonconforming parts;
                         false alarm rate: conforming parts judged nonconforming / decisions on conforming parts;
                         Cohen's kappa against the reference (all decisions of the appraiser)
  system                 parts on which all appraisers judged right in all trials

Kappa = (po - pe) / (1 - pe). Intervals are exact binomial (Clopper-Pearson) 95 %: they treat the decisions as independent, which the trials of one part are not,
so they are a guide. The acceptance guidelines are the ones of AIAG MSA (4th edition) and are parameters of the policy of a system:
effectiveness >= 90 % capable, >= 80 % conditional; miss rate <= 2 % capable, <= 5 % conditional; false alarm rate <= 5 % capable, <= 10 % conditional;
kappa >= 0.75 capable, >= 0.4 conditional. They are Table III-C 6 of the manual (which calls them example guidelines) and its rule of thumb for kappa, and the 50-part study of the
manual (Tables III-C 1 to 7) gives the printed kappas, effectiveness, miss and false alarm rates (spc.validation.aiag_msa).
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import Any, Mapping, Sequence

import numpy as np
from scipy import stats

from spc.core.msa import MsaError

MIN_PARTS = 20
MIN_APPRAISERS = 2
MIN_TRIALS = 2
MIN_PER_CLASS = 5  # parts of the reference of each kind: without them a miss or a false alarm cannot happen
POLICY_DEFAULTS = {"eff_pass": 90.0, "eff_conditional": 80.0, "miss_pass": 2.0, "miss_conditional": 5.0, "fa_pass": 5.0, "fa_conditional": 10.0,
                   "kappa_pass": 0.75, "kappa_conditional": 0.4}


def cohen_kappa(a: Sequence[int], b: Sequence[int]) -> float | None:
    """Cohen's kappa of two series of 0/1 decisions; None when the agreement by chance is complete (no variation to agree about)."""
    x, y = np.asarray(a, dtype=int), np.asarray(b, dtype=int)
    n = x.size
    po = float(np.mean(x == y))
    pe = float(np.mean(x == 1) * np.mean(y == 1) + np.mean(x == 0) * np.mean(y == 0))
    return None if pe >= 1.0 else (po - pe) / (1.0 - pe)


def fleiss_kappa(table: np.ndarray) -> float | None:
    """Fleiss' kappa of an (items x 2) table of the number of raters that chose each category (the same number of raters for every item)."""
    n_items, _ = table.shape
    m = int(table[0].sum())
    p_cat = table.sum(axis=0) / (n_items * m)
    p_i = (np.sum(table ** 2, axis=1) - m) / (m * (m - 1))
    pe = float(np.sum(p_cat ** 2))
    return None if pe >= 1.0 else (float(p_i.mean()) - pe) / (1.0 - pe)


def _interval(k: int, n: int, confidence: float = 0.95) -> list[float | None]:
    if n == 0:
        return [None, None]
    a = 1.0 - confidence
    lo = 0.0 if k == 0 else float(stats.beta.ppf(a / 2, k, n - k + 1))
    hi = 1.0 if k == n else float(stats.beta.ppf(1 - a / 2, k + 1, n - k))
    return [100 * lo, 100 * hi]


def _rate(k: int, n: int) -> dict:
    return {"k": int(k), "n": int(n), "pct": None if n == 0 else 100.0 * k / n, "ci": _interval(k, n)}


def _grade(value: float | None, good: float, ok: float, higher_is_better: bool) -> str:
    if value is None:
        return "fail"
    if higher_is_better:
        return "pass" if value >= good else "conditional" if value >= ok else "fail"
    return "pass" if value <= good else "conditional" if value <= ok else "fail"


def validate_policy(policy: Mapping[str, Any] | None) -> dict:
    p = {**POLICY_DEFAULTS, **dict(policy or {})}
    for key, value in p.items():
        if key not in POLICY_DEFAULTS:
            raise ValueError(f"policy: unknown setting {key!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"policy.{key} must be a number")
        p[key] = float(value)
    for key in ("eff_pass", "eff_conditional", "miss_pass", "miss_conditional", "fa_pass", "fa_conditional"):
        if not 0 <= p[key] <= 100:
            raise ValueError(f"policy.{key} must be a percentage from 0 to 100")
    for key in ("kappa_pass", "kappa_conditional"):
        if not -1 <= p[key] <= 1:
            raise ValueError(f"policy.{key} must be from -1 to 1")
    if not p["eff_pass"] > p["eff_conditional"] or not p["miss_pass"] < p["miss_conditional"] or not p["fa_pass"] < p["fa_conditional"] or not p["kappa_pass"] > p["kappa_conditional"]:
        raise ValueError("policy: the limit for capable must be better than the limit for conditional")
    return p


def evaluate(ratings: Mapping[str, Sequence[Sequence[int]]], reference: Sequence[int], policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """`ratings`: {appraiser: [trial 1 decisions per part, trial 2 ...]}, 1 = accept, 0 = reject; `reference`: 1 = conforming, 0 = nonconforming per part."""
    pol = validate_policy(policy)
    if not isinstance(ratings, Mapping) or len(ratings) < MIN_APPRAISERS:
        raise MsaError(f"an attribute study needs at least {MIN_APPRAISERS} appraisers")
    try:
        ref = np.asarray(reference, dtype=int)
        data = {str(name): np.asarray(trials, dtype=int) for name, trials in ratings.items()}
    except (TypeError, ValueError):
        raise MsaError("the decisions must be 0 or 1") from None
    if ref.ndim != 1 or ref.size < MIN_PARTS:
        raise MsaError(f"an attribute study needs at least {MIN_PARTS} parts with a reference decision each")
    if not np.all(np.isin(ref, (0, 1))):
        raise MsaError("the reference decisions must be 0 (nonconforming) or 1 (conforming)")
    trials = None
    for name, arr in data.items():
        if arr.ndim != 2 or arr.shape[1] != ref.size:
            raise MsaError(f"appraiser {name!r}: every trial needs one decision for each of the {ref.size} parts")
        if not np.all(np.isin(arr, (0, 1))):
            raise MsaError(f"appraiser {name!r}: the decisions must be 0 (reject) or 1 (accept)")
        if trials is None:
            trials = arr.shape[0]
        elif arr.shape[0] != trials:
            raise MsaError("every appraiser needs the same number of trials")
    if trials < MIN_TRIALS:
        raise MsaError(f"an attribute study needs at least {MIN_TRIALS} trials")
    n_ok, n_bad = int(np.sum(ref == 1)), int(np.sum(ref == 0))
    if n_ok < MIN_PER_CLASS or n_bad < MIN_PER_CLASS:
        raise MsaError(f"the reference needs at least {MIN_PER_CLASS} conforming and {MIN_PER_CLASS} nonconforming parts (it has {n_ok} and {n_bad})")
    names = list(data)
    n = ref.size
    warnings = []
    if n < 50:
        warnings.append({"code": "attr_few_parts", "n": n})
    if len(names) < 3 or trials < 3:
        warnings.append({"code": "attr_small_design", "appraisers": len(names), "trials": trials})
    if min(n_ok, n_bad) / n < 0.25:
        warnings.append({"code": "attr_unbalanced_reference", "conforming": n_ok, "nonconforming": n_bad})

    within, appraisers = {}, {}
    all_right_parts = np.ones(n, dtype=bool)
    for name in names:
        arr = data[name]
        agree = np.all(arr == arr[0], axis=0)
        counts = np.stack([np.sum(arr == 1, axis=0), np.sum(arr == 0, axis=0)], axis=1)
        within[name] = {"agree": _rate(int(agree.sum()), n), "kappa": fleiss_kappa(counts)}
        right = arr == ref  # trials x parts
        all_right = np.all(right, axis=0)
        all_right_parts &= all_right
        miss_k = int(np.sum((arr == 1) & (ref == 0)))
        fa_k = int(np.sum((arr == 0) & (ref == 1)))
        appraisers[name] = {
            "effectiveness": _rate(int(all_right.sum()), n),
            "decisions_right": _rate(int(right.sum()), right.size),
            "miss": _rate(miss_k, n_bad * trials),
            "false_alarm": _rate(fa_k, n_ok * trials),
            "kappa": cohen_kappa(arr.ravel(), np.tile(ref, trials)),
        }
    all_agree = np.ones(n, dtype=bool)
    for arr in data.values():
        all_agree &= np.all(arr == arr[0], axis=0)
    first = data[names[0]]
    for name in names[1:]:
        all_agree &= np.all(data[name] == first[0], axis=0)
    pairs = {f"{a}×{b}": cohen_kappa(data[a].ravel(), data[b].ravel()) for a, b in combinations(names, 2)}
    between = {"agree": _rate(int(all_agree.sum()), n), "kappa": pairs}
    system = {"right": _rate(int(all_right_parts.sum()), n)}

    # the verdict: the worst appraiser on each measure decides
    def worst(key: str, getter, good, ok, higher):
        grades = [_grade(getter(a), good, ok, higher) for a in appraisers.values()]
        return "fail" if "fail" in grades else "conditional" if "conditional" in grades else "pass"

    kappas = [k for k in [*pairs.values(), *(a["kappa"] for a in appraisers.values())]]
    kappa_grades = [_grade(k, pol["kappa_pass"], pol["kappa_conditional"], True) for k in kappas]
    checks = {
        "effectiveness": worst("effectiveness", lambda a: a["effectiveness"]["pct"], pol["eff_pass"], pol["eff_conditional"], True),
        "miss": worst("miss", lambda a: a["miss"]["pct"], pol["miss_pass"], pol["miss_conditional"], False),
        "false_alarm": worst("false_alarm", lambda a: a["false_alarm"]["pct"], pol["fa_pass"], pol["fa_conditional"], False),
        "kappa": "fail" if "fail" in kappa_grades else "conditional" if "conditional" in kappa_grades else "pass",
    }
    verdict = "fail" if "fail" in checks.values() else "conditional" if "conditional" in checks.values() else "pass"
    effs = [a["effectiveness"]["pct"] for a in appraisers.values()]
    return {
        "parts": n, "appraisers": names, "trials": int(trials), "reference": {"conforming": n_ok, "nonconforming": n_bad},
        "within": within, "between": between, "against_reference": appraisers, "system": system,
        "checks": checks, "verdict": verdict, "worst": {
            "effectiveness": min(effs), "miss": max(a["miss"]["pct"] for a in appraisers.values()), "false_alarm": max(a["false_alarm"]["pct"] for a in appraisers.values()),
            "kappa": min((k for k in kappas if k is not None), default=None)},
        "policy": pol, "warnings": warnings,
    }

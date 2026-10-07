"""Special cases of draft 8.5 on stored data: the combinations of a multi-stage machine (8.5.1)."""

from __future__ import annotations

import math

import numpy as np

from spc.core import multistage as mst
from spc.core import nested
from spc.core.capability.indices import geometric_indices, ppm_out_of_spec, zscore_indices
from spc.core.charts import trend as trend_chart
from spc.core.distributions import FAMILIES, EXPLICIT_ONLY, FitError, GaussianMixture, choose_automatically, fit, fit_candidates
from spc.data import Dataset


def _columns(ds: Dataset, factors: list[str]) -> tuple[list[float], dict[str, list[str]]]:
    mask = ds.valid_mask
    cols = {}
    for name in factors:
        if name == "subgroup":
            col = ds.subgroup
            if col is None:
                raise ValueError("the data has no subgroup labels")
        elif name in ds.tags:
            col = ds.tags[name]
        else:
            raise ValueError(f"the data has no tag {name!r}; the tags are {sorted(ds.tags)}")
        cols[name] = [str(v) for v, ok in zip(col.tolist(), mask.tolist()) if ok]
    return [v for v, ok in zip(ds.values.tolist(), mask.tolist()) if ok], cols


def coverage_for_dataset(ds: Dataset, factors: list[str] | None = None, expected: dict | None = None, thin_share: float = 0.5) -> dict:
    """Does the sample stand for the tools, lots and shifts (draft 9.2)? `factors` are tag names (all of them when empty); values marked invalid are left out."""
    from spc.core import sampling_plan

    names = list(factors or ds.tags)
    if not names:
        raise ValueError("the data has no tags (tool, lot, shift ...) to look at")
    _, cols = _columns(ds, names)
    sub = [str(v) for v, ok in zip(ds.subgroup.tolist(), ds.valid_mask.tolist()) if ok] if ds.subgroup is not None else None
    return sampling_plan.coverage(cols, sub, expected, thin_share)


def multistage_for_dataset(ds: Dataset, factors: list[str], lsl: float | None, usl: float | None, **options) -> dict:
    """The factors are tag names (pallet, spindle, machine, position ...) or `subgroup`. Values marked invalid are left out."""
    values, cols = _columns(ds, factors)
    return mst.analyse_combinations(values, cols, lsl, usl, **options)


def nested_for_dataset(ds: Dataset, levels: list[str], alpha: float = 0.05) -> dict:
    """Nested variance components. The levels are tag names or `subgroup`, outermost first."""
    values, cols = _columns(ds, levels)
    return nested.analyse(values, cols, alpha)


def cavity_study(ds: Dataset, factor: str, lsl: float | None, usl: float | None, alpha: float = 0.05) -> dict:
    """Equipment with several cavities, stations or clamping devices (draft 8.2.6): each one is a machine of its own (Pm and Pmk of its values), and the variation is split
    into the part between the cavities and the part within them. `factor` is a tag name or `subgroup`."""
    from spc.core.capability.indices import overall_indices

    values, cols = _columns(ds, [factor])
    labels = cols[factor]
    x = np.asarray(values, dtype=float)
    per = []
    for name in dict.fromkeys(labels):
        v = x[[i for i, k in enumerate(labels) if k == name]]
        row = {"label": name, "n": int(v.size), "mean": float(v.mean()), "sd": float(v.std(ddof=1)) if v.size > 1 else None, "pm": None, "pmk": None}
        if (lsl is not None or usl is not None) and v.size >= 5 and np.ptp(v) > 0:
            idx = overall_indices(v, lsl, usl)
            row["pm"], row["pmk"] = (None if idx.p is None else float(idx.p)), float(idx.pk)
        per.append(row)
    whole = None
    if (lsl is not None or usl is not None) and x.size >= 5 and np.ptp(x) > 0:
        idx = overall_indices(x, lsl, usl)
        whole = {"pm": None if idx.p is None else float(idx.p), "pmk": float(idx.pk), "n": int(x.size)}
    return {"factor": factor, "cavities": per, "whole": whole, "variance": nested.analyse(values, {factor: labels}, alpha) if len(per) >= 2 else None,
            "combinations": mst.analyse_combinations(values, {factor: labels}, lsl, usl, alpha=alpha) if len(per) >= 2 else None, "alpha": alpha}


def trend_for_dataset(ds: Dataset, cycle: int | None = None, subgroup_size: int | None = None, lsl: float | None = None, usl: float | None = None,
                      distribution: str = "auto", method: str = "G") -> dict:
    """The regression control chart of the subgroup means (the labels of the data, or `subgroup_size`) or of the individual values."""
    if distribution not in ("auto", *FAMILIES, *EXPLICIT_ONLY) or method not in ("G", "Z"):
        raise ValueError("distribution must be 'auto' or a known family and method G or Z")
    if lsl is not None and usl is not None and not lsl < usl:
        raise ValueError("lsl must be below usl")
    labels = None
    if ds.subgroup is not None or (subgroup_size or 1) > 1:
        sg = ds.subgroups(size=None if ds.subgroup is not None else subgroup_size, incomplete="drop")
        stat, n_sub, labels = sg.matrix.mean(axis=1), int(sg.nominal_size), list(sg.labels)
        sigma_in = float(math.sqrt(np.mean(np.var(sg.matrix, axis=1, ddof=1)))) if n_sub > 1 else None
        values = sg.matrix.ravel()
    else:
        values, _ = ds.individuals()
        stat, n_sub, sigma_in = values, 1, None
    chart = trend_chart.fit(stat, cycle)
    k = int(stat.size)
    out = {
        "n_points": k, "subgroup_size": n_sub, "cycle": cycle, "labels": labels,
        "position": chart.position.tolist(), "values": [float(v) for v in chart.values], "center": chart.center.tolist(),
        "lcl": chart.lcl.tolist(), "ucl": chart.ucl.tolist(), "alarms": [{"index": v.index, "rule": v.rule} for v in chart.violations],
        "intercept": chart.intercept, "slope": chart.slope, "sigma": chart.sigma, "r2": chart.r2, "slope_p": chart.slope_p,
        "slope_ci": list(chart.slope_ci), "df": chart.df, "trend_significant": bool(chart.slope_p < 0.01),
        "drift_per_cycle": chart.slope * ((cycle if cycle else k) - 1),
        "sigma_in": sigma_in, "sigma_res_values": chart.sigma * math.sqrt(n_sub) if n_sub > 1 else chart.sigma,
        "indices": None, "warnings": [],
    }
    if sigma_in:
        out["residual_ratio"] = chart.sigma * math.sqrt(n_sub) / sigma_in  # about 1 when the trend is the only systematic part
    if k < 20:
        out["warnings"].append({"code": "trend_few_points", "n": k})
    if lsl is not None or usl is not None:
        allv = np.asarray(values, dtype=float)
        try:
            cands = fit_candidates(allv, tuple(f for f in FAMILIES if f != "empirical"))
            if distribution == "auto":
                chosen = choose_automatically(cands)
                family = chosen.family
                comps = len(chosen.dist.w) if isinstance(chosen.dist, GaussianMixture) else None
            else:
                family, comps = distribution, None
            dist = fit(allv, family, components=comps)
            idx = geometric_indices(dist, lsl, usl) if method == "G" else zscore_indices(dist, lsl, usl)
            out["indices"] = {"pk": float(idx.pk), "p": None if idx.p is None else float(idx.p), "distribution": family,
                              "method": ("General Geometric (.G)" if method == "G" else "z-score (.Z)"), "ppm": float(ppm_out_of_spec(dist, lsl, usl)), "n": int(allv.size)}
        except FitError as exc:
            raise ValueError(f"the distribution could not be fitted: {exc}") from None
    return out


# ---------------------------------------------------------------- results kept in a report and its archive (annex E)
SPECIAL_ITEMS = ("scope", "multistage", "nested", "trend", "gdt", "multivariate", "cavities")
_DROP = {"gdt": ("clearance", "position_deviation")}  # per-part arrays are made again from the stored request: the archive keeps the figures


def run_special(name: str, req: dict, dataset: Dataset) -> dict:
    """One special-case result from its request. The report and the check of an archive both come through here, so they cannot differ."""
    from spc.core import gdt, multivariate_perf as mvperf

    if name == "scope":
        return mst.inspection_scope(req["components_per_carrier"], req["carriers"], req["spindles"], req["machines"],
                                    geometrically_identical=req.get("geometrically_identical", 0), measured_carriers=req.get("measured_carriers"),
                                    per_combination=req.get("per_combination", mst.PER_COMBINATION), minimum_total=req.get("minimum_total", mst.MINIMUM_TOTAL)) | {
            "spindle_form": mst.spindle_distribution(req.get("minimum_total", mst.MINIMUM_TOTAL), req["spindles"])}
    if name == "multistage":
        return multistage_for_dataset(dataset, req["factors"], req.get("lsl"), req.get("usl"), alpha=req.get("alpha", 0.05),
                                      per_combination=req.get("per_combination", mst.PER_COMBINATION), minimum_total=req.get("minimum_total", mst.MINIMUM_TOTAL))
    if name == "nested":
        return nested_for_dataset(dataset, req["levels"], req.get("alpha", 0.05))
    if name == "cavities":
        return cavity_study(dataset, req["factor"], req.get("lsl"), req.get("usl"), req.get("alpha", 0.05))
    if name == "trend":
        return trend_for_dataset(dataset, req.get("cycle"), req.get("subgroup_size"), req.get("lsl"), req.get("usl"), req.get("distribution", "auto"), req.get("method", "G"))
    if name == "gdt":
        out = gdt.analyse_clearance(req["xd"], req.get("xp"), dx=req.get("dx"), dy=req.get("dy"), kind=req["kind"], requirement=req["requirement"], lower=req["lower"],
                                    upper=req["upper"], position_tolerance=req["position_tolerance"], distribution=req.get("distribution", "auto"),
                                    method=req.get("method", "G"), bootstrap_n=req.get("bootstrap_n", 200), confidence=req.get("confidence", 0.95), seed=req.get("seed", 20260701))
        return {k: v for k, v in out.items() if k not in _DROP["gdt"]}
    if name == "multivariate":
        return mvperf.performance(req["data"], req["lower"], req["upper"], req.get("names"))
    raise ValueError(f"unknown special result {name!r}")


def special_snapshot(requests: dict, dataset: Dataset) -> dict:
    """{name: {"request": ..., "result": ...}} for the requested items, in a fixed order."""
    unknown = sorted(set(requests) - set(SPECIAL_ITEMS))
    if unknown:
        raise ValueError(f"unknown special result(s): {unknown}")
    return {name: {"request": requests[name], "result": run_special(name, requests[name], dataset)} for name in SPECIAL_ITEMS if requests.get(name) is not None}

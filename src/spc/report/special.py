"""Annex E of a report: the special-case results of draft 8.5 and 10.3.1 that were put into it. Shared by the HTML and the Excel report."""

from __future__ import annotations


def _g(x, d: int = 4) -> str:
    return "–" if x is None else f"{x:.{d}g}"


def _p(x) -> str:
    return "–" if x is None else ("< 0.001" if x < 0.001 else f"{x:.2g}")


def special_view(special: dict, L) -> list[dict]:
    """A list of sections {title, lines, heads, rows, tail}; empty parts are empty lists."""
    out = []
    for name in ("scope", "multistage", "nested", "trend", "gdt", "multivariate"):
        item = special.get(name)
        if item:
            out.append({"title": L(f"sx.{name}"), **_VIEWS[name](item["request"], item["result"], L)})
    return out


def _scope(req, r, L):
    f = r["factors"]
    lines = [L("sx.scope_factors", c=f["components_per_carrier"], k=f["carriers"], s=f["spindles"], m=f["machines"], ident=r["geometrically_identical"]),
             L("sx.scope_result", combos=r["combinations"], inspected=r["inspected_combinations"], per=r["parts_per_combination"], total=r["total_parts"],
               full=r["full_acceptance_parts"], saving=r["saving"])]
    if r["parts_other_carriers"]:
        lines.append(L("sx.scope_other", n=r["parts_other_carriers"], study=r["parts_in_study"]))
    lines.append(L("sx.scope_form", total=r["spindle_form"]["total"], list=" / ".join(str(v) for v in r["spindle_form"]["per_spindle"])))
    return {"lines": lines, "heads": [], "rows": [], "tail": []}


def _multistage(req, r, L):
    lines = [L("sx.ms_overall", factors=", ".join(r["factors"]), n=r["n"], mean=_g(r["mean"], 6), sd=_g(r["sd"]))]
    if r["indices"]:
        lines.append(L("sx.ms_indices", pp=_g(r["indices"]["p"], 3), ppk=_g(r["indices"]["pk"], 3)))
    b = r["between"]
    if b["anova"]:
        lines.append(L("sx.ms_anova", f=_g(b["anova"]["f"]), df1=b["anova"]["df1"], df2=b["anova"]["df2"], p=_p(b["anova"]["p_value"]),
                       result=L("sx.differ" if b["anova"]["different"] else "sx.same")))
    if b["variances"]:
        lines.append(L("sx.ms_var", p=_p(b["variances"]["p_value"]), result=L("sx.differ" if b["variances"]["different"] else "sx.same")))
    if b["eta_squared"] is not None:
        lines.append(L("sx.ms_eta", eta=_g(100 * b["eta_squared"], 3), pooled=_g(b["pooled_sd"])))
    heads = [L(k) for k in ("sx.col_combination", "sx.col_n", "sx.col_mean", "sx.col_sd", "sx.col_delta", "sx.col_p", "sx.col_ppk", "sx.col_remark")]
    rows = [[" / ".join(c["labels"][k] for k in r["factors"]), str(c["n"]), _g(c["mean"], 6), _g(c["sd"]), _g(c["delta"], 3), _p(c["p_value"]), _g(c["ppk"], 3),
             L("sx.deviates") if c["different"] else (L("sx.short") if c["short"] else "")] for c in r["combinations"]]
    cov = r["coverage"]
    tail = [L("sx.ms_bonferroni", alpha=_g(r["bonferroni_alpha"], 3)),
            L("sx.ms_cov_ok" if cov["complete"] else "sx.ms_cov_gaps", combos=cov["combinations"], expected=cov["expected_combinations"], missing=cov["missing_count"],
              short=cov["short_count"], per=cov["per_combination"], total=cov["total"], min=cov["minimum_total"]), L("sx.ms_aid")]
    return {"lines": lines, "heads": heads, "rows": rows, "tail": tail}


def _nested(req, r, L):
    lines = [L("sx.nest_result", levels=" > ".join(r["levels"]), n=r["n"], sd=_g(r["total_sd"]), largest=r["largest"])]
    heads = [L(k) for k in ("sx.col_level", "sx.col_groups", "sx.col_df", "sx.col_ms", "sx.col_f", "sx.col_p", "sx.col_var", "sx.col_sd", "sx.col_share", "sx.col_remark")]
    rows = [[t["level"], str(t["groups"]), str(t["df"]), _g(t["ms"]), _g(t["f"]), _p(t["p_value"]), _g(t["variance"]), _g(t["sd"]), _g(None if t["share"] is None else 100 * t["share"], 3),
             L("sx.truncated") if t["truncated"] else ""] for t in r["table"]]
    e = r["error"]
    rows.append([L("sx.error_level"), str(e["groups"]), str(e["df"]), _g(e["ms"]), "–", "–", _g(e["variance"]), _g(e["sd"]), _g(None if e["share"] is None else 100 * e["share"], 3), ""])
    tail = ([L("sx.nest_unbalanced")] if not r["balanced"] else []) + [L("sx.nest_method")]
    return {"lines": lines, "heads": heads, "rows": rows, "tail": tail}


def _trend(req, r, L):
    lines = [L("sx.trend_setup", n=r["n_points"], size=r["subgroup_size"], cycle=req.get("cycle") or "–"),
             L("sx.trend_line", slope=_g(r["slope"]), lo=_g(r["slope_ci"][0], 3), hi=_g(r["slope_ci"][1], 3), p=_p(r["slope_p"]), r2=_g(r["r2"], 3)),
             L("sx.trend_significant" if r["trend_significant"] else "sx.trend_none", drift=_g(r["drift_per_cycle"])),
             L("sx.trend_sigma", res=_g(r["sigma"]), n=r["n_points"]),
             L("sx.trend_alarms", n=len(r["alarms"]))]
    if r.get("residual_ratio") is not None:
        lines.append(L("sx.trend_ratio", ratio=_g(r["residual_ratio"], 3), sd=_g(r["sigma_in"])))
    if r["indices"]:
        i = r["indices"]
        lines.append(L("sx.trend_indices", pk=_g(i["pk"], 3), dist=i["distribution"], method=i["method"], ppm=_g(i["ppm"], 3), n=i["n"]))
    return {"lines": lines, "heads": [], "rows": [], "tail": [L("sx.trend_stability")]}


def _gdt(req, r, L):
    q, ix, o = r["quantiles"], r["indices"], r["observed"]
    lines = [L("sx.gdt_setup", kind=L("sx.kind_" + r["kind"]), req=L("sx.req_" + r["requirement"]), lower=_g(r["lower"], 6), upper=_g(r["upper"], 6),
               tp=_g(r["position_tolerance"], 6), vs=_g(r["virtual_size"], 6)),
             L("sx.gdt_quantiles", c50=_g(q["c_50"]), low=_g(q["c_0135"]), high=_g(q["c_99865"]), dist=r["distribution"]["name"]),
             L("sx.gdt_pk", pk=_g(ix["pk"], 3), method=ix["method"], ppm=_g(ix["ppm"], 3))]
    if ix["ci_pk"]:
        lines.append(L("sx.gdt_ci", lo=_g(ix["ci_pk"][0], 3), hi=_g(ix["ci_pk"][1], 3), conf=round(100 * ix["ci_confidence"])))
    lines.append(L("sx.gdt_observed", n=r["n"], neg=o["negative"], size=o["size_out"], pos=o["position_out"]))
    tail = [L("sx.gdt_sign")] if r.get("sign_note") else []
    return {"lines": lines, "heads": [], "rows": [], "tail": tail}


def _multivariate(req, r, L):
    lines = [L("sx.mv_result", pm=_g(r["pm"], 3), pmk=_g(r["pmk"], 3), n=r["n"], d=r["d"]),
             L("sx.mv_case1" if r["centre_inside"] else "sx.mv_case2", c=_g(r["c_pmk"]), p=_g(100 * r["p_pmk"], 5)),
             L("sx.mv_pm_detail", c=_g(r["c_pm"]), p=_g(100 * r["p_pm"], 5))]
    if r["expected_outside"] is not None:
        lines.append(L("sx.mv_outside", share=_g(100 * r["expected_outside"], 3)))
    heads = [L(k) for k in ("sx.col_name", "sx.col_mean", "sx.col_sd", "sx.col_lower", "sx.col_upper", "sx.col_pm_single", "sx.col_pmk_single")]
    rows = [[u["name"], _g(u["mean"], 6), _g(u["sd"]), _g(lo, 6), _g(up, 6), _g(u["pm"], 3), _g(u["pmk"], 3)] for u, lo, up in zip(r["univariate"], r["lower"], r["upper"])]
    nm = r["normality"]
    return {"lines": lines, "heads": heads, "rows": rows, "tail": [L("sx.mv_normality", sp=_p(nm["skewness_p"]), kp=_p(nm["kurtosis_p"])), L("sx.mv_reading")]}


_VIEWS = {"scope": _scope, "multistage": _multistage, "nested": _nested, "trend": _trend, "gdt": _gdt, "multivariate": _multivariate}

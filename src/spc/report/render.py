"""Report layout: one self-contained HTML file with inline SVG. Print it to PDF from any browser.

No script, no external file. Every dynamic string goes through `esc`.
"""

from __future__ import annotations

from html import escape

from spc.report.builder import Report
from spc.report.texts import T

CSS = """
@page { size: A4; margin: 14mm; }
* { box-sizing: border-box; }
body { margin: 0; font: 10.5pt/1.45 "Noto Sans TC", "Microsoft JhengHei", "WenQuanYi Zen Hei", system-ui, sans-serif; color: #1c2430; background: #fff; }
main { max-width: 190mm; margin: 0 auto; padding: 8mm 0; }
h1 { font-size: 18pt; margin: 0; }
h2 { font-size: 11.5pt; margin: 0 0 4px; }
.sub { color: #59626e; margin: 2px 0 8px; }
.head { display: flex; justify-content: space-between; gap: 12px; border-bottom: 2px solid #1f5fbf; padding-bottom: 6px; margin-bottom: 10px; }
.head .id { text-align: right; color: #59626e; font-size: 9pt; }
.summary { border: 2px solid #1f5fbf; border-radius: 6px; padding: 8px 12px; margin: 10px 0 14px; break-inside: avoid; }
.summary .big { font-size: 15pt; font-weight: 700; }
.ok { color: #1f7a4d; font-weight: 600; } .bad { color: #c62828; font-weight: 600; } .warn { color: #8a5a00; font-weight: 600; }
section.el { margin: 0 0 8px; break-inside: avoid; }
section.el > h2 { background: #eef2f8; border-left: 4px solid #1f5fbf; padding: 3px 8px; }
section.el > h2 .no { display: inline-block; min-width: 1.8em; margin-right: 6px; color: #1f5fbf; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; }
th, td { border: 1px solid #d3d9e2; padding: 3px 7px; text-align: left; vertical-align: top; }
th { background: #f6f8fb; font-weight: 600; }
table.kv th { width: 38%; font-weight: 500; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.figs { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.figs .wide { grid-column: 1 / -1; }
.fig { break-inside: avoid; border: 1px solid #d3d9e2; border-radius: 4px; padding: 4px; }
.fig svg { display: block; width: 100%; height: auto; }
ul.concl { margin: 4px 0 0 18px; padding: 0; }
.muted { color: #59626e; font-size: 9pt; }
.note { white-space: pre-wrap; }
footer { margin-top: 12px; border-top: 1px solid #d3d9e2; padding-top: 6px; color: #59626e; font-size: 8.5pt; }
@media print { main { padding: 0; } a { color: inherit; text-decoration: none; } }
"""


def esc(x) -> str:
    return escape("" if x is None else str(x), quote=True)


def _f(x, d=2) -> str:
    return "–" if x is None else f"{x:.{d}f}"


def _sig(x, digits=6) -> str:
    return "–" if x is None else f"{float(f'{x:.{digits}g}'):g}"


def render_html(rep: Report) -> str:
    lang, m, f, r = rep.lang, rep.meta, rep.facts, rep.result
    L = lambda key, **p: T(lang, key, **p)
    unit = f" {esc(m.unit)}" if m.unit else ""

    def given(text) -> str:
        t = (text or "").strip()
        return esc(t) if t else f'<span class="muted">{esc(L("doc.not_given"))}</span>'

    def kv(rows) -> str:
        body = "".join(f"<tr><th>{esc(k)}</th><td>{v}</td></tr>" for k, v in rows)
        return f'<table class="kv">{body}</table>'

    def element(no: int, inner: str, cls: str = "") -> str:
        return f'<section class="el {cls}"><h2><span class="no">{no}</span>{esc(L(f"el.{no}"))}</h2>{inner}</section>'

    sep = L("doc.list_sep")
    ix, tg, st = r["indices"], r["targets"], r["stability"]
    name_p, name_pk = f["name_p"], f["name_pk"]
    level = f["level"]

    # ---------------- summary box
    verdicts = []
    if tg and not tg.get("blocked"):
        verdicts = [v for v in (tg["verdict_p"], tg["verdict_pk"]) if v]
    if verdicts:
        overall = "ok" if all(v == "meets" for v in verdicts) else "bad"
        overall_text = L("c.overall_met" if overall == "ok" else "c.overall_not_met")
    else:
        overall, overall_text = "warn", L("v.verdict_none")
    cls = st["class"] if st["assessed"] else "not_tested"
    values = f"{esc(name_pk)} = {_f(ix['pk'])}" + (f" &nbsp;·&nbsp; {esc(name_p)} = {_f(ix['p'])}" if ix["p"] is not None else "")
    summary = (
        f'<div class="summary"><div class="big">{values}</div>'
        f'<div class="{overall}">{esc(overall_text)}</div>'
        f'<div class="muted">{esc(L("v.class_" + cls))} · n = {f["n"]} · {esc(L("v.chart_" + f["chart_kind"]))}</div></div>'
    )

    # ---------------- elements 1-10
    e1 = kv([(L("f.process"), given(m.process)), (L("f.machine"), given(m.machine)), (L("f.site"), given(m.site))])
    e2 = kv([(L("f.process_ref"), given(m.process_ref)), (L("f.machine_ref"), given(m.machine_ref))])
    e3 = f'<p class="note">{given(m.persons)}</p>'
    if f["period"]:
        p = f["period"]
        e4 = kv([(L("f.start"), esc(p["start"])), (L("f.end"), esc(p["end"])), (L("f.duration"), esc(p["duration"])), ("", f'<span class="muted">{esc(L("v.period_from_data"))}</span>')])
    else:
        e4 = f'<p class="note">{given(m.period_text)}</p>'
    e5 = kv([(L("f.part_name"), given(m.part_name)), (L("f.part_number"), given(m.part_number))])
    rows6 = [(L("f.characteristic"), given(m.characteristic))]
    if m.unit:
        rows6.append((L("f.unit"), esc(m.unit)))
    rows6.append((L("f.lsl"), f"{_sig(rep.request.lsl)}{unit}" if rep.request.lsl is not None else "–"))
    rows6.append((L("f.usl"), f"{_sig(rep.request.usl)}{unit}" if rep.request.usl is not None else "–"))
    if f["tolerance"] is not None:
        rows6.append((L("f.tolerance"), f"{_sig(f['tolerance'])}{unit}"))
    if m.target is not None:
        rows6.append((L("f.target"), f"{_sig(m.target)}{unit}"))
    e6 = kv(rows6)
    e7 = f'<p class="note">{given(m.technical_conditions)}</p>'
    e8 = f'<p class="note">{given(m.deviations)}</p>'
    e9 = kv([
        (L("f.n_total"), f["n_total"]), (L("f.n_marked"), f["n_marked"]), (L("f.n_unused"), f["n_unused"]),
        (L("f.n_used"), f"<b>{f['n']}</b>"),
    ])
    rows10 = [(L("f.subgroup_size"), f["subgroup_size"]), (L("f.subgroups"), f["k"])]
    if f["interval_min"] is not None:
        rows10.append((L("f.frequency_measured"), esc(L("v.interval_min", value=f"{f['interval_min']:.1f}"))))
    if m.sampling_frequency.strip():
        rows10.append((L("f.frequency_text"), esc(m.sampling_frequency)))
    e10 = kv(rows10)

    # ---------------- figures 11-14
    def fig(no: int, key: str, wide=False) -> str:
        return f'<div class="fig{" wide" if wide else ""}"><div class="muted">{no}. {esc(L(f"el.{no}"))}</div>{rep.figures[key]}</div>'

    figs = f'<div class="figs">{fig(11, "hist")}{fig(13, "prob")}{fig(12, "run", True)}'
    if "control_location" in rep.figures:
        figs += f'{fig(14, "control_location", True)}{fig(14, "control_variation", True)}'
    figs += "</div>"
    if "control_location" in rep.figures:
        crit = "; ".join(f["criteria"]) or "–"
        mode = L("v.mode_" + r["params"]["stability_mode"])
        e14 = kv([
            (L("f.chart"), esc(L("v.chart_" + f["chart_kind"]))),
            *(
                [(L("f.moving"), esc(L("v.moving_line", n=f["moving_n"], k=len(f["restarts"]))))]
                if f["moving_n"] else []
            ),
            *(
                [(L("f.phase", k=i + 1), esc(L("v.phase_line", row=p["from_row"], n=p["n"], mu=_sig(p["mu"], 5), sigma=_sig(p["sigma"], 4))))
                 for i, p in enumerate(f["phases"])]
            ),
            (L("f.risk"), esc(_sig(f["alpha"], 4))),
            (L("f.criteria"), esc(crit)),
            (L("f.stability_mode"), esc(mode)),
            (L("f.stable"), esc(L("v.class_" + st["class"]))),
            ("", esc(L("v.points_checked", n=st["n_alarm_points"], k=st["n_points_checked"], expected=_f(st["expected_false_alarms"]), threshold=st["threshold"]))),
        ])
    else:
        e14 = f'<p>{esc(L("v.not_for_machine"))}</p>'

    # ---------------- elements 15-19
    ci = L("f.ci", level=level)
    e15 = kv([
        (L("f.mean"), f"{_sig(f['mean'])}{unit}"),
        (ci, f"{_sig(f['mean_ci'][0])} – {_sig(f['mean_ci'][1])}{unit}"),
        (L("f.median"), f"{_sig(f['q_mid'])}{unit}"),
        (L("f.method"), esc(L("v.median_fitted" if f["dist"] else "v.mean_method"))),
    ])
    e16 = kv([
        (L("f.sd"), f"{_sig(f['sd'])}{unit}"),
        (ci, f"{_sig(f['sd_ci'][0])} – {_sig(f['sd_ci'][1])}{unit}"),
        (L("f.q_low"), f"{_sig(f['q_low'])}{unit}"),
        (L("f.q_high"), f"{_sig(f['q_high'])}{unit}"),
        (L("f.spread"), f"{_sig(f['q_high'] - f['q_low'])}{unit}"),
        (L("f.method"), esc(L("v.sd_method"))),
    ])
    nm = f["normality"]
    d = f["dist"]
    if d:
        params = "; ".join(f"{k} = {_sig(v, 5) if not isinstance(v, list) else '[' + ', '.join(_sig(x, 5) for x in v) + ']'}"
                           for k, v in d["params"].items())
        how = L("v.how_auto" if d["requested"] == "auto" else "v.how_manual")
        e17_rows = [(L("f.model"), esc(L("v.fitted_model", name=L("v.dist_" + d["name"]), how=how, params=params)))]
        chosen = next((c for c in d["candidates"] if c["family"] == d["name"] and c["ok"]), None)
        if chosen:
            e17_rows.append(("", esc(L("v.fit_quality", aic=_f(chosen["aic"], 1), ad=_f(chosen["ad"], 2)))))
    else:
        e17_rows = [(L("f.model"), esc(L("v.normal_assumed")))]
    if nm and nm["p_value"] is not None:
        e17_rows.append((L("f.normality"), esc(L("v.p_value", test=nm["test"], p=f"{nm['p_value']:.4f}"))))
    e17 = kv(e17_rows)
    rows18 = []
    if tg and not tg.get("blocked"):
        rows18.append((L("f.target_class"), esc(L("v.class_" + tg["class"]))))
        if tg["p"] is not None and ix["p"] is not None:
            rows18.append((L("f.target_p", name=name_p), f"≥ {_f(tg['p'])}"))
        rows18.append((L("f.target_pk", name=name_pk), f"≥ {_f(tg['pk'])}"))
        rows18.append((L("f.target_base"), tg["n_base"]))
        rows18.append((L("f.target_used"), tg["n"]))
        rows18.append((L("f.confidence_target"), esc(f"{tg['confidence'] * 100:g} %")))
    elif tg:
        rows18.append((L("f.target_class"), esc(L("v.class_" + tg["class"]))))
        rows18.append(("", esc(L("c.target_blocked", base=tg["n_base"]))))
    else:
        rows18.append(("", esc(L("c.no_target"))))
    rows18.append((L("f.confidence_est"), esc(level)))
    if d:
        b = d["bootstrap"]
        interval = L("v.interval_boot", used=b["succeeded"], requested=b["requested"], seed=b["seed"]) if b and ix["ci_pk"] else L("v.interval_none")
        rows18.append((L("f.method"), esc(L("v.method_" + f["method"], interval=interval))))
    else:
        rows18.append((L("f.method"), esc(L("v.normal_total"))))
    e18 = kv(rows18)

    def idx_row(name, value, ci_, target, verdict) -> str:
        tcell = _f(target) if target is not None else "–"
        vcell = esc(L("v.verdict_" + verdict)) if verdict else esc(L("v.verdict_none"))
        cls_ = {"meets": "ok", "meets_estimate_only": "warn", "meets_no_interval": "warn", "fails": "bad"}.get(verdict or "", "")
        return (f'<tr><td>{esc(name)}</td><td class="num">{_f(value)}</td><td class="num">{_f(ci_[0]) + " – " + _f(ci_[1]) if ci_ else "–"}</td>'
                f'<td class="num">{tcell}</td><td class="{cls_}">{vcell}</td></tr>')

    ok_tg = tg and not tg.get("blocked")
    rows19 = ""
    if ix["p"] is not None:
        rows19 += idx_row(name_p, ix["p"], ix["ci_p"], tg["p"] if ok_tg else None, tg["verdict_p"] if ok_tg else None)
    rows19 += idx_row(name_pk, ix["pk"], ix["ci_pk"], tg["pk"] if ok_tg else None, tg["verdict_pk"] if ok_tg else None)
    t19 = (f'<table><tr><th>{esc(L("f.index"))}</th><th class="num">{esc(L("f.value"))}</th><th class="num">{esc(ci)}</th>'
           f'<th class="num">{esc(L("f.target_col"))}</th><th>{esc(L("f.verdict"))}</th></tr>{rows19}</table>')
    share = []
    if f["ppm_below"] is not None:
        share.append((L("f.below_lsl"), f"{f['ppm_below']:.2f} ppm ({f['ppm_below'] / 1e4:.5f} %)"))
    if f["ppm_above"] is not None:
        share.append((L("f.above_usl"), f"{f['ppm_above']:.2f} ppm ({f['ppm_above'] / 1e4:.5f} %)"))
    share.append((L("f.total_out"), f"<b>{f['ppm_total']:.2f} ppm</b>"))
    e19 = t19 + '<div style="height:6px"></div>' + kv(share)

    # ---------------- 20, 21, 22
    items = "".join(f"<li>{esc(c)}</li>" for c in rep.conclusions)
    e20 = f'<ul class="concl">{items}</ul>'
    if m.recommendations.strip():
        e20 += f'<p><b>{esc(L("c.recommendations"))}</b></p><p class="note">{esc(m.recommendations)}</p>'
    model = rep.request.model
    e21 = kv([
        (L("f.model"), esc(L("v.model_" + model) if model else L("v.model_unknown"))),
        (L("f.controlled"), esc(L("v.yes" if rep.request.controlled_stable else "v.no"))),
    ])
    g = f["guard"]
    if g:
        rows22 = [
            (L("f.uncertainty"), f"{_sig(g['U'])}{unit}"), (L("f.coverage"), _sig(g["k"])),
            (L("f.std_uncertainty"), f"{_sig(g['u'])}{unit}"),
            (L("f.guard_band", z=f"{g['z']:.3f}", risk=f"{g['risk'] * 100:g} %"), f"<b>{_sig(g['g'])}</b>{unit}"),
        ]
        if g["accept_low"] is not None:
            rows22.append((L("f.accept_low"), f"{_sig(g['accept_low'])}{unit}"))
        if g["accept_high"] is not None:
            rows22.append((L("f.accept_high"), f"{_sig(g['accept_high'])}{unit}"))
        e22 = kv(rows22)
    else:
        e22 = f'<p class="muted">{esc(L("doc.not_given"))}</p>'

    # ---------------- annexes
    if rep.marked:
        body = "".join(
            f'<tr><td class="num">{x["source_row"]}</td><td class="num">{_sig(x["value"])}</td><td>{esc(x["reason"])}</td>'
            f'<td>{esc(x["by"])}</td><td>{esc(x["at"])}</td></tr>' for x in rep.marked
        )
        annex_a = (f'<table><tr><th class="num">{esc(L("f.col_row"))}</th><th class="num">{esc(L("f.col_value"))}</th>'
                   f'<th>{esc(L("f.col_reason"))}</th><th>{esc(L("f.col_by"))}</th><th>{esc(L("f.col_at"))}</th></tr>{body}</table>')
    else:
        annex_a = f'<p class="muted">{esc(L("f.none"))}</p>'
    if f["restarts"]:
        body = "".join(
            f'<tr><td class="num">{x["source_row"]}</td><td>{esc(x["reason"])}</td><td>{esc(L("v.new_limits") if x["new_limits"] else "")}</td>'
            f'<td>{esc(x["by"])}</td><td>{esc(x["at"])}</td></tr>'
            for x in f["restarts"]
        )
        annex_a += (f'<h3>{esc(L("f.restarts_title"))}</h3><table><tr><th class="num">{esc(L("f.col_row"))}</th>'
                    f'<th>{esc(L("f.col_reason"))}</th><th>{esc(L("f.col_phase"))}</th><th>{esc(L("f.col_by"))}</th><th>{esc(L("f.col_at"))}</th></tr>{body}</table>')
    dropped = next((w for w in r["warnings"] if w["code"] == "dropped_subgroups"), None)
    if dropped:
        annex_a += f'<p class="muted">{esc(L("f.unused_labels", labels=", ".join(dropped["params"]["labels"])))}</p>'
    tr = rep.trace
    rows_b = [
        (L("f.engine"), esc(tr["engine_version"])), (L("f.fingerprint"), f"<code>{esc(tr['fingerprint'])}</code>"),
        (L("f.edition"), esc(tr["edition"])), (L("f.stability_mode"), esc(L("v.mode_" + tr["stability_mode"]))),
    ]
    if tr.get("customer"):
        rows_b.append((L("f.customer"), esc(tr["customer"])))
    if tr.get("source"):
        rows_b.append((L("f.source_file"), esc(tr["source"]["name"])))
        rows_b.append((L("f.source_hash"), f"<code>{esc(tr['source']['sha256'])}</code>"))
    if rep.archive_digest:
        rows_b.append((L("f.archive_digest"), f"<code>{esc(rep.archive_digest)}</code>"))
    annex_b = kv(rows_b)

    note = L("doc.draft_note") if tr["edition"] == "draft" else L("doc.final_note")
    stage_line = L("doc.stage_" + f["stage"])
    title = f"{L('doc.title')} {rep.report_id}"
    return (
        f'<!doctype html><html lang="{esc(lang)}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(title)}</title>'
        f"<style>{CSS}</style></head><body><main>"
        f'<div class="head"><div><h1>{esc(L("doc.title"))}</h1><p class="sub">{esc(L("doc.subtitle"))}</p>'
        f'<div>{esc(stage_line)}</div></div>'
        f'<div class="id">{esc(L("doc.report_id"))}: {esc(rep.report_id)}<br>{esc(L("doc.created"))}: {esc(rep.generated_at)}</div></div>'
        f"{summary}"
        + element(1, e1) + element(2, e2) + element(3, e3) + element(4, e4) + element(5, e5) + element(6, e6)
        + element(7, e7) + element(8, e8) + element(9, e9) + element(10, e10)
        + f'<section class="el" style="break-inside:auto"><h2><span class="no">11–14</span>{esc(sep.join(L(f"el.{n}") for n in (11, 12, 13, 14)))}</h2>{figs}</section>'
        + element(14, e14) + element(15, e15) + element(16, e16) + element(17, e17) + element(18, e18)
        + element(19, e19) + element(20, e20, "") + element(21, e21) + element(22, e22)
        + f'<section class="el"><h2>{esc(L("doc.annex_a"))}</h2>{annex_a}</section>'
        + f'<section class="el"><h2>{esc(L("doc.annex_b"))}</h2>{annex_b}</section>'
        + f"<footer>{esc(L('doc.agreed'))}<br>{esc(note)}</footer>"
        + "</main></body></html>"
    )


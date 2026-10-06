"""The validation report as one HTML page, English or Traditional Chinese."""

from __future__ import annotations

from html import escape as esc

from spc.validation.texts import EN, ZH


def _num(x) -> str:
    if x is None:
        return "–"
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, float):
        return f"{x:.6g}"
    return str(x)


def render_run(run: dict, lang: str = "en") -> str:
    T = ZH if lang == "zh-TW" else EN
    L = lambda k, **p: T[k].format(**p) if p else T[k]

    def tol(c) -> str:
        if c["tol"] is None:
            return L("tol_equal")
        parts = []
        if c["tol"]:
            parts.append(L("tol_rel", tol=f"{c['tol']:g}"))
        if c.get("abs_tol"):
            parts.append(L("tol_abs", tol=f"{c['abs_tol']:g}"))
        return ", ".join(parts) or L("tol_equal")

    def table(checks, area_label: bool) -> str:
        head = "".join(f"<th>{esc(L(k))}</th>" for k in ("col_id", "col_requirement", "col_reference", "col_expected", "col_actual", "col_tol", "col_result"))
        rows = []
        for c in checks:
            req = L(c["requirement"]) if c["requirement"] in T else c["requirement"]
            what = req + (f" ({c['note']})" if c["note"] else "")
            st = c.get("status") or ("pass" if c["ok"] else "fail")
            res = f'<td class="{ {"pass": "ok", "fail": "bad", "known": "known", "info": "info"}[st] }">{esc(L({"pass": "ok", "fail": "bad", "known": "known", "info": "info"}[st]))}</td>'
            rows.append(f"<tr><td>{esc(c['id'])}</td><td>{esc(what)}</td><td>{esc(c['reference'])}</td><td>{esc(_num(c['expected']))}</td><td>{esc(_num(c['actual']))}</td><td>{esc(tol(c))}</td>{res}</tr>")
        return f"<table><tr>{head}</tr>{''.join(rows)}</table>"

    v, val = run["verification"], run["validation"]
    areas: dict[str, list] = {}
    for c in v["checks"]:
        areas.setdefault(c["area"], []).append(c)
    ver_html = ""
    for area, cs in areas.items():
        if area.startswith("iso22514."):
            name = L("area." + area)
        elif area.startswith("iso11462."):
            name = L("area.iso11462_example", n=area.split(".")[1])
        else:
            name = L("area." + area) if ("area." + area) in T else area
        counts = {k: sum((c.get("status") or ("pass" if c["ok"] else "fail")) == k for c in cs) for k in ("pass", "known", "info", "fail")}
        ver_html += (f"<h3>{esc(name)} <span class=muted>({esc(L('summary_full', passed=counts['pass'], total=len(cs), known=counts['known'], info=counts['info'], failed=counts['fail']))})</span></h3>"
                     f"{table(cs, False)}")
    if val["cases"]:
        val_html = ""
        for case in val["cases"]:
            val_html += (f"<h3>{esc(case['name'])} <span class=muted>({esc(L('summary', passed=sum(c['ok'] for c in case['checks']), total=len(case['checks'])))})</span></h3>"
                         f"<p>{esc(case['description'])}</p><p>{esc(L('case_source', source=case['source']))}</p>{table(case['checks'], False)}")
    else:
        val_html = f"<p class=warn>{esc(L('no_cases'))}</p>"
    failed = v["failed"] + val["failed"]
    verdict = {"pass": L("verdict_pass"), "verified": L("verdict_verified"), "fail": L("verdict_fail", n=failed)}[run["verdict"]]
    params = "".join(f"<tr><th>{esc(k)}</th><td>{esc(_num(x) if not isinstance(x, dict) else ', '.join(f'{a}={_num(b)}' for a, b in x.items()))}</td></tr>" for k, x in run["parameters"].items())
    css = ("body{font:14px/1.5 system-ui,sans-serif;max-width:1000px;margin:2em auto;padding:0 1em;color:#1a1a1a}table{border-collapse:collapse;width:100%;margin:.5em 0}"
           "th,td{border:1px solid #bbb;padding:3px 6px;text-align:left;vertical-align:top}th{background:#eef}td.ok{color:#0a6b2d;font-weight:600}td.bad{color:#b00020;font-weight:700}td.known{color:#8a5a00;font-weight:600}td.info{color:#555}"
           ".muted{color:#666;font-weight:400}.warn{color:#8a5a00}.verdict{font-size:1.15em;font-weight:700}")
    return (f'<!doctype html><html lang="{esc(lang)}"><head><meta charset="utf-8"><title>{esc(L("title"))} {run["id"]}</title><style>{css}</style></head><body>'
            f'<h1>{esc(L("title"))}</h1><p class=muted>{esc(L("subtitle"))}</p>'
            f'<p>{esc(L("meta", id=run["id"], at=run["created_at"].replace("T", " ").replace("Z", ""), by=run["created_by"]))}<br>'
            f'{esc(L("engine", engine=run["engine_version"], python=run["python"], numpy=run["numpy"], scipy=run["scipy"]))}<br>'
            f'{esc(L("digest"))}: <code>{esc(run["digest"])}</code></p>'
            f'<p class="verdict {"ok" if run["verdict"] != "fail" else "bad"}">{esc(L("verdict"))}: {esc(verdict)}</p><p>{esc(L("terms"))}</p>'
            f'<h2>{esc(L("params"))}</h2><p>{esc(L("params_note"))}</p><table class="kv">{params}</table>'
            f'<h2>{esc(L("verification"))}</h2><p>{esc(L("summary_full", passed=sum((c.get("status") or ("pass" if c["ok"] else "fail")) == "pass" for c in v["checks"]), total=len(v["checks"]), known=v.get("known", 0), info=v.get("info", 0), failed=v["failed"]))}</p>'
            f'{"<p>" + esc(L("iso_note")) + "</p>" if any(c["area"].startswith(("iso11462.", "iso22514.")) for c in v["checks"]) else ""}{ver_html}'
            f'<h2>{esc(L("validation"))}</h2>{val_html}<p class=muted>{esc(L("limits"))}</p></body></html>')

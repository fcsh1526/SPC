"use strict";
/* SPC web interface. No build step and no external libraries.
 * All text comes from /static/i18n/<lang>.json through t(). Data from the user (labels, reasons,
 * file names) is only ever put into the page with textContent, never with innerHTML. */
(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const SVG_NS = "http://www.w3.org/2000/svg";

  const state = {
    lang: "zh-TW", msgs: {}, fallback: {},
    file: null, preview: null,
    dataset: null, offset: 0, pageSize: 100, pageRows: [], total: 0,
    selected: new Set(), suspects: new Set(),
    result: null, toolsTargets: null, toolsArl: null,
  };

  // ---------------------------------------------------------------- small helpers
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function svg(tag, attrs, text) {
    const e = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, String(v));
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function store(key, value) {
    try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (e) { return null; }
    return null;
  }
  function fmt(x, d = 2) {
    if (x === null || x === undefined || Number.isNaN(x)) return "–";
    return Number(x).toFixed(d);
  }
  function sig(x, digits = 6) {
    if (x === null || x === undefined) return "–";
    return String(Number(Number(x).toPrecision(digits)));
  }

  // ---------------------------------------------------------------- language
  function t(key, params) {
    const raw = key in state.msgs ? state.msgs[key] : (key in state.fallback ? state.fallback[key] : key);
    return raw.replace(/\{(\w+)\}/g, (_, k) => (params && k in params ? String(params[k]) : `{${k}}`));
  }
  async function fetchJson(url) {
    const r = await fetch(url);
    if (!r.ok) throw { code: "network", message: url, params: {} };
    return r.json();
  }
  async function loadMessages(lang) {
    const [msgs, fb] = await Promise.all([fetchJson(`/static/i18n/${lang}.json`), lang === "en" ? null : fetchJson("/static/i18n/en.json")]);
    state.msgs = msgs;
    state.fallback = fb || msgs;
    state.lang = lang;
  }
  function applyStatic() {
    document.documentElement.lang = state.lang;
    $$("[data-i18n]").forEach((e) => { e.textContent = t(e.dataset.i18n); });
    $$("[data-i18n-placeholder]").forEach((e) => { e.placeholder = t(e.dataset.i18nPlaceholder); });
    if (!state.file) $("#file-name").textContent = t("import.no_file");
  }
  function pickLanguage() {
    const saved = store("spc.lang");
    if (saved === "zh-TW" || saved === "en") return saved;
    return (navigator.language || "").toLowerCase().startsWith("zh") ? "zh-TW" : "en";
  }

  // ---------------------------------------------------------------- errors and busy state
  function errorText(e) {
    const key = "error." + e.code;
    if (!(key in state.msgs) && !(key in state.fallback)) return e.message || e.code;
    const p = Object.assign({}, e.params || {});
    if (Array.isArray(p.labels)) p.labels = p.labels.join(", ");
    if (e.code === "validation") p.fields = (p.errors || []).map((x) => x.field).join(", ");
    if (e.code === "invalid_input") p.message = e.message;
    return t(key, p);
  }
  function showError(err) {
    const box = $("#errors");
    box.replaceChildren();
    const inner = el("div", "box");
    inner.appendChild(el("strong", "", t("app.error_title")));
    inner.appendChild(el("p", "", errorText(err)));
    if (err.code === "import_failed" && err.params && err.params.issues) {
      const ul = el("ul");
      for (const issue of err.params.issues.slice(0, 20)) {
        const where = issue.line ? t("import.issue_line", { line: issue.line }) : t("import.issue_file");
        const key = "issue." + issue.code;
        const text = key in state.msgs || key in state.fallback ? t(key) : issue.message;
        const li = el("li", "", `${where}${issue.column ? " [" + issue.column + "]" : ""}: ${text}`);
        li.title = issue.message;
        ul.appendChild(li);
      }
      inner.appendChild(ul);
      if (err.params.n_issues > 20) inner.appendChild(el("p", "muted", t("import.issues_more", { shown: 20 })));
      inner.firstChild.textContent = t("import.issues_title", { n: err.params.n_issues });
    }
    const close = el("button", "", t("app.close"));
    close.addEventListener("click", clearError);
    inner.appendChild(close);
    box.appendChild(inner);
    box.hidden = false;
    box.scrollIntoView({ block: "nearest" });
  }
  function clearError() { const b = $("#errors"); b.hidden = true; b.replaceChildren(); }
  async function guarded(fn) {
    clearError();
    $("#busy").hidden = false;
    const buttons = $$("button");
    buttons.forEach((b) => { b.dataset.wasDisabled = b.disabled ? "1" : ""; b.disabled = true; });
    try { return await fn(); }
    catch (e) { showError(e && e.code ? e : { code: "network", message: String(e), params: {} }); }
    finally {
      $("#busy").hidden = true;
      buttons.forEach((b) => { b.disabled = b.dataset.wasDisabled === "1"; });
    }
  }
  async function api(path, opts) {
    let resp;
    try { resp = await fetch(path, opts); } catch (e) { throw { code: "network", message: String(e), params: {} }; }
    const isJson = (resp.headers.get("content-type") || "").includes("json");
    if (!resp.ok) {
      let data = null;
      if (isJson) { try { data = await resp.json(); } catch (e) { data = null; } }
      throw (data && data.error) || { code: "http_" + resp.status, message: resp.statusText, params: {} };
    }
    return isJson ? resp.json() : resp;
  }
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  // ---------------------------------------------------------------- tabs
  function showTab(name) {
    if ((name === "data" || name === "analysis") && !state.dataset) return;
    $$("nav.tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    $$("main > section").forEach((s) => { s.hidden = s.id !== "tab-" + name; });
  }
  function unlockTabs() { $$("nav.tabs button").forEach((b) => b.removeAttribute("aria-disabled")); }

  // ---------------------------------------------------------------- import
  const ROLE_SELECTS = ["#col-value", "#col-subgroup", "#col-timestamp", "#col-valid", "#col-reason", "#col-by", "#col-at", "#col-source-row"];

  function looksNumeric(cells, decimal) {
    const cleaned = cells.filter((c) => c !== "");
    if (!cleaned.length) return false;
    const re = decimal === "," ? /^-?\d+(,\d+)?$/ : /^-?\d+(\.\d+)?$/;
    return cleaned.every((c) => re.test(c.replace(/\s/g, "")));
  }
  function guessColumns() {
    const { header, rows } = state.preview;
    const decimal = $("#decimal").value;
    const timeRe = /time|date|時間|日期/i;
    const groupRe = /lot|batch|group|subgroup|子組|批|組/i;
    const guess = { value: "", subgroup: "", timestamp: "" };
    header.forEach((h, i) => {
      const col = rows.map((r) => r[i] || "");
      if (!guess.timestamp && timeRe.test(h)) guess.timestamp = h;
      else if (!guess.subgroup && groupRe.test(h)) guess.subgroup = h;
      else if (!guess.value && !timeRe.test(h) && looksNumeric(col, decimal)) guess.value = h;
    });
    return guess;
  }
  function fillSelects(initial) {
    const header = state.preview.header;
    for (const sel of ROLE_SELECTS) {
      const s = $(sel);
      const keep = initial ? (initial[sel] || "") : s.value;
      s.replaceChildren();
      s.appendChild(new Option(t("import.optional"), ""));
      header.forEach((h) => s.appendChild(new Option(h, h)));
      s.value = header.includes(keep) ? keep : "";
    }
  }
  function renderTagChecks() {
    const box = $("#col-tags");
    const chosen = new Set($$("input", box).filter((i) => i.checked).map((i) => i.value));
    box.replaceChildren();
    const used = new Set(ROLE_SELECTS.map((s) => $(s).value).filter(Boolean));
    state.preview.header.filter((h) => !used.has(h)).forEach((h) => {
      const label = el("label", "check");
      const cb = el("input"); cb.type = "checkbox"; cb.value = h; cb.checked = chosen.has(h);
      label.appendChild(cb); label.appendChild(el("span", "", h));
      box.appendChild(label);
    });
  }
  function renderPreview() {
    const table = $("#preview");
    table.replaceChildren();
    const picks = new Set(ROLE_SELECTS.map((s) => $(s).value).filter(Boolean));
    const head = el("tr");
    state.preview.header.forEach((h) => { const th = el("th", picks.has(h) ? "picked" : "", h); head.appendChild(th); });
    table.appendChild(head);
    state.preview.rows.forEach((r) => {
      const tr = el("tr");
      state.preview.header.forEach((h, i) => tr.appendChild(el("td", picks.has(h) ? "picked" : "", r[i] || "")));
      table.appendChild(tr);
    });
  }
  function renderDetected() {
    $("#detected").textContent = t("import.detected", {
      encoding: state.preview.encoding,
      delimiter: state.preview.delimiter === "\t" ? "Tab" : state.preview.delimiter,
    });
  }
  function refreshImportForm() { renderTagChecks(); renderPreview(); }

  async function onFileChosen(file) {
    clearError();
    if (!file) return;
    state.file = file;
    $("#file-name").textContent = file.name;
    await guarded(async () => {
      state.preview = await api("/api/preview", { method: "POST", body: file });
      const g = guessColumns();
      fillSelects({ "#col-value": g.value, "#col-subgroup": g.subgroup, "#col-timestamp": g.timestamp });
      renderDetected();
      refreshImportForm();
      $("#import-form").hidden = false;
    });
  }
  async function doImport() {
    if (!state.file) return showError({ code: "no_file", params: {} });
    if (!$("#col-value").value) return showError({ code: "no_value_column", params: {} });
    const q = new URLSearchParams();
    const map = { value: "#col-value", subgroup: "#col-subgroup", timestamp: "#col-timestamp", valid: "#col-valid",
      invalid_reason: "#col-reason", invalid_by: "#col-by", invalid_at: "#col-at", source_row: "#col-source-row" };
    for (const [k, sel] of Object.entries(map)) { const v = $(sel).value; if (v) q.set(k, v); }
    $$("#col-tags input").filter((i) => i.checked).forEach((i) => q.append("tags", i.value));
    q.set("decimal", $("#decimal").value);
    q.set("missing", $("#missing").value);
    q.set("filename", state.file.name);
    await guarded(async () => {
      const ds = await api("/api/datasets?" + q.toString(), { method: "POST", body: state.file });
      state.dataset = ds;
      state.offset = 0; state.selected.clear(); state.suspects.clear(); state.result = null;
      $("#result").hidden = true;
      $("#a-size-wrap").hidden = ds.has_subgroup;
      $("#export-link").href = `/api/datasets/${ds.id}/export.csv`;
      $("#file-name").textContent = t("import.done", { n: ds.summary.n_total, name: state.file.name });
      unlockTabs();
      await loadRows();
      renderData();
      showTab("data");
    });
  }

  // ---------------------------------------------------------------- data tab
  function renderWarnings(ul, list) {
    ul.replaceChildren();
    (list || []).forEach((w) => {
      const p = Object.assign({}, w.params || {});
      if (Array.isArray(p.labels)) p.labels = p.labels.join(", ");
      if (typeof p.p_value === "number") p.p_value = fmt(p.p_value, 4);
      ul.appendChild(el("li", "", t("warn." + w.code, p)));
    });
  }
  function renderData() {
    const ds = state.dataset;
    if (!ds) return;
    const s = ds.summary;
    let line = t("data.counts", { total: s.n_total, valid: s.n_effective, invalid: s.n_invalid });
    if (s.k_subgroups) line += " · " + t("data.subgroups", { k: s.k_subgroups });
    $("#data-counts").textContent = line;
    const src = $("#data-source");
    if (ds.source) {
      src.textContent = t("data.source", { name: ds.source.name, encoding: ds.source.encoding, hash: ds.source.sha256.slice(0, 12) + "…" });
      src.title = ds.source.sha256;
    } else { src.textContent = ""; }
    renderWarnings($("#data-warnings"), ds.warnings);
    $("#selected-count").textContent = state.selected.size ? t("data.selected", { n: state.selected.size }) : t("data.select_hint");
    renderRows();
    renderLog();
  }
  function renderLog() {
    const ul = $("#log");
    ul.replaceChildren();
    const log = state.dataset.log;
    if (!log.length) { ul.appendChild(el("li", "muted", t("data.log_empty"))); return; }
    log.forEach((e) => {
      const action = t(e.action === "mark_invalid" ? "data.log_mark_invalid" : "data.log_restore");
      const li = el("li", "", t("data.log_line", { action, n: e.positions.length, by: e.by, at: e.at }));
      li.appendChild(el("div", "muted", "“" + e.reason + "”"));
      ul.appendChild(li);
    });
  }
  async function loadRows() {
    const r = await api(`/api/datasets/${state.dataset.id}/rows?offset=${state.offset}&limit=${state.pageSize}`);
    state.pageRows = r.rows; state.total = r.total;
  }
  function renderRows() {
    const ds = state.dataset;
    const table = $("#rows");
    table.replaceChildren();
    const head = el("tr");
    const all = el("input"); all.type = "checkbox";
    all.checked = state.pageRows.length > 0 && state.pageRows.every((r) => state.selected.has(r.pos));
    all.addEventListener("change", () => {
      state.pageRows.forEach((r) => (all.checked ? state.selected.add(r.pos) : state.selected.delete(r.pos)));
      renderData();
    });
    const th0 = el("th"); th0.appendChild(all); head.appendChild(th0);
    ["data.col_pos", "data.col_row", "data.col_value"].forEach((k) => head.appendChild(el("th", "", t(k))));
    if (ds.has_subgroup) head.appendChild(el("th", "", t("data.col_subgroup")));
    if (ds.has_timestamp) head.appendChild(el("th", "", t("data.col_time")));
    ds.tags.forEach((tag) => head.appendChild(el("th", "", tag)));
    head.appendChild(el("th", "", t("data.col_status")));
    head.appendChild(el("th", "", t("data.col_reason")));
    table.appendChild(head);
    state.pageRows.forEach((r) => {
      const tr = el("tr", (r.valid ? "" : "invalid ") + (state.suspects.has(r.pos) ? "suspect" : ""));
      const cb = el("input"); cb.type = "checkbox"; cb.checked = state.selected.has(r.pos);
      cb.addEventListener("change", () => { cb.checked ? state.selected.add(r.pos) : state.selected.delete(r.pos); renderData(); });
      const c0 = el("td"); c0.appendChild(cb); tr.appendChild(c0);
      tr.appendChild(el("td", "num", r.pos + 1));
      tr.appendChild(el("td", "num", r.source_row));
      tr.appendChild(el("td", "num", sig(r.value)));
      if (ds.has_subgroup) tr.appendChild(el("td", "", r.subgroup));
      if (ds.has_timestamp) tr.appendChild(el("td", "", r.timestamp));
      ds.tags.forEach((tag) => tr.appendChild(el("td", "", r.tags[tag])));
      tr.appendChild(el("td", "status", t(r.valid ? "data.status_valid" : "data.status_invalid")));
      const reason = el("td", "reason", r.invalid ? r.invalid.reason : "");
      if (r.invalid) reason.title = `${r.invalid.by}, ${r.invalid.at}`;
      tr.appendChild(reason);
      table.appendChild(tr);
    });
    const from = state.total ? state.offset + 1 : 0;
    $("#page-info").textContent = t("data.page", { from, to: state.offset + state.pageRows.length, total: state.total });
    $("#prev").disabled = state.offset === 0;
    $("#next").disabled = state.offset + state.pageSize >= state.total;
  }
  async function gotoPage(delta) {
    state.offset = Math.max(0, state.offset + delta * state.pageSize);
    await guarded(async () => { await loadRows(); renderData(); });
  }
  async function findSuspects() {
    await guarded(async () => {
      const r = await post(`/api/datasets/${state.dataset.id}/suspects`, { method: $("#suspect-method").value });
      state.suspects = new Set(r.suspects.map((s) => s.position));
      $("#suspect-msg").textContent = r.suspects.length ? t("data.suspects_found", { n: r.suspects.length }) : t("data.suspects_none");
      $("#suspect-select").hidden = r.suspects.length === 0;
      renderRows();
    });
  }
  async function markOrRestore(kind) {
    if (!state.selected.size) return showError({ code: "no_selection", params: {} });
    const body = { positions: Array.from(state.selected).sort((a, b) => a - b), reason: $("#reason").value, by: $("#person").value };
    store("spc.person", body.by);
    await guarded(async () => {
      const ds = await post(`/api/datasets/${state.dataset.id}/${kind === "mark" ? "invalid" : "restore"}`, body);
      const n = body.positions.length;
      state.dataset = ds;
      state.selected.clear(); state.suspects.clear(); $("#suspect-select").hidden = true;
      state.result = null; $("#result").hidden = true;
      await loadRows();
      renderData();
      $("#suspect-msg").textContent = t(kind === "mark" ? "data.marked" : "data.restored", { n });
    });
  }

  // ---------------------------------------------------------------- analysis
  function buildAnalysisBody() {
    const num = (sel) => { const v = $(sel).value.trim(); return v === "" ? null : Number(v); };
    const alpha = $("#a-alpha").value;
    const body = {
      stage: $("#a-stage").value,
      chart: $("#a-chart").value,
      subgroup_size: state.dataset.has_subgroup ? null : num("#a-size"),
      lsl: num("#a-lsl"), usl: num("#a-usl"),
      stability_mode: $("#a-mode").value,
      model: $("#a-model").value || null,
      controlled_stable: $("#a-controlled").checked,
      characteristic_class: $("#a-class").value || null,
      edition: $("#a-edition").value,
      incomplete: $("#a-incomplete").value,
      customer: $("#a-customer").value.trim() || null,
      rules: {
        beyond_limits: $("#r-beyond").checked,
        run_length: $("#r-run").checked ? Number($("#r-run-n").value) : null,
        trend_length: $("#r-trend").checked ? Number($("#r-trend-n").value) : null,
        middle_third: $("#r-middle").checked,
        two_of_three_beyond_2s: $("#r-2of3").checked,
        four_of_five_beyond_1s: $("#r-4of5").checked,
        fifteen_within_1s: $("#r-15").checked,
      },
    };
    if (alpha) body.alpha = Number(alpha);
    return body;
  }
  async function runAnalysis(ev) {
    ev.preventDefault();
    await guarded(async () => {
      state.result = await post(`/api/datasets/${state.dataset.id}/analyze`, buildAnalysisBody());
      renderResult();
    });
  }

  function drawChart(container, part, title) {
    container.replaceChildren();
    const W = 960, H = 280, ml = 70, mr = 150, mt = 16, mb = 34;
    const n = part.values.length;
    const ys = part.values.concat([part.lcl, part.ucl, part.center]).filter((v) => v !== null);
    let lo = Math.min(...ys), hi = Math.max(...ys);
    if (hi === lo) { hi += 1; lo -= 1; }
    const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
    const X = (i) => ml + (n === 1 ? (W - ml - mr) / 2 : (i * (W - ml - mr)) / (n - 1));
    const Y = (v) => mt + ((hi - v) * (H - mt - mb)) / (hi - lo);
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title });
    root.appendChild(svg("title", {}, title));
    root.appendChild(svg("line", { class: "axis", x1: ml, y1: mt, x2: ml, y2: H - mb }));
    root.appendChild(svg("line", { class: "axis", x1: ml, y1: H - mb, x2: W - mr, y2: H - mb }));
    // Same number of decimals on every tick, chosen from the size of the range.
    const decimals = Math.max(0, Math.min(8, Math.ceil(-Math.log10((hi - lo) / 4)) + 1));
    for (let i = 0; i <= 4; i++) {
      const v = lo + ((hi - lo) * i) / 4;
      root.appendChild(svg("line", { class: "axis", x1: ml - 4, y1: Y(v), x2: ml, y2: Y(v) }));
      root.appendChild(svg("text", { x: ml - 8, y: Y(v) + 4, "text-anchor": "end" }, v.toFixed(decimals)));
    }
    const step = Math.max(1, Math.ceil(n / 10));
    for (let i = 0; i < n; i += step) {
      root.appendChild(svg("text", { x: X(i), y: H - mb + 16, "text-anchor": "middle" }, String(part.labels[i] || i + 1).slice(0, 8)));
    }
    [["limit", part.ucl, t("result.ucl")], ["center", part.center, t("result.cl")], ["limit", part.lcl, t("result.lcl")]].forEach(([cls, v, name]) => {
      if (v === null) return;
      root.appendChild(svg("line", { class: cls, x1: ml, y1: Y(v), x2: W - mr, y2: Y(v) }));
      root.appendChild(svg("text", { class: "limit-label", x: W - mr + 6, y: Y(v) + 4 }, `${name} ${sig(v, 5)}`));
    });
    root.appendChild(svg("polyline", { class: "series", points: part.values.map((v, i) => `${X(i)},${Y(v)}`).join(" ") }));
    const alarmSet = new Set(part.alarms.map((a) => a.index));
    part.values.forEach((v, i) => {
      const c = svg("circle", { class: "dot" + (alarmSet.has(i) ? " alarm" : ""), cx: X(i), cy: Y(v), r: n > 300 ? 1.8 : 3.2 });
      c.appendChild(svg("title", {}, t("result.point", { label: part.labels[i] || i + 1, value: sig(v) })));
      root.appendChild(c);
    });
    container.appendChild(root);
  }
  function alarmSummary(ul, part) {
    ul.replaceChildren();
    part.alarms.slice(0, 15).forEach((a) => {
      ul.appendChild(el("li", "", t("result.alarm_rule", { rule: t("alarmrule." + a.rule), label: part.labels[a.index] || a.index + 1 })));
    });
    if (part.alarms.length > 15) ul.appendChild(el("li", "", `+${part.alarms.length - 15}`));
  }
  function namesReason(r) {
    if (r.stage === "machine") return t("result.names_reason_machine");
    if (r.stage === "preliminary") return t("result.names_reason_preliminary");
    const stable = ["statistical_control", "in_control"].includes(r.stability.class);
    return t(stable ? "result.names_reason_stable" : "result.names_reason_not_proven");
  }
  function tableRow(table, cells, header) {
    const tr = el("tr");
    cells.forEach((c, i) => tr.appendChild(el(header ? "th" : "td", !header && i > 0 ? "num" : "", c)));
    table.appendChild(tr);
  }
  function renderResult() {
    const r = state.result;
    if (!r) return;
    $("#result").hidden = false;
    renderWarnings($("#r-warnings"), r.warnings);
    $("#r-counts").textContent = t("result.counts", { total: r.counts.n_total, used: r.counts.n_used, n: r.counts.subgroup_size });
    $("#r-names").textContent = t("result.names_line", { p: r.names.p, pk: r.names.pk });
    $("#r-names-reason").textContent = namesReason(r);
    const st = r.stability;
    $("#r-stability").textContent = st.assessed ? t("result.class_" + st.class) : t("result.class_not_tested");
    $("#r-alarm-points").textContent = st.assessed
      ? t("result.alarm_points", { n: st.n_alarm_points, k: st.n_points_checked, expected: fmt(st.expected_false_alarms, 2), threshold: st.threshold })
      : "";

    const ch = r.chart;
    const locKey = ch.kind === "imr" ? "result.series_location_imr" : "result.series_location_xbar";
    const sum = (part) => (part.alarms.length ? t("result.alarms", { n: part.alarms.length }) : t("result.no_alarms"));
    const locTitle = `${t("result.chart_location")} – ${t(locKey)} (${t("result.kind_" + ch.kind)})`;
    const varTitle = `${t("result.chart_variation")} – ${t("result.series_variation_" + ch.kind)}`;
    $("#r-loc-title").textContent = `${locTitle} · ${sum(ch.location)}`;
    $("#r-var-title").textContent = `${varTitle} · ${sum(ch.variation)}`;
    drawChart($("#chart-loc"), ch.location, locTitle);
    drawChart($("#chart-var"), ch.variation, varTitle);
    alarmSummary($("#r-loc-alarms"), ch.location);
    alarmSummary($("#r-var-alarms"), ch.variation);

    const idxBlock = $("#r-indices-block");
    idxBlock.hidden = !r.indices;
    if (r.indices) {
      const ix = r.indices;
      const table = $("#r-indices");
      table.replaceChildren();
      tableRow(table, [t("result.index_name"), t("result.index_value"), t("result.index_ci", { level: Math.round(ix.ci_confidence * 1000) / 10 + " %" })], true);
      const ci = (c) => (c ? `${fmt(c[0])} – ${fmt(c[1])}` : "–");
      if (ix.p !== null) tableRow(table, [`${r.names.p} · ${t("result.index_p")}`, fmt(ix.p), ci(ix.ci_p)]);
      tableRow(table, [`${r.names.pk} · ${t("result.index_pk")}`, fmt(ix.pk), ci(ix.ci_pk)]);
      if (ix.pu !== null) tableRow(table, [t("result.index_pu"), fmt(ix.pu), ""]);
      if (ix.pl !== null) tableRow(table, [t("result.index_pl"), fmt(ix.pl), ""]);
      $("#r-stats").textContent = `${t("result.mean")} ${sig(ix.mean)} · ${t("result.sd")} ${sig(ix.sd)} · n = ${ix.n}`;
      $("#r-ppm").textContent = `${t("result.ppm")}: ${fmt(ix.ppm, 1)} ${t("result.ppm_unit")}`;
      $("#r-normality").textContent = r.normality
        ? `${t("result.normality")}: ${t("result.normality_line", { test: r.normality.test, p: fmt(r.normality.p_value, 4) })}`
        : "";
    }

    const tb = $("#r-targets-block");
    tb.hidden = !r.targets;
    if (r.targets) {
      const tg = r.targets, table = $("#r-targets");
      table.replaceChildren();
      if (tg.blocked) {
        $("#r-target-note").textContent = t("result.target_blocked", { base: tg.n_base });
      } else {
        tableRow(table, [t("result.index_name"), t("result.index_value"), t("result.index_ci", { level: Math.round(r.indices.ci_confidence * 1000) / 10 + " %" }), t("result.target_value"), t("result.verdict")], true);
        const verdict = (v) => t(v ? "result.verdict_" + v : "result.verdict_none");
        if (r.indices.p !== null) tableRow(table, [r.names.p, fmt(r.indices.p), fmt(r.indices.ci_p[0]), fmt(tg.p), verdict(tg.verdict_p)]);
        tableRow(table, [r.names.pk, fmt(r.indices.pk), fmt(r.indices.ci_pk[0]), fmt(tg.pk), verdict(tg.verdict_pk)]);
        $("#r-target-note").textContent = t("result.target_base", { base: tg.n_base, n: tg.n }) + (tg.adjusted ? " " + t("result.target_adjusted") : "");
      }
    }
    const db = $("#r-diagnosis-block");
    db.hidden = !r.diagnosis;
    if (r.diagnosis) {
      const d = r.diagnosis;
      $("#r-diagnosis").textContent = `${d.name_p ? d.name_p + " " + fmt(d.p) + " · " : ""}${d.name_pk} ${fmt(d.pk)}`;
    }
    const p = r.params;
    $("#r-params").textContent = t("result.params_edition", { edition: p.edition, alpha: fmt(p.alpha, 5), conf: p.estimate_confidence }) + (p.customer ? ` · ${p.customer}` : "");
    $("#r-fingerprint").textContent = `${t("result.params_fingerprint")}: ${p.fingerprint}`;
    $("#r-hash").textContent = r.source ? `${t("result.source_hash")}: ${r.source.sha256}` : "";
  }

  // ---------------------------------------------------------------- tools
  async function calcTargets() {
    await guarded(async () => {
      const body = { stage: $("#t-stage").value, characteristic_class: $("#t-class").value, n: Number($("#t-n").value), confidence: Number($("#t-conf").value) };
      state.toolsTargets = { body, out: await post("/api/targets", body) };
      renderTargets();
    });
  }
  function renderTargets() {
    const box = $("#t-out");
    if (!state.toolsTargets) { box.textContent = ""; return; }
    const { body, out } = state.toolsTargets;
    if (out.blocked) { box.textContent = t("result.target_blocked", { base: body.stage === "machine" ? 50 : 125 }); return; }
    box.textContent = `${t("tools.result_p")}: ${fmt(out.p)} · ${t("tools.result_pk")}: ${fmt(out.pk)}` + (out.adjusted ? ` · ${t("result.target_adjusted")}` : "");
  }
  async function calcArl() {
    await guarded(async () => {
      const body = { shift: Number($("#l-shift").value), n: Number($("#l-n").value) };
      const mx = $("#l-max").value.trim();
      if (mx) body.max_arl = Number(mx);
      state.toolsArl = { body, out: await post("/api/arl", body) };
      renderArl();
    });
  }
  function renderArl() {
    const box = $("#l-out");
    box.replaceChildren();
    if (!state.toolsArl) return;
    const { body, out } = state.toolsArl;
    box.appendChild(el("p", "strong", `${t("tools.arl_alarm")}: ${fmt(out.alarm_probability * 100, 1)} %`));
    box.appendChild(el("p", "strong", `${t("tools.arl_arl")}: ${fmt(out.arl, 1)}`));
    if (body.max_arl !== undefined) {
      box.appendChild(el("p", "strong", out.required_n ? `${t("tools.arl_required")}: ${out.required_n}` : t("tools.arl_none")));
    }
    box.appendChild(el("h4", "", t("tools.arl_curve")));
    const wrap = el("div", "scroll"), table = el("table", "grid");
    tableRow(table, [t("tools.arl_col_shift"), t("tools.arl_col_arl")], true);
    out.curve.forEach((c) => tableRow(table, [fmt(c.shift, 2), fmt(c.arl, 1)]));
    wrap.appendChild(table); box.appendChild(wrap);
  }

  // ---------------------------------------------------------------- wiring
  function rerender() {
    applyStatic();
    if (state.preview) { const keep = {}; ROLE_SELECTS.forEach((s) => { keep[s] = $(s).value; }); fillSelects(keep); renderDetected(); refreshImportForm(); }
    if (state.dataset) renderData();
    if (state.result) renderResult();
    renderTargets(); renderArl();
  }
  async function setLanguage(lang) {
    await loadMessages(lang);
    store("spc.lang", lang);
    $("#lang").value = lang;
    rerender();
  }
  function wire() {
    $("#lang").addEventListener("change", (e) => setLanguage(e.target.value));
    $$("nav.tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
    $("#file").addEventListener("change", (e) => onFileChosen(e.target.files[0]));
    ROLE_SELECTS.forEach((s) => $(s).addEventListener("change", refreshImportForm));
    $("#decimal").addEventListener("change", () => { if (state.preview) { const g = guessColumns(); if (!$("#col-value").value && g.value) { $("#col-value").value = g.value; refreshImportForm(); } } });
    $("#import-btn").addEventListener("click", doImport);
    $("#prev").addEventListener("click", () => gotoPage(-1));
    $("#next").addEventListener("click", () => gotoPage(1));
    $("#suspect-btn").addEventListener("click", findSuspects);
    $("#suspect-select").addEventListener("click", () => { state.suspects.forEach((p) => state.selected.add(p)); renderData(); });
    $("#mark-btn").addEventListener("click", () => markOrRestore("mark"));
    $("#restore-btn").addEventListener("click", () => markOrRestore("restore"));
    $("#to-analysis").addEventListener("click", () => showTab("analysis"));
    $("#analysis-form").addEventListener("submit", runAnalysis);
    $("#t-btn").addEventListener("click", calcTargets);
    $("#l-btn").addEventListener("click", calcArl);
    $("#person").value = store("spc.person") || "";
  }
  async function start() {
    wire();
    try { await setLanguage(pickLanguage()); }
    catch (e) { showError({ code: "network", message: String(e && e.message), params: {} }); }
  }
  start();
})();

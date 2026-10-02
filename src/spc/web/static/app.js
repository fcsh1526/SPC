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
    lastAnalysisBody: null, reportOut: null, reportLangTouched: false, archiveOut: null,
    user: null, csrf: "", mustChange: false,
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
    if (e.code === "login_locked") p.minutes = Math.max(1, Math.ceil((p.retry_after || 0) / 60));
    if (e.code === "forbidden" && p.role) p.role = t("role." + p.role);
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
    catch (e) {
      if (e && e.code === "not_authenticated" && state.user) { showLogin("login.session_ended"); return; }
      if (e && e.code === "password_change_required") { state.mustChange = true; showTab("password"); }
      showError(e && e.code ? e : { code: "network", message: String(e), params: {} });
    }
    finally {
      $("#busy").hidden = true;
      buttons.forEach((b) => { b.disabled = b.dataset.wasDisabled === "1"; });
    }
  }
  async function api(path, opts) {
    let resp;
    opts = Object.assign({}, opts);
    if ((opts.method || "GET") !== "GET" && state.csrf) opts.headers = Object.assign({ "X-CSRF-Token": state.csrf }, opts.headers);
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
    if (state.mustChange) name = "password";  // nothing else works until the password is changed
    $$("nav.tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    $$("main > section").forEach((s) => { s.hidden = s.id !== "tab-" + name; });
    if (name === "saved") loadSaved();
    if (name === "admin") loadAdmin();
    if (name === "password") renderPasswordPanel();
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
      $("#file-name").textContent = t("import.done", { n: ds.summary.n_total, name: state.file.name });
      await openDataset(ds);
    });
  }
  async function openDataset(ds) {
    state.dataset = ds;
    state.offset = 0; state.selected.clear(); state.suspects.clear(); state.result = null; state.reportOut = null;
    renderReportOut();
    $("#result").hidden = true;
    $("#a-size-wrap").hidden = ds.has_subgroup;
    $("#a-moving-wrap").hidden = ds.has_subgroup;  // a moving sample belongs to the individuals chart
    $("#export-link").href = `/api/datasets/${ds.id}/export.csv`;
    setExcelLink();
    unlockTabs();
    await loadRows();
    renderData();
    showTab("data");
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
    if (s.n_restarts) line += " · " + t("data.restarts_count", { n: s.n_restarts });
    $("#restart-list").textContent = ds.restarts.length ? t("data.restart_list", { rows: ds.restarts.map((r) => (r.new_limits ? t("data.restart_item_phase", { n: r.pos + 1 }) : String(r.pos + 1))).join(", ") }) : t("data.restart_none");
    $("#data-counts").textContent = line;
    const src = $("#data-source");
    if (ds.source) {
      src.textContent = t("data.source", { name: ds.source.name, encoding: ds.source.encoding, hash: ds.source.sha256.slice(0, 12) + "…" });
      src.title = ds.source.sha256;
    } else { src.textContent = ""; }
    renderWarnings($("#data-warnings"), ds.warnings);
    $("#person-note").textContent = state.user ? t("data.person_note", { user: userLabel() }) : "";
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
      const action = t({ mark_invalid: "data.log_mark_invalid", restore: "data.log_restore", restart: "data.log_restart", restart_phase: "data.log_restart_phase", unrestart: "data.log_unrestart" }[e.action]);
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
      tr.appendChild(el("td", "status", t(r.valid ? "data.status_valid" : "data.status_invalid") + (r.restart ? " · ↻ " + t(r.restart.new_limits ? "data.status_phase" : "data.status_restart") : "")));
      const note = r.invalid || r.restart;
      const reason = el("td", "reason", note ? note.reason : "");
      if (note) reason.title = `${note.by}, ${note.at}`;
      if (r.restart) tr.classList.add("restart-row");
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
    const body = { positions: Array.from(state.selected).sort((a, b) => a - b), reason: $("#reason").value };
    await guarded(async () => {
      const ds = await post(`/api/datasets/${state.dataset.id}/${kind === "mark" ? "invalid" : "restore"}`, body);
      const n = body.positions.length;
      state.dataset = ds;
      state.selected.clear(); state.suspects.clear(); $("#suspect-select").hidden = true;
      state.result = null; state.reportOut = null; renderReportOut(); $("#result").hidden = true;
      await loadRows();
      renderData();
      $("#suspect-msg").textContent = t(kind === "mark" ? "data.marked" : "data.restored", { n });
    });
  }

  async function restartOrRemove(kind) {
    if (!state.selected.size) return showError({ code: "no_selection", params: {} });
    const body = { positions: Array.from(state.selected).sort((a, b) => a - b), reason: $("#reason").value };
    if (kind === "add") body.new_limits = $("#restart-phase").checked;
    await guarded(async () => {
      const ds = await post(`/api/datasets/${state.dataset.id}/restarts${kind === "add" ? "" : "/remove"}`, body);
      state.dataset = ds;
      state.selected.clear(); state.suspects.clear(); $("#suspect-select").hidden = true;
      state.result = null; state.reportOut = null; renderReportOut(); $("#result").hidden = true;
      await loadRows();
      renderData();
      $("#suspect-msg").textContent = t(kind === "add" ? "data.restarted" : "data.restart_removed", { n: body.positions.length });
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
    if (!$("#a-moving-wrap").hidden) body.moving_n = Math.max(1, Math.min(10, Math.round(Number($("#a-moving").value) || 1)));
    body.distribution = $("#a-dist").value;
    if (body.distribution !== "normal") {
      body.method = $("#a-method").value;
      body.bootstrap_n = Math.max(0, Math.min(2000, Math.round(Number($("#a-boot").value) || 0)));
      body.seed = Math.max(0, Math.round(Number($("#a-seed").value) || 0));
    }
    return body;
  }
  async function runAnalysis(ev) {
    ev.preventDefault();
    await guarded(async () => {
      const body = buildAnalysisBody();
      state.result = await post(`/api/datasets/${state.dataset.id}/analyze`, body);
      state.lastAnalysisBody = body;
      state.reportOut = null;
      renderReportOut();
      renderResult();
    });
  }

  function drawChart(container, part, title) {
    container.replaceChildren();
    const W = 960, H = 280, ml = 70, mr = 150, mt = 16, mb = 34;
    const n = part.values.length;
    const flat = (v) => (Array.isArray(v) ? v : [v]);  // limits are one number, or one per point after a restart
    const ys = part.values.concat(flat(part.lcl), flat(part.ucl), flat(part.center)).filter((v) => v !== null);
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
    const half = n > 1 ? (W - ml - mr) / (n - 1) / 2 : 0;
    const phaseSet = new Set(part.phases || []);
    (part.restarts || []).forEach((i) => {  // a restart sits between two points; a new phase also has its own limits
      root.appendChild(svg("line", { class: "restart" + (phaseSet.has(i) ? " phase" : ""), x1: X(i) - half, y1: mt, x2: X(i) - half, y2: H - mb }));
    });
    [["limit", part.ucl, t("result.ucl")], ["center", part.center, t("result.cl")], ["limit", part.lcl, t("result.lcl")]].forEach(([cls, v, name]) => {
      if (v === null) return;
      if (Array.isArray(v)) {  // limits that follow the size of the moving sample: a staircase
        const pts = [];
        v.forEach((val, i) => { pts.push(`${Math.max(ml, X(i) - half)},${Y(val)}`, `${Math.min(W - mr, X(i) + half)},${Y(val)}`); });
        root.appendChild(svg("polyline", { class: cls, points: pts.join(" "), fill: "none" }));
        root.appendChild(svg("text", { class: "limit-label", x: W - mr + 6, y: Y(v[v.length - 1]) + 4 }, `${name} ${sig(v[v.length - 1], 5)}`));
        return;
      }
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
    const mv = $("#r-moving");
    mv.hidden = !ch.moving_n;
    if (ch.moving_n) mv.textContent = t("result.moving_line", { n: ch.moving_n, k: ch.location.restarts.length });
    const ph = $("#r-phases");
    ph.replaceChildren();
    (ch.phase_stats || []).forEach((p, i) => ph.appendChild(el("li", "", t("result.phase_line", {
      k: i + 1, from: ch.location.labels[p.start], n: p.n_values, mu: sig(p.mu_hat, 5), sigma: sig(p.sigma_hat, 4) }))));
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
      if (ix.p !== null) tableRow(table, [`${indexName(r, "p")} · ${t("result.index_p")}`, fmt(ix.p), ci(ix.ci_p)]);
      tableRow(table, [`${indexName(r, "pk")} · ${t("result.index_pk")}`, fmt(ix.pk), ci(ix.ci_pk)]);
      if (ix.pu !== null) tableRow(table, [t("result.index_pu"), fmt(ix.pu), ""]);
      if (ix.pl !== null) tableRow(table, [t("result.index_pl"), fmt(ix.pl), ""]);
      $("#r-stats").textContent = `${t("result.mean")} ${sig(ix.mean)} · ${t("result.sd")} ${sig(ix.sd)} · n = ${ix.n}`;
      $("#r-ppm").textContent = `${t("result.ppm")}: ${fmt(ix.ppm, 1)} ${t("result.ppm_unit")}`;
      $("#r-normality").textContent = r.normality
        ? `${t("result.normality")}: ${t("result.normality_line", { test: r.normality.test, p: fmt(r.normality.p_value, 4) })}`
        : "";
    }
    renderDistribution(r);

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
        const lower = (c) => (c ? fmt(c[0]) : "–");
        if (r.indices.p !== null) tableRow(table, [indexName(r, "p"), fmt(r.indices.p), lower(r.indices.ci_p), fmt(tg.p), verdict(tg.verdict_p)]);
        tableRow(table, [indexName(r, "pk"), fmt(r.indices.pk), lower(r.indices.ci_pk), fmt(tg.pk), verdict(tg.verdict_pk)]);
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


  // index name with the method suffix when the distribution is not normal: Ppk.G, Cp.Z
  function indexName(r, key) {
    const d = r.distribution;
    return r.names[key] + (d && d.name !== "normal" ? "." + d.method : "");
  }
  function renderDistribution(r) {
    const d = r.distribution, block = $("#r-dist-block");
    block.hidden = !d;
    if (!d) return;
    const how = t(d.requested === "auto" ? "result.dist_how_auto" : "result.dist_how_manual");
    $("#r-dist-line").textContent = t("result.dist_line", { name: t("dist." + d.name), how, method: d.method });
    const fmtParam = (v) => (Array.isArray(v) ? "[" + v.map((x) => sig(x, 5)).join(", ") + "]" : sig(v, 5));
    $("#r-dist-params").textContent = t("result.dist_params", { params: Object.entries(d.params).map(([k, v]) => `${k} = ${fmtParam(v)}`).join(" · ") });
    $("#r-dist-boot").textContent = d.bootstrap
      ? t(r.indices.ci_pk ? "result.dist_boot" : "result.dist_boot_failed", { used: d.bootstrap.succeeded, requested: d.bootstrap.requested, seed: d.bootstrap.seed })
      : t("result.dist_no_boot");
    const table = $("#r-dist-cands"); table.replaceChildren();
    tableRow(table, [t("result.dist_cand_family"), t("result.dist_cand_aic"), t("result.dist_cand_delta"), t("result.dist_cand_ad"), ""], true);
    d.candidates.forEach((c) => {
      const row = c.ok ? [t("dist." + c.family), fmt(c.aic, 1), fmt(c.delta_aic, 1), fmt(c.ad, 2), c.family === d.name ? "◀" : ""]
        : [t("dist." + c.family), "–", "–", "–", t("result.dist_failed")];
      tableRow(table, row);
    });
    $("#r-dist-sample").textContent = r.counts.n_used > d.ranked_on ? t("result.dist_ranked_sample", { n: d.ranked_on }) : "";
  }

  // ---------------------------------------------------------------- report and archive
  const REPORT_TEXT_FIELDS = ["process", "machine", "site", "process_ref", "machine_ref", "persons", "period_text",
    "part_name", "part_number", "characteristic", "unit", "technical_conditions", "deviations", "sampling_frequency", "recommendations"];
  const REMEMBERED_FIELDS = ["process", "machine", "site", "process_ref", "machine_ref", "persons", "unit"];

  function readReportMeta() {
    const meta = {};
    REPORT_TEXT_FIELDS.forEach((f) => { meta[f] = $("#rp-" + f).value; });
    const num = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
    meta.target = num("#rp-target");
    meta.uncertainty = num("#rp-uncertainty");
    const k = num("#rp-coverage_factor");
    meta.coverage_factor = k === null ? 2 : k;
    return meta;
  }
  function restoreReportMeta() {
    try {
      const saved = JSON.parse(store("spc.reportMeta") || "{}");
      REMEMBERED_FIELDS.forEach((f) => { if (typeof saved[f] === "string") $("#rp-" + f).value = saved[f]; });
    } catch (e) { /* ignore a broken saved value */ }
  }
  async function createReport() {
    if (!state.result || !state.lastAnalysisBody) {
      return showError({ code: "needs_run", message: t("report.needs_run"), params: {} });
    }
    const meta = readReportMeta();
    const remembered = {};
    REMEMBERED_FIELDS.forEach((f) => { remembered[f] = meta[f]; });
    store("spc.reportMeta", JSON.stringify(remembered));
    await guarded(async () => {
      const out = await post(`/api/datasets/${state.dataset.id}/reports`, {
        analysis: state.lastAnalysisBody, meta, language: $("#rp-language").value,
      });
      state.reportOut = out;
      renderReportOut();
    });
  }
  function renderReportOut() {
    const box = $("#rp-out");
    const out = state.reportOut;
    box.hidden = !out;
    if (!out) return;
    $("#rp-created").textContent = t("report.created", { id: out.id });
    $("#rp-open").href = out.urls.html;
    $("#rp-download").href = out.urls.download;
    $("#rp-archive").href = out.urls.archive;
    $("#rp-excel").href = `${out.urls.html}/report.xlsx`;
    $("#rp-digest").textContent = `${t("report.digest")}: ${out.digest}`;
  }
  async function checkArchive() {
    const file = $("#archive-file").files[0];
    if (!file) return showError({ code: "no_file", params: {} });
    await guarded(async () => {
      state.archiveOut = await api("/api/archive/check", { method: "POST", body: file });
      renderArchiveOut();
    });
  }
  function renderArchiveOut() {
    const box = $("#archive-out");
    box.replaceChildren();
    const r = state.archiveOut;
    if (!r) return;
    box.appendChild(el("p", r.integrity_ok ? "ok strong" : "bad strong", t(r.integrity_ok ? "archive.integrity_ok" : "archive.integrity_bad")));
    box.appendChild(el("p", r.reproduced ? "ok strong" : "bad strong", t(r.reproduced ? "archive.reproduced_yes" : "archive.reproduced_no")));
    if (!r.reproduced) {
      const ul = el("ul");
      r.differences.forEach((d) => ul.appendChild(el("li", "", d)));
      box.appendChild(ul);
    }
    box.appendChild(el("p", "muted", t(r.same_engine_version ? "archive.same_engine" : "archive.other_engine")));
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

  // ---------------------------------------------------------------- login, password, roles
  const userLabel = () => (state.user.display_name ? `${state.user.display_name} (${state.user.username})` : state.user.username);
  const when = (s) => String(s || "").replace("T", " ").replace("Z", "");

  function showLogin(messageKey) {
    state.user = null; state.csrf = "";
    document.body.removeAttribute("data-role");
    $("#app").hidden = true; $("#userbox").hidden = true; $("#login").hidden = false;
    $("#login-setup").hidden = !state.setupNeeded;
    const msg = $("#login-msg");
    msg.hidden = !messageKey;
    if (messageKey) { msg.dataset.i18n = messageKey; msg.textContent = t(messageKey); } else { delete msg.dataset.i18n; }
    $("#login-pass").value = "";
  }
  function enterApp(me) {
    state.user = me.user; state.csrf = me.csrf; state.mustChange = me.user.must_change;
    document.body.dataset.role = me.user.role;
    $("#login").hidden = true; $("#app").hidden = false; $("#userbox").hidden = false;
    $("#login-user").value = ""; $("#login-pass").value = "";
    renderUserBox();
    showTab(state.mustChange ? "password" : (me.user.role === "viewer" ? "saved" : "import"));
  }
  function renderUserBox() {
    if (!state.user) return;
    $("#user-label").textContent = `${userLabel()} · ${t("role." + state.user.role)}`;
  }
  async function doLogin() {
    await guarded(async () => {
      const me = await post("/api/auth/login", { username: $("#login-user").value, password: $("#login-pass").value });
      enterApp(me);
    });
  }
  async function doLogout() {
    await guarded(async () => { await post("/api/auth/logout", {}); });
    location.reload();  // drops everything that is still in the page
  }
  function renderPasswordPanel() {
    $("#password-forced").hidden = !state.mustChange;
    $("#password-rules").textContent = t("password.rules", { min: 10 });
    $("#pw-done").hidden = true;
  }
  async function doChangePassword() {
    const fresh = $("#pw-new").value;
    if (fresh !== $("#pw-repeat").value) return showError({ code: "password_mismatch", params: {} });
    await guarded(async () => {
      const r = await post("/api/auth/password", { current: $("#pw-current").value, new: fresh });
      state.user = r.user; state.mustChange = false;
      $("#password-form").reset();
      $("#pw-done").hidden = false;
    });
  }

  // ---------------------------------------------------------------- saved data and reports
  function cell(tr, text, tag = "td") { const c = el(tag, "", text); tr.appendChild(c); return c; }
  function linkButton(label, href, newTab) {
    const a = el("a", "button", label);
    a.href = href;
    if (newTab) { a.target = "_blank"; a.rel = "noopener"; }
    return a;
  }
  async function loadSaved() {
    await guarded(async () => {
      const [ds, rp] = await Promise.all([api("/api/datasets"), api("/api/reports")]);
      const dt = $("#saved-datasets"); dt.replaceChildren();
      const head = el("tr");
      ["saved.col_name", "saved.col_owner", "saved.col_updated", "saved.col_values", "saved.col_marked", ""].forEach((k) => cell(head, k ? t(k) : "", "th"));
      dt.appendChild(head);
      if (!ds.datasets.length) { const tr = el("tr"); const c = cell(tr, t("saved.empty")); c.colSpan = 6; dt.appendChild(tr); }
      ds.datasets.forEach((d) => {
        const tr = el("tr");
        cell(tr, d.name); cell(tr, d.owner); cell(tr, when(d.updated_at)); cell(tr, String(d.n_total)); cell(tr, String(d.n_invalid));
        const actions = cell(tr, "");
        const open = el("button", "", t("saved.open"));
        open.addEventListener("click", () => guarded(async () => { await openDataset(await api(`/api/datasets/${d.id}`)); }));
        actions.appendChild(open);
        if (state.user.role !== "viewer" && (d.owner.toLowerCase() === state.user.username.toLowerCase() || state.user.role === "admin")) {
          const del = el("button", "", t("saved.delete"));
          del.addEventListener("click", async () => {
            if (!window.confirm(t("saved.confirm_delete", { name: d.name }))) return;
            await guarded(async () => {
              await api(`/api/datasets/${d.id}`, { method: "DELETE" });
              if (state.dataset && state.dataset.id === d.id) { state.dataset = null; state.result = null; }
            });
            loadSaved();
          });
          actions.appendChild(del);
        }
        dt.appendChild(tr);
      });
      const rt = $("#saved-reports"); rt.replaceChildren();
      const rh = el("tr");
      ["saved.col_id", "saved.col_created", "saved.col_language", "saved.col_owner", ""].forEach((k) => cell(rh, k ? t(k) : "", "th"));
      rt.appendChild(rh);
      if (!rp.reports.length) { const tr = el("tr"); const c = cell(tr, t("saved.empty")); c.colSpan = 5; rt.appendChild(tr); }
      rp.reports.forEach((r) => {
        const tr = el("tr");
        cell(tr, r.id); cell(tr, when(r.created_at)); cell(tr, r.language); cell(tr, r.owner);
        const actions = cell(tr, "");
        const base = `/api/reports/${r.id}`;
        actions.appendChild(linkButton(t("saved.report_open"), base, true));
        actions.appendChild(linkButton(t("saved.report_download"), `${base}?download=1`));
        actions.appendChild(linkButton(t("saved.report_archive"), `${base}/archive.json`));
        actions.appendChild(linkButton(t("saved.report_excel"), `${base}/report.xlsx`));
        rt.appendChild(tr);
      });
    });
  }

  // ---------------------------------------------------------------- administration
  async function loadAdmin() {
    await guarded(async () => {
      const [users, audit] = await Promise.all([api("/api/users"), api("/api/audit?limit=100")]);
      renderUsers(users.users);
      renderAudit(audit.entries);
    });
  }
  function renderUsers(users) {
    const table = $("#admin-users"); table.replaceChildren();
    const head = el("tr");
    ["admin.col_user", "admin.col_name", "admin.col_role", "admin.col_active", "admin.col_actions"].forEach((k) => cell(head, t(k), "th"));
    table.appendChild(head);
    users.forEach((u) => {
      const tr = el("tr");
      cell(tr, u.username); cell(tr, u.display_name);
      const roleCell = cell(tr, ""), role = el("select");
      ["viewer", "engineer", "admin"].forEach((r) => { const o = el("option", "", t("role." + r)); o.value = r; role.appendChild(o); });
      role.value = u.role;
      role.addEventListener("change", () => updateUser(u.id, { role: role.value }));
      roleCell.appendChild(role);
      const activeCell = cell(tr, ""), active = el("input");
      active.type = "checkbox"; active.checked = u.active;
      active.addEventListener("change", () => updateUser(u.id, { active: active.checked }));
      activeCell.appendChild(active);
      const actions = cell(tr, "");
      const reset = el("button", "", t("admin.reset"));
      reset.addEventListener("click", async () => {
        const pw = window.prompt(t("admin.reset_prompt", { name: u.username }));
        if (!pw) return;
        await guarded(async () => { await post(`/api/users/${u.id}/password`, { password: pw }); $("#admin-msg").textContent = t("admin.reset_done", { name: u.username }); });
      });
      const unlock = el("button", "", t("admin.unlock"));
      unlock.addEventListener("click", () => guarded(async () => { await post(`/api/users/${u.id}/unlock`, {}); $("#admin-msg").textContent = t("admin.unlocked", { name: u.username }); }));
      actions.appendChild(reset); actions.appendChild(unlock);
      table.appendChild(tr);
    });
  }
  async function updateUser(id, patch) {
    await guarded(async () => { await api(`/api/users/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }); });
    loadAdmin();  // shows the real state again, also after a refused change
  }
  async function createUser() {
    const body = { username: $("#nu-name").value.trim(), display_name: $("#nu-display").value.trim(), role: $("#nu-role").value, password: $("#nu-pass").value, must_change: true };
    await guarded(async () => {
      const r = await post("/api/users", body);
      $("#admin-msg").textContent = t("admin.created", { name: r.user.username });
      $("#nu-name").value = ""; $("#nu-display").value = ""; $("#nu-pass").value = "";
      renderUsers((await api("/api/users")).users);
      renderAudit((await api("/api/audit?limit=100")).entries);
    });
  }
  function renderAudit(entries) {
    const table = $("#audit-log"); table.replaceChildren();
    const head = el("tr");
    ["admin.audit_col_time", "admin.audit_col_user", "admin.audit_col_action", "admin.audit_col_target", "admin.audit_col_detail"].forEach((k) => cell(head, t(k), "th"));
    table.appendChild(head);
    entries.forEach((e) => {
      const tr = el("tr");
      cell(tr, when(e.ts)); cell(tr, e.username); cell(tr, e.action); cell(tr, e.target);
      cell(tr, Object.keys(e.detail).length ? JSON.stringify(e.detail) : "");
      table.appendChild(tr);
    });
  }
  async function verifyAudit() {
    await guarded(async () => {
      const r = await api("/api/audit/verify");
      $("#audit-verdict").textContent = r.ok ? t("admin.audit_ok", { n: r.entries, hash: r.last_hash }) : t("admin.audit_broken", { id: r.broken_at });
    });
  }

  // ---------------------------------------------------------------- wiring
  function syncDistributionControls() {
    const d = $("#a-dist").value, nonNormal = d !== "normal";
    ["#a-method-wrap", "#a-boot-wrap", "#a-seed-wrap"].forEach((s) => { $(s).hidden = !nonNormal; });
    if (d === "empirical") $("#a-method").value = "G";
    $("#a-method option[value=Z]").disabled = d === "empirical";
  }
  function setExcelLink() {  // the sheets of the data workbook follow the language of the page
    if (state.dataset) $("#export-xlsx-link").href = `/api/datasets/${state.dataset.id}/export.xlsx?lang=${state.lang}`;
  }
  function rerender() {
    setExcelLink();
    applyStatic();
    if (state.preview) { const keep = {}; ROLE_SELECTS.forEach((s) => { keep[s] = $(s).value; }); fillSelects(keep); renderDetected(); refreshImportForm(); }
    if (state.dataset) renderData();
    if (state.result) renderResult();
    renderTargets(); renderArl(); renderReportOut(); renderArchiveOut(); renderUserBox();
    if (state.user && !$("#tab-saved").hidden) loadSaved();
    if (state.user && !$("#tab-admin").hidden) loadAdmin();
  }
  async function setLanguage(lang) {
    await loadMessages(lang);
    store("spc.lang", lang);
    $("#lang").value = lang;
    if (!state.reportLangTouched) $("#rp-language").value = lang;
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
    $("#restart-btn").addEventListener("click", () => restartOrRemove("add"));
    $("#unrestart-btn").addEventListener("click", () => restartOrRemove("remove"));
    $("#to-analysis").addEventListener("click", () => showTab("analysis"));
    $("#a-dist").addEventListener("change", syncDistributionControls);
    syncDistributionControls();
    $("#analysis-form").addEventListener("submit", runAnalysis);
    $("#t-btn").addEventListener("click", calcTargets);
    $("#l-btn").addEventListener("click", calcArl);
    $("#login-form").addEventListener("submit", (e) => { e.preventDefault(); doLogin(); });
    $("#logout-btn").addEventListener("click", doLogout);
    $("#pw-btn").addEventListener("click", () => showTab("password"));
    $("#password-form").addEventListener("submit", (e) => { e.preventDefault(); doChangePassword(); });
    $("#nu-create").addEventListener("click", createUser);
    $("#audit-verify").addEventListener("click", verifyAudit);
    restoreReportMeta();
    $("#rp-language").addEventListener("change", () => { state.reportLangTouched = true; });
    $("#rp-create").addEventListener("click", createReport);
    $("#archive-file").addEventListener("change", (e) => { const f = e.target.files[0]; $("#archive-name").textContent = f ? f.name : ""; state.archiveOut = null; renderArchiveOut(); });
    $("#archive-btn").addEventListener("click", checkArchive);
  }
  async function start() {
    wire();
    try {
      await setLanguage(pickLanguage());
      const meta = await api("/api/meta");
      state.setupNeeded = meta.setup_needed;
      if (meta.session) enterApp(meta.session); else showLogin();
    } catch (e) { showError({ code: "network", message: String(e && e.message || e.code), params: {} }); }
  }
  start();
})();

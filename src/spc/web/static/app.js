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
    profiles: [], profile: null, defaultTargets: null, editing: null, logo: "",
    mon: { list: [], id: null, view: null, last: null, editing: null, timer: null },
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
    if (Array.isArray(p.fields) && e.code.startsWith("report_field")) p.fields = p.fields.map(fieldLabel).join(", ");
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
    if (name === "monitor") loadMonitors();
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
    if (state.profile) {  // the profile fixes what the customer agreed: those fields are not sent, the server applies them
      const fixed = state.profile.analysis;
      ["alpha", "stability_mode", "edition", "rules"].forEach((k) => { if (k in fixed) delete body[k]; });
      delete body.customer;
      body.profile_id = state.profile.id;
    }
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
    const ys = part.values.concat(flat(part.lcl), flat(part.ucl), flat(part.center), flat(part.wlcl ?? null), flat(part.wucl ?? null)).filter((v) => v !== null);
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
    [["warning", part.wucl, t("mon.warning_limit")], ["warning", part.wlcl, t("mon.warning_limit")]].forEach(([cls, v]) => {
      if (v === null || v === undefined) return;
      root.appendChild(svg("line", { class: cls, x1: ml, y1: Y(v), x2: W - mr, y2: Y(v) }));
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
    const pr = r.profile;
    $("#r-profile").textContent = pr
      ? t("result.profile_line", { name: pr.name, rev: pr.revision }) + (pr.deviations.length ? " · " + t("result.profile_deviations", { keys: pr.deviations.map((k) => t("pkey." + k)).join(", ") }) : "")
      : "";
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
    meta.extra = {};
    if (state.profile) state.profile.report.extra_fields.forEach((f) => { meta.extra[f.key] = $("#rp-x-" + f.key).value; });
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
    if (!state.mustChange) { loadProfiles(); startAlertPolling(); }
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

  // ---------------------------------------------------------------- customer profiles
  const RULE_IDS = (p) => ({ beyond: `#${p}beyond`, run: `#${p}run`, runN: `#${p}run-n`, trend: `#${p}trend`, trendN: `#${p}trend-n`,
    middle: `#${p}middle`, two: `#${p}2of3`, four: `#${p}4of5`, fifteen: `#${p}15` });
  function readRules(prefix) {
    const i = RULE_IDS(prefix), on = (k) => $(i[k]).checked;
    return { beyond_limits: on("beyond"), run_length: on("run") ? Number($(i.runN).value) : null,
      trend_length: on("trend") ? Number($(i.trendN).value) : null, middle_third: on("middle"),
      two_of_three_beyond_2s: on("two"), four_of_five_beyond_1s: on("four"), fifteen_within_1s: on("fifteen") };
  }
  function fillRules(prefix, rules, lock) {
    const i = RULE_IDS(prefix), r = rules || {};
    $(i.beyond).checked = r.beyond_limits !== false;
    $(i.run).checked = !!r.run_length; if (r.run_length) $(i.runN).value = r.run_length;
    $(i.trend).checked = !!r.trend_length; if (r.trend_length) $(i.trendN).value = r.trend_length;
    $(i.middle).checked = !!r.middle_third; $(i.two).checked = !!r.two_of_three_beyond_2s;
    $(i.four).checked = !!r.four_of_five_beyond_1s; $(i.fifteen).checked = !!r.fifteen_within_1s;
    if (lock !== undefined) Object.values(i).forEach((sel) => { $(sel).disabled = lock; });
  }
  async function loadProfiles() {
    try { state.profiles = (await api("/api/profiles")).profiles; } catch (e) { state.profiles = []; }
    const select = $("#a-profile"), keep = state.profile ? String(state.profile.id) : "";
    $$("option", select).slice(1).forEach((o) => o.remove());
    state.profiles.forEach((p) => { const o = el("option", "", p.name); o.value = String(p.id); select.appendChild(o); });
    select.value = state.profiles.some((p) => String(p.id) === keep) ? keep : "";
    onProfileChange();
  }
  function onProfileChange() {
    const id = $("#a-profile").value;
    state.profile = id ? state.profiles.find((p) => String(p.id) === id) || null : null;
    const a = state.profile ? state.profile.analysis : {};
    const lock = (sel, key) => { $(sel).disabled = !!state.profile && key in a; };
    lock("#a-alpha", "alpha"); lock("#a-mode", "stability_mode"); lock("#a-edition", "edition");
    if (state.profile && "stability_mode" in a) $("#a-mode").value = a.stability_mode;
    if (state.profile && "edition" in a) $("#a-edition").value = a.edition;
    if (state.profile && "alpha" in a) { const o = $$("#a-alpha option").find((x) => Number(x.value) === a.alpha); if (o) $("#a-alpha").value = o.value; }
    fillRules("r-", state.profile && "rules" in a ? a.rules : readRules("r-"), !!state.profile && "rules" in a);
    $("#a-customer").value = state.profile ? state.profile.name : $("#a-customer").value;
    $("#a-customer").disabled = !!state.profile;
    const line = $("#a-profile-line");
    line.hidden = !state.profile;
    if (state.profile) {
      const keys = Object.keys(a).map((k) => t("pkey." + k)).join(", ") || "–";
      line.textContent = t("analysis.profile_line", { name: state.profile.name, rev: state.profile.revision, keys });
    }
    renderReportProfile();
  }
  function fieldLabel(name) {
    if (name.startsWith("extra:") && state.profile) {
      const f = state.profile.report.extra_fields.find((x) => x.key === name.slice(6));
      return f ? (state.lang === "zh-TW" ? f.label_zh : f.label_en) || f.label_en || f.label_zh || f.key : name;
    }
    const input = $("#rp-" + name), label = input && input.closest("label");
    return label ? label.querySelector("span").textContent : name;
  }
  function renderReportProfile() {
    const report = state.profile ? state.profile.report : null;
    $$("label.required").forEach((l) => l.classList.remove("required"));
    (report ? report.required : []).forEach((n) => { const i = $("#rp-" + n); if (i) i.closest("label").classList.add("required"); });
    const box = $("#rp-extra"), fields = $("#rp-extra-fields");
    const keep = {};
    $$("input", fields).forEach((i) => { keep[i.id] = i.value; });
    fields.replaceChildren();
    const extra = report ? report.extra_fields : [];
    extra.forEach((f) => {
      const label = el("label"), span = el("span", "", (state.lang === "zh-TW" ? f.label_zh : f.label_en) || f.label_en || f.label_zh);
      const input = el("input"); input.type = "text"; input.maxLength = 2000; input.id = "rp-x-" + f.key;
      input.value = keep[input.id] || "";
      if (f.required) label.classList.add("required");
      label.append(span, input); fields.appendChild(label);
    });
    box.hidden = !extra.length;
    $("#rp-required-hint").hidden = !report || !(report.required.length || extra.some((f) => f.required));
    if (report && report.language && !state.reportLangTouched) $("#rp-language").value = report.language;
  }

  // ---- administration of profiles
  function renderProfileList() {
    const table = $("#profile-list"); table.replaceChildren();
    const head = el("tr");
    ["admin.pf_col_name", "admin.pf_col_rev", "admin.pf_col_updated", "admin.col_actions"].forEach((k) => cell(head, t(k), "th"));
    table.appendChild(head);
    if (!state.profiles.length) { const tr = el("tr"); const c = cell(tr, t("saved.empty")); c.colSpan = 4; table.appendChild(tr); }
    state.profiles.forEach((p) => {
      const tr = el("tr");
      cell(tr, p.name); cell(tr, String(p.revision)); cell(tr, when(p.updated_at));
      const actions = cell(tr, "");
      const edit = el("button", "", t("admin.profile_edit")); edit.addEventListener("click", () => openProfileEditor(p));
      const del = el("button", "", t("admin.profile_delete"));
      del.addEventListener("click", async () => {
        if (!window.confirm(t("admin.profile_confirm_delete", { name: p.name }))) return;
        await guarded(async () => { await api(`/api/profiles/${p.id}`, { method: "DELETE" }); });
        await loadProfiles(); renderProfileList(); closeProfileEditor();
      });
      actions.append(edit, del); table.appendChild(tr);
    });
  }
  const STAGES = ["machine", "preliminary", "production"], CLASSES = ["critical", "major", "minor", "others"];
  const REQUIRABLE = ["process", "machine", "site", "process_ref", "machine_ref", "persons", "period_text", "part_name", "part_number",
    "characteristic", "unit", "target", "technical_conditions", "deviations", "sampling_frequency", "recommendations", "uncertainty"];
  function buildTargetsTable() {
    const table = $("#pf-targets"); table.replaceChildren();
    const head = el("tr"); cell(head, "", "th");
    CLASSES.forEach((c) => cell(head, t("analysis.class_" + c), "th")); table.appendChild(head);
    STAGES.forEach((s) => {
      const tr = el("tr"); cell(tr, t("analysis.stage_" + s), "th");
      CLASSES.forEach((c) => {
        const td = el("td"), d = state.defaultTargets ? state.defaultTargets[s][c] : ["", ""];
        ["p", "pk"].forEach((k, i) => {
          const input = el("input", "small"); input.type = "number"; input.step = "any"; input.min = "0"; input.id = `pf-t-${s}-${c}-${k}`;
          input.placeholder = String(d[i]); td.appendChild(input);
        });
        tr.appendChild(td);
      });
      table.appendChild(tr);
    });
  }
  function buildRequiredBoxes(selected) {
    const box = $("#pf-required"); box.replaceChildren();
    REQUIRABLE.forEach((n) => {
      const label = el("label", "check"), input = el("input"); input.type = "checkbox"; input.id = "pf-req-" + n; input.checked = selected.includes(n);
      label.append(input, el("span", "", t("report." + n))); box.appendChild(label);
    });
  }
  function addExtraRow(f) {
    const row = el("div", "row"), mk = (cls, ph, v) => { const i = el("input", cls); i.type = "text"; i.placeholder = ph; i.value = v || ""; return i; };
    const key = mk("key", t("admin.pf_extra_key"), f && f.key), en = mk("lab", t("admin.pf_extra_en"), f && f.label_en), zh = mk("lab", t("admin.pf_extra_zh"), f && f.label_zh);
    key.maxLength = 31; en.maxLength = 100; zh.maxLength = 100;
    const req = el("input"); req.type = "checkbox"; req.checked = !!(f && f.required);
    const rl = el("label", "check"); rl.append(req, el("span", "", t("admin.pf_extra_required")));
    const rm = el("button", "", "✕"); rm.addEventListener("click", () => row.remove());
    row.append(key, en, zh, rl, rm); $("#pf-extra").appendChild(row);
  }
  function showLogo() {
    $("#pf-logo-preview").hidden = !state.logo; $("#pf-logo-remove").hidden = !state.logo;
    if (state.logo) $("#pf-logo-preview").src = state.logo; else $("#pf-logo-preview").removeAttribute("src");
  }
  function openProfileEditor(p) {
    state.editing = p ? p.id : "new";
    const a = p ? p.analysis : {}, r = p ? p.report : { extra_fields: [], required: [], show_element_21: true, show_element_22: true };
    $("#pf-title-line").textContent = p ? t("admin.profile_edit_title", { name: p.name }) : t("admin.profile_new");
    $("#pf-name").value = p ? p.name : "";
    $("#pf-alpha").value = a.alpha ?? ""; $("#pf-est").value = a.estimate_confidence ?? ""; $("#pf-tgt").value = a.target_confidence ?? "";
    $("#pf-stab").value = a.stability_confidence ?? ""; $("#pf-edition").value = a.edition || ""; $("#pf-mode").value = a.stability_mode || "";
    $("#pf-rules-on").checked = "rules" in a; fillRules("pf-r-", a.rules, !("rules" in a));
    buildTargetsTable();
    STAGES.forEach((s) => CLASSES.forEach((c) => {
      const pair = a.targets && a.targets[s] && a.targets[s][c];
      $(`#pf-t-${s}-${c}-p`).value = pair ? pair[0] : ""; $(`#pf-t-${s}-${c}-pk`).value = pair ? pair[1] : "";
    }));
    $("#pf-org").value = r.organization || ""; $("#pf-title").value = r.title || ""; $("#pf-form").value = r.form_no || "";
    $("#pf-rev").value = r.revision || ""; $("#pf-footer").value = r.footer || ""; $("#pf-lang").value = r.language || "";
    $("#pf-accent-on").checked = !!r.accent; $("#pf-accent").value = r.accent || "#1f5fbf"; $("#pf-accent").disabled = !r.accent;
    $("#pf-el21").checked = r.show_element_21 !== false; $("#pf-el22").checked = r.show_element_22 !== false;
    state.logo = r.logo || ""; showLogo();
    buildRequiredBoxes(r.required || []);
    $("#pf-extra").replaceChildren(); (r.extra_fields || []).forEach(addExtraRow);
    $("#pf-msg").textContent = ""; $("#pf-editor").hidden = false; $("#pf-editor").scrollIntoView({ block: "nearest" });
  }
  function closeProfileEditor() { state.editing = null; $("#pf-editor").hidden = true; }
  function readProfileForm() {
    const num = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
    const analysis = {};
    [["alpha", "#pf-alpha"], ["estimate_confidence", "#pf-est"], ["target_confidence", "#pf-tgt"], ["stability_confidence", "#pf-stab"]]
      .forEach(([k, id]) => { const v = num(id); if (v !== null) analysis[k] = v; });
    if ($("#pf-edition").value) analysis.edition = $("#pf-edition").value;
    if ($("#pf-mode").value) analysis.stability_mode = $("#pf-mode").value;
    if ($("#pf-rules-on").checked) analysis.rules = readRules("pf-r-");
    const targets = {};
    for (const s of STAGES) for (const c of CLASSES) {
      const p = num(`#pf-t-${s}-${c}-p`), pk = num(`#pf-t-${s}-${c}-pk`);
      if (p === null && pk === null) continue;
      if (p === null || pk === null) throw { code: "profile_targets_pair", params: {} };
      (targets[s] = targets[s] || {})[c] = [p, pk];
    }
    if (Object.keys(targets).length) analysis.targets = targets;
    const report = {
      organization: $("#pf-org").value, title: $("#pf-title").value, form_no: $("#pf-form").value, revision: $("#pf-rev").value,
      footer: $("#pf-footer").value, accent: $("#pf-accent-on").checked ? $("#pf-accent").value : "", logo: state.logo,
      language: $("#pf-lang").value, show_element_21: $("#pf-el21").checked, show_element_22: $("#pf-el22").checked,
      required: REQUIRABLE.filter((n) => $("#pf-req-" + n).checked),
      extra_fields: $$("#pf-extra .row").map((row) => { const [key, en, zh] = $$("input[type=text]", row); return { key: key.value.trim(), label_en: en.value, label_zh: zh.value, required: $("input[type=checkbox]", row).checked }; }),
    };
    return { name: $("#pf-name").value, analysis, report };
  }
  async function saveProfile() {
    await guarded(async () => {
      const body = readProfileForm();
      const url = state.editing === "new" ? "/api/profiles" : `/api/profiles/${state.editing}`;
      const saved = await api(url, { method: state.editing === "new" ? "POST" : "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      state.editing = saved.id;
      await loadProfiles(); renderProfileList();
      $("#pf-msg").textContent = t("admin.profile_saved", { name: saved.name, rev: saved.revision });
    });
  }
  function onLogoChosen(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => { state.logo = String(reader.result); showLogo(); };
    reader.readAsDataURL(file);
  }

  // ---------------------------------------------------------------- SPC at the line (control loop 1)
  const M = state.mon;
  const RULES = ["beyond_limits", "run", "trend", "middle_third", "two_of_three_beyond_2s", "four_of_five_beyond_1s", "fifteen_within_1s"];
  const showMonitorView = (which) => {
    ["#mon-list-view", "#mon-editor", "#mon-detail"].forEach((sel) => { $(sel).hidden = sel !== which; });
  };
  function startAlertPolling() {
    clearInterval(M.timer);
    const poll = async () => {
      if (!state.user) return;
      try { renderAlerts(await api("/api/alerts")); } catch (e) { /* the next poll tries again */ }
    };
    poll();
    M.timer = setInterval(poll, 20000);
  }
  function renderAlerts(a) {
    const badge = $("#alert-badge");
    badge.hidden = !a.open;
    badge.textContent = String(a.open);
    badge.title = t("mon.alerts_line", { open: a.open, unack: a.unacknowledged, overdue: a.overdue });
    $("#mon-alerts").textContent = a.open ? t("mon.alerts_line", { open: a.open, unack: a.unacknowledged, overdue: a.overdue }) : t("mon.alerts_none");
    $("#mon-alerts").className = a.open ? "strong status-alarm" : "muted";
  }
  async function loadMonitors() {
    if (M.id && !$("#mon-detail").hidden) return;  // an open monitor stays open when the tab is shown again
    await guarded(async () => {
      M.list = (await api("/api/monitors")).monitors;
      renderMonitorList();
      showMonitorView("#mon-list-view");
    });
  }
  function renderMonitorList() {
    const table = $("#mon-list"); table.replaceChildren();
    const head = el("tr");
    ["mon.col_name", "mon.col_chart", "mon.col_line", "mon.col_limits", "mon.col_last", "mon.col_incidents", ""].forEach((k) => cell(head, k ? t(k) : "", "th"));
    table.appendChild(head);
    if (!M.list.length) { const tr = el("tr"); const c = cell(tr, t("mon.none")); c.colSpan = 7; table.appendChild(tr); }
    M.list.forEach((m) => {
      const tr = el("tr");
      cell(tr, m.name); cell(tr, `${t("result.kind_" + m.kind)}${m.n > 1 ? ", n = " + m.n : ""}`); cell(tr, m.line);
      cell(tr, `#${m.limits_rev}`);
      cell(tr, m.last_point ? `#${m.last_point.seq} · ${when(m.last_point.taken_at)}` : "–");
      const inc = cell(tr, m.open_incidents ? t("mon.open_incidents", { n: m.open_incidents }) : "–");
      if (m.open_incidents) inc.className = "status-alarm";
      const actions = cell(tr, "");
      const open = el("button", "", t("mon.open")); open.addEventListener("click", () => openMonitor(m.id));
      actions.appendChild(open);
      if (!m.active) actions.appendChild(el("span", "muted", " " + t("mon.inactive")));
      table.appendChild(tr);
    });
  }
  async function openMonitor(id, keepResult) {
    await guarded(async () => {
      M.id = id;
      M.view = await api(`/api/monitors/${id}`);
      if (!keepResult) M.last = null;
      renderMonitor();
      showMonitorView("#mon-detail");
    });
  }
  function ocapTable(table, instructions) {
    table.replaceChildren();
    const head = el("tr");
    ["mon.ocap_criterion", "mon.ocap_action", "mon.ocap_responsible", "mon.ocap_escalate", "mon.ocap_after"].forEach((k) => cell(head, t(k), "th"));
    table.appendChild(head);
    instructions.forEach((i) => {
      const tr = el("tr");
      cell(tr, t(i.rule === "default" ? "mon.ocap_default" : "alarmrule." + i.rule)); cell(tr, i.operator_action || "–"); cell(tr, i.responsible || "–");
      cell(tr, i.escalate_to || "–"); cell(tr, i.escalate_after_min ? `${i.escalate_after_min} min` : "–");
      table.appendChild(tr);
    });
  }
  function renderMonitor() {
    const v = M.view, m = v.monitor, lim = v.limits;
    $("#md-title").textContent = m.name;
    $("#md-sub").textContent = t("mon.sub", { process: m.process || "–", characteristic: m.characteristic, unit: m.unit || "–", line: m.line || "–",
      chart: t("result.kind_" + m.kind), n: m.n, rev: lim.revision, by: lim.created_by, at: when(lim.created_at) });
    // the action plan must be known
    const plan = [{ rule: "default", ...m.ocap.default }].concat(Object.entries(m.ocap.rules).map(([rule, c]) => ({ rule, ...c })));
    $("#md-ack").hidden = !(m.require_ack && !v.acknowledged);
    ocapTable($("#md-plan").firstChild || $("#md-plan").appendChild(el("table", "grid")), plan);
    renderIncident();
    // entry fields follow the subgroup size
    const box = $("#md-values");
    if (box.dataset.n !== String(m.n) || box.dataset.id !== String(m.id)) {
      box.replaceChildren(); box.dataset.n = String(m.n); box.dataset.id = String(m.id);
      for (let i = 0; i < m.n; i++) {
        const input = el("input"); input.type = "number"; input.step = "any"; input.id = "md-v" + i;
        input.setAttribute("aria-label", t("mon.value_n", { i: i + 1 }));
        input.placeholder = String(i + 1);
        input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submitPoint(); } });
        box.appendChild(input);
      }
    }
    $("#md-entry-card").hidden = !m.active;
    renderMonitorResult();
    drawMonitorCharts();
    renderMonitorPoints();
    renderLimits();
  }
  function renderIncident() {
    const inc = M.view.incident, card = $("#md-incident");
    card.hidden = !inc;
    if (!inc) return;
    $("#mi-title").textContent = t("mon.incident_title", { id: inc.id, at: when(inc.opened_at), seq: inc.point_seq });
    const rules = inc.rules.map((r) => `${t("mon.chart_" + r.chart)}: ${t("alarmrule." + r.rule)}`).join("; ");
    $("#mi-line").textContent = `${rules} · ${inc.acked_by ? t("mon.taken_over_by", { by: inc.acked_by, at: when(inc.acked_at) }) : t("mon.not_taken_over")}` +
      (inc.overdue ? " · " + t("mon.overdue", { min: inc.escalate_after_min }) : "");
    $("#mi-line").className = "strong " + (inc.overdue ? "status-alarm" : "");
    ocapTable($("#mi-instructions"), inc.instructions);
    const ul = $("#mi-events"); ul.replaceChildren();
    inc.events.forEach((e) => {
      const what = e.kind === "action" || e.kind === "observation" ? `${t("mon.kind_" + e.kind)} (${t("mon.step_" + e.step)})` : t("mon.kind_" + e.kind);
      const li = el("li", "", `${when(e.at)} · ${e.by} · ${what}${e.text ? ": " : ""}`);
      if (e.text) li.appendChild(el("span", "", e.text));
      ul.appendChild(li);
    });
  }
  function monitorPart(which) {
    const v = M.view, lim = v.limits[which === "loc" ? "location" : "variation"];
    const rows = v.points.filter((p) => p.valid && (which === "loc" || p.var !== null));
    const key = which === "loc" ? "location" : "variation";
    return {
      values: rows.map((p) => (which === "loc" ? p.loc : p.var)),
      labels: rows.map((p) => p.label || `#${p.seq}`),
      lcl: lim.lcl, center: lim.cl, ucl: lim.ucl, wlcl: lim.wlcl ?? null, wucl: lim.wucl ?? null,
      alarms: rows.map((p, i) => ({ index: i, hit: p.alarms.some((a) => a.chart === key) })).filter((a) => a.hit),
    };
  }
  function drawMonitorCharts() {
    const m = M.view.monitor;
    const loc = monitorPart("loc"), vr = monitorPart("var");
    const draw = (sel, part, title) => { if (part.values.length) drawChart($(sel), part, title); else $(sel).replaceChildren(el("p", "muted", t("mon.no_points"))); };
    draw("#md-chart-loc", loc, `${t("result.chart_location")} – ${t(m.kind === "imr" ? "result.series_location_imr" : "result.series_location_xbar")}`);
    draw("#md-chart-var", vr, `${t("result.chart_variation")} – ${t("result.series_variation_" + m.kind)}`);
  }
  function statusOf(p) {
    if (!p.valid) return [t("mon.status_invalid"), ""];
    if (p.alarms.length) return [p.alarms.map((a) => t("alarmrule." + a.rule)).join(", "), "status-alarm"];
    if (p.warnings.length) return [t("mon.status_warning"), "status-warning"];
    return [t("mon.status_ok"), "status-ok"];
  }
  function renderMonitorPoints() {
    const table = $("#md-points"); table.replaceChildren();
    const head = el("tr");
    ["mon.col_no", "mon.col_time", "mon.col_label", "mon.col_values", "mon.col_loc", "mon.col_var", "mon.col_status", "mon.col_by", ""].forEach((k) => cell(head, k ? t(k) : "", "th"));
    table.appendChild(head);
    M.view.points.slice(-30).reverse().forEach((p) => {
      const tr = el("tr", p.valid ? "" : "invalid-point");
      cell(tr, String(p.seq)); cell(tr, when(p.taken_at)); cell(tr, p.label); cell(tr, p.values.map((x) => sig(x, 5)).join(" "));
      cell(tr, sig(p.loc, 5)); cell(tr, p.var === null ? "–" : sig(p.var, 4));
      const [text, cls] = statusOf(p);
      const s = cell(tr, text); s.className = cls; if (p.invalid) s.title = `${p.invalid.reason} (${p.invalid.by})`;
      cell(tr, p.entered_by);
      const actions = cell(tr, "");
      if (p.valid && state.user.role !== "viewer") {
        const b = el("button", "", t("mon.invalidate"));
        b.addEventListener("click", async () => {
          const reason = window.prompt(t("mon.invalidate_prompt", { seq: p.seq }));
          if (!reason) return;
          await guarded(async () => { await post(`/api/monitors/${M.id}/points/${p.seq}/invalid`, { reason }); });
          openMonitor(M.id);
        });
        actions.appendChild(b);
      }
      table.appendChild(tr);
    });
  }
  function renderMonitorResult() {
    const box = $("#md-result"); box.replaceChildren();
    const r = M.last;
    if (!r) return;
    const div = el("div", { ok: "ok-box", warning: "warn-box", alarm: "alarm-box" }[r.status]);
    div.appendChild(el("p", "strong status-" + r.status, t("mon.result_" + r.status, { seq: r.point.seq })));
    if (r.verification) div.appendChild(el("p", "", t("mon.verification_note")));
    if (r.status === "alarm") {
      div.appendChild(el("p", "", r.point.alarms.map((a) => `${t("mon.chart_" + a.chart)}: ${t("alarmrule." + a.rule)}`).join("; ")));
      const table = el("table", "grid"); ocapTable(table, r.instructions); div.appendChild(table);
      div.appendChild(el("p", "strong", t("mon.next_step")));
    }
    box.appendChild(div);
  }
  async function submitPoint() {
    const m = M.view.monitor;
    const values = [];
    for (let i = 0; i < m.n; i++) {
      const raw = $("#md-v" + i).value.trim();
      if (raw === "") return showError({ code: "wrong_value_count", message: "", params: { n: m.n } });
      values.push(Number(raw));
    }
    const body = { values, label: $("#md-label").value };
    const taken = $("#md-taken").value;
    if (taken) body.taken_at = new Date(taken).toISOString();
    await guarded(async () => {
      M.last = await post(`/api/monitors/${M.id}/points`, body);
      $$("#md-values input").forEach((i) => { i.value = ""; });
      $("#md-label").value = ""; $("#md-taken").value = "";
      M.view = await api(`/api/monitors/${M.id}`);
      renderMonitor();
      startAlertPolling();
      const first = $("#md-v0"); if (first) first.focus();
    });
  }
  async function incidentAction(kind) {
    const inc = M.view.incident;
    const body = { kind, step: kind === "action" ? $("#mi-step").value : "other", text: $("#mi-text").value };
    await guarded(async () => {
      await post(`/api/monitors/${M.id}/incidents/${inc.id}/events`, body);
      $("#mi-text").value = "";
      M.view = await api(`/api/monitors/${M.id}`); renderMonitor(); startAlertPolling();
    });
  }
  async function closeIncident() {
    const inc = M.view.incident;
    await guarded(async () => {
      await post(`/api/monitors/${M.id}/incidents/${inc.id}/close`, { outcome: $("#mi-outcome").value, text: $("#mi-close-text").value });
      $("#mi-close-text").value = ""; M.last = null;
      M.view = await api(`/api/monitors/${M.id}`); renderMonitor(); startAlertPolling();
    });
  }
  function sourceText(s) {
    if (s.type === "dataset") return t("mon.src_text_dataset", { name: s.name || s.dataset_id, n: s.n_values });
    if (s.type === "points") return t("mon.src_text_points", { from: s.seq_from, to: s.seq_to, n: s.n_points });
    return t("mon.src_text_parameters", { mu: sig(s.mu, 6), sigma: sig(s.sigma, 5) });
  }
  function renderLimits() {
    const v = M.view, lim = v.limits, box = $("#md-limits");
    box.replaceChildren();
    const row = (label, o) => `${label}: ${sig(o.lcl, 6)} / ${sig(o.cl, 6)} / ${sig(o.ucl, 6)}` + (o.wlcl !== undefined ? ` (${t("mon.warning_limit")} ${sig(o.wlcl, 6)} / ${sig(o.wucl, 6)})` : "");
    box.appendChild(el("p", "", t("mon.limits_now", { rev: lim.revision, mu: sig(lim.mu, 6), sigma: sig(lim.sigma, 5) })));
    box.appendChild(el("p", "", row(t("result.chart_location"), lim.location)));
    box.appendChild(el("p", "", row(t("result.chart_variation"), lim.variation)));
    const table = el("table", "grid"), head = el("tr");
    ["mon.rev", "mon.col_time", "mon.col_by", "mon.source", "mon.reason"].forEach((k) => cell(head, t(k), "th")); table.appendChild(head);
    v.limits_history.forEach((h) => {
      const tr = el("tr"); cell(tr, `#${h.revision}`); cell(tr, when(h.created_at)); cell(tr, h.created_by); cell(tr, sourceText(h.source)); cell(tr, h.reason); table.appendChild(tr);
    });
    const wrap = el("div", "scroll"); wrap.appendChild(table); box.appendChild(wrap);
  }
  async function setLimits() {
    const type = $("#nl-type").value, num = (id) => Number($(id).value);
    const source = type === "points" ? { type, seq_from: num("#nl-from"), seq_to: num("#nl-to") }
      : type === "parameters" ? { type, mu: num("#nl-mu"), sigma: num("#nl-sigma") } : { type, dataset_id: $("#nl-dataset").value };
    await guarded(async () => {
      M.view = await post(`/api/monitors/${M.id}/limits`, { source, reason: $("#nl-reason").value });
      $("#nl-reason").value = ""; renderMonitor();
    });
  }
  async function fillDatasetSelect(select) {
    select.replaceChildren();
    try { (await api("/api/datasets")).datasets.forEach((d) => { const o = el("option", "", `${d.name} (${d.n_total})`); o.value = d.id; select.appendChild(o); }); } catch (e) { /* none */ }
  }
  function syncSourceFields() {
    const type = $("#nl-type").value;
    ["points", "parameters", "dataset"].forEach((k) => $$(".nl-" + k).forEach((e) => { e.hidden = k !== type; }));
    if (type === "dataset" && !$("#nl-dataset").options.length) fillDatasetSelect($("#nl-dataset"));
  }
  // ---- ongoing performance and capability (draft 10.4)
  async function showOngoing() {
    const window_ = Number($("#og-window").value) || 125;
    await guarded(async () => {
      const o = await api(`/api/monitors/${M.id}/ongoing?window=${window_}`);
      const out = $("#og-out"); out.replaceChildren();
      const w = o.window, r = o.result;
      out.appendChild(el("p", "muted", t("mon.og_window", { n: w.points, from: w.from_seq, to: w.to_seq, a: when(w.from), b: when(w.to) })));
      if (r.indices) {
        const names = `${r.names.pk} = ${fmt(r.indices.pk)}` + (r.indices.p !== null ? ` · ${r.names.p} = ${fmt(r.indices.p)}` : "");
        out.appendChild(el("p", "strong", `${names} · ${t("result.class_" + r.stability.class)}`));
        if (r.targets && !r.targets.blocked) out.appendChild(el("p", "", t("mon.og_targets", { p: fmt(r.targets.p), pk: fmt(r.targets.pk), verdict: t("result.verdict_" + (r.targets.verdict_pk || "none")) })));
      } else out.appendChild(el("p", "muted", t("mon.og_no_spec")));
      out.appendChild(el("p", "strong", o.quadrant.number ? t("mon.quadrant_" + o.quadrant.number) : t("mon.quadrant_unknown")));
      out.appendChild(el("p", "muted", t("mon.quadrant_note")));
      if (o.trend.length) {
        const table = el("table", "grid"), head = el("tr");
        ["mon.og_end", "mon.og_index", "mon.og_stability", "mon.og_quadrant"].forEach((k) => cell(head, t(k), "th")); table.appendChild(head);
        o.trend.forEach((x) => { const tr = el("tr"); cell(tr, `#${x.end_seq}`); cell(tr, `${x.name_pk} ${fmt(x.pk)}`); cell(tr, t("result.class_" + x.stability)); cell(tr, x.quadrant.number ? ["", "I", "II", "III", "IV"][x.quadrant.number] : "–"); table.appendChild(tr); });
        const wrap = el("div", "scroll"); wrap.appendChild(table); out.appendChild(wrap);
      }
      const lr = o.limits_review;
      out.appendChild(el("p", lr.verdict === "ok" ? "status-ok" : "status-warning", t("mon.review_" + lr.verdict, {
        ratio: lr.sigma_ratio === null ? "–" : fmt(lr.sigma_ratio), alarms: lr.alarm_points, expected: fmt(lr.expected_alarm_points, 1), shift: lr.location_shift_in_se === null ? "–" : fmt(lr.location_shift_in_se) })));
      const rs = o.response;
      const fmtSec = (s) => (s === null || s === undefined ? "–" : s < 120 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`);
      out.appendChild(el("p", "muted", t("mon.og_response", { n: rs.incidents, open: rs.open, overdue: rs.overdue,
        ack: rs.to_ack ? fmtSec(rs.to_ack.median) : "–", action: rs.to_action ? fmtSec(rs.to_action.median) : "–", close: rs.to_close ? fmtSec(rs.to_close.median) : "–" })));
    });
  }
  async function ongoingReport() {
    await guarded(async () => {
      const r = await post(`/api/monitors/${M.id}/ongoing-report`, { window: Number($("#og-window").value) || 125, language: state.lang });
      const out = $("#og-out");
      const p = el("p", "row");
      p.appendChild(el("span", "strong ok", t("report.created", { id: r.id })));
      p.appendChild(linkButton(t("report.open"), r.urls.html, true)); p.appendChild(linkButton(t("report.download"), r.urls.download));
      p.appendChild(linkButton(t("report.excel"), `${r.urls.html}/report.xlsx`)); out.prepend(p);
    });
  }
  // ---- set up a monitor
  function ocapEditorRows(ocap) {
    const table = $("#me-ocap"); table.replaceChildren();
    const head = el("tr");
    ["mon.ocap_criterion", "mon.ocap_action", "mon.ocap_responsible", "mon.ocap_escalate", "mon.ocap_after"].forEach((k) => cell(head, t(k), "th")); table.appendChild(head);
    ["default"].concat(RULES).forEach((key) => {
      const c = key === "default" ? ocap.default : ocap.rules[key] || {};
      const tr = el("tr"); tr.dataset.key = key;
      cell(tr, key === "default" ? t("mon.ocap_default") : t("alarmrule." + key));
      ["operator_action", "responsible", "escalate_to"].forEach((f) => { const td = el("td"), i = el("input"); i.type = "text"; i.maxLength = f === "operator_action" ? 2000 : 200; i.value = c[f] || ""; i.dataset.f = f; td.appendChild(i); tr.appendChild(td); });
      const td = el("td"), i = el("input", "small"); i.type = "number"; i.min = "0"; i.max = "10080"; i.step = "1"; i.value = c.escalate_after_min || 0; i.dataset.f = "escalate_after_min"; td.appendChild(i); tr.appendChild(td);
      table.appendChild(tr);
    });
  }
  function readOcap() {
    const out = { default: {}, rules: {} };
    $$("#me-ocap tr[data-key]").forEach((tr) => {
      const c = {}; $$("input", tr).forEach((i) => { c[i.dataset.f] = i.dataset.f === "escalate_after_min" ? Number(i.value) || 0 : i.value; });
      if (tr.dataset.key === "default") out.default = c;
      else if (c.operator_action || c.responsible || c.escalate_to || c.escalate_after_min) out.rules[tr.dataset.key] = c;
    });
    return out;
  }
  async function openMonitorEditor(monitor) {
    M.editing = monitor ? monitor.id : "new";
    const m = monitor || { name: "", process: "", characteristic: "", unit: "", line: "", kind: "xbar-s", n: 5, alpha: null, warn_alpha: 0.05,
      specs: {}, rules: { beyond_limits: true }, ocap: { default: {}, rules: {} }, require_ack: true, active: true };
    $("#mon-editor-title").textContent = monitor ? t("mon.edit_title", { name: m.name }) : t("mon.new");
    ["name", "process", "characteristic", "unit", "line"].forEach((k) => { $("#me-" + k).value = m[k] || ""; });
    $("#me-kind").value = m.kind; $("#me-n").value = m.n; $("#me-alpha").value = m.alpha && Math.abs(m.alpha - 0.01) < 1e-12 ? "0.01" : "";
    $("#me-warn").value = m.warn_alpha ?? "";
    ["#me-kind", "#me-n", "#me-alpha", "#me-warn"].forEach((s) => { $(s).disabled = !!monitor; });  // the shape is fixed once there are limits
    $("#me-source").hidden = !!monitor;
    $("#me-lsl").value = m.specs.lsl ?? ""; $("#me-usl").value = m.specs.usl ?? ""; $("#me-class").value = m.specs.target_class || ""; $("#me-model").value = m.specs.model || "";
    fillRules("mon-r-", m.rules, false);
    ocapEditorRows(m.ocap);
    $("#me-ack").checked = m.require_ack; $("#me-active").checked = m.active;
    syncKindFields();
    if (!monitor) await fillDatasetSelect($("#me-dataset"));
    showMonitorView("#mon-editor");
  }
  function syncKindFields() {
    if ($("#me-kind").value === "imr") $("#me-n").value = 1;
    else if (Number($("#me-n").value) < 2) $("#me-n").value = 5;
    if (!$("#me-kind").disabled) $("#me-n").disabled = $("#me-kind").value === "imr";
  }
  function readMonitorEditor() {
    const num = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
    const editing = M.editing !== "new" ? M.view.monitor : null;
    const warn = editing ? editing.warn_alpha : num("#me-warn");
    const config = {
      name: $("#me-name").value, process: $("#me-process").value, characteristic: $("#me-characteristic").value, unit: $("#me-unit").value, line: $("#me-line").value,
      kind: editing ? editing.kind : $("#me-kind").value, n: editing ? editing.n : Number($("#me-n").value), warn_alpha: warn,
      specs: { lsl: num("#me-lsl"), usl: num("#me-usl"), target_class: $("#me-class").value || null, model: $("#me-model").value || null,
        controlled_stable: editing ? editing.specs.controlled_stable : false, edition: editing ? editing.specs.edition : "draft" },
      rules: readRules("mon-r-"), ocap: readOcap(), require_ack: $("#me-ack").checked, active: $("#me-active").checked,
    };
    if (editing) config.alpha = editing.alpha; else if ($("#me-alpha").value) config.alpha = Number($("#me-alpha").value);
    return config;
  }
  async function saveMonitor() {
    await guarded(async () => {
      const config = readMonitorEditor();
      if (M.editing === "new") {
        const type = $("#me-src-type").value;
        const source = type === "parameters" ? { type, mu: Number($("#me-mu").value), sigma: Number($("#me-sigma").value) } : { type, dataset_id: $("#me-dataset").value };
        M.view = await post("/api/monitors", { config, source });
      } else {
        M.view = await api(`/api/monitors/${M.editing}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ config }) });
      }
      M.id = M.view.monitor.id; M.last = null;
      renderMonitor(); showMonitorView("#mon-detail");
    });
  }
  function wireMonitor() {
    $("#mon-new").addEventListener("click", () => openMonitorEditor(null));
    $("#me-cancel").addEventListener("click", () => showMonitorView(M.id && M.editing !== "new" ? "#mon-detail" : "#mon-list-view"));
    $("#me-save").addEventListener("click", saveMonitor);
    $("#me-kind").addEventListener("change", syncKindFields);
    $("#me-src-type").addEventListener("change", () => { const d = $("#me-src-type").value === "dataset"; $("#me-src-dataset").hidden = !d; $("#me-src-parameters").hidden = d; });
    $("#mon-back").addEventListener("click", () => { M.id = null; M.view = null; loadMonitors(); });
    $("#mon-edit").addEventListener("click", () => openMonitorEditor(M.view.monitor));
    $("#mon-refresh").addEventListener("click", () => openMonitor(M.id, true));
    $("#md-ack-btn").addEventListener("click", () => guarded(async () => { await post(`/api/monitors/${M.id}/ack`, {}); M.view = await api(`/api/monitors/${M.id}`); renderMonitor(); }));
    $("#md-submit").addEventListener("click", submitPoint);
    $("#mi-ack").addEventListener("click", () => incidentAction("ack"));
    $("#mi-action").addEventListener("click", () => incidentAction("action"));
    $("#mi-observation").addEventListener("click", () => incidentAction("observation"));
    $("#mi-escalate").addEventListener("click", () => incidentAction("escalation"));
    $("#mi-close").addEventListener("click", closeIncident);
    $("#nl-type").addEventListener("change", syncSourceFields); syncSourceFields();
    $("#nl-save").addEventListener("click", setLimits);
    $("#og-show").addEventListener("click", showOngoing);
    $("#og-report").addEventListener("click", ongoingReport);
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
      await loadProfiles(); renderProfileList();
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
      ["viewer", "operator", "engineer", "admin"].forEach((r) => { const o = el("option", "", t("role." + r)); o.value = r; role.appendChild(o); });
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
    if (state.user && M.view && !$("#mon-detail").hidden) renderMonitor();
    if (state.user && !$("#mon-list-view").hidden && !$("#tab-monitor").hidden) renderMonitorList();
    if (state.user && state.profile) { onProfileChange(); }
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
    wireMonitor();
    $("#a-profile").addEventListener("change", onProfileChange);
    $("#pf-new").addEventListener("click", () => openProfileEditor(null));
    $("#pf-save").addEventListener("click", saveProfile);
    $("#pf-cancel").addEventListener("click", closeProfileEditor);
    $("#pf-extra-add").addEventListener("click", () => addExtraRow(null));
    $("#pf-rules-on").addEventListener("change", (e) => fillRules("pf-r-", readRules("pf-r-"), !e.target.checked));
    $("#pf-accent-on").addEventListener("change", (e) => { $("#pf-accent").disabled = !e.target.checked; });
    $("#pf-logo-file").addEventListener("change", (e) => onLogoChosen(e.target.files[0]));
    $("#pf-logo-remove").addEventListener("click", () => { state.logo = ""; $("#pf-logo-file").value = ""; showLogo(); });
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
      state.defaultTargets = meta.default_targets;
      if (meta.session) enterApp(meta.session); else showLogin();
    } catch (e) { showError({ code: "network", message: String(e && e.message || e.code), params: {} }); }
  }
  start();
})();

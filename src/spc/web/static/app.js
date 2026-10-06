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
    if (e.code === "invalid_input" || e.code === "bad_source" || e.code === "bad_count") p.message = e.message;
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
    if (name === "study") loadStudies();
    if (name === "msa") loadMsa();
    if (name === "plan") loadPlans();
    if (name === "validation") loadValidation();
    if (name === "equipment") loadEquipment();
    if (name === "roles") loadRoles();
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
    state.offset = 0; state.selected.clear(); state.suspects.clear(); state.result = null; state.reportOut = null; state.modelSuggestion = null; state.stateTests = null; renderModelSuggestion(); renderStateTests();
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
      limit_method: $("#a-limit-method").value,
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
      ["alpha", "stability_mode", "edition", "rules", "limit_method"].forEach((k) => { if (k in fixed) delete body[k]; });
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
  // ---- suggestion of the time-dependent model (draft 9.4): evidence and a button to take it over, the person decides
  async function suggestModel() {
    const hints = {};
    $$("#a-model-assist input[data-hint]").forEach((i) => { if (i.checked) hints[i.dataset.hint] = true; });
    const body = { hints };
    if (!state.dataset.has_subgroup) { const n = num("#a-size"); if (n) body.subgroup_size = Math.max(2, Math.min(25, Math.round(n))); }
    let r = null;
    await guarded(async () => { r = await post(`/api/datasets/${state.dataset.id}/time-model`, body); });
    state.modelSuggestion = r;
    renderModelSuggestion();
  }
  async function runStateTests() {
    const body = { by: $("#a-states-by").value.trim() || null };
    let r = null;
    await guarded(async () => { r = await post(`/api/datasets/${state.dataset.id}/state-tests`, body); });
    state.stateTests = r;
    renderStateTests();
  }
  function renderStateTests() {
    const box = $("#a-states-result"); box.replaceChildren();
    const r = state.stateTests;
    if (!r) return;
    const table = el("table", "grid");
    const head = el("tr"); ["st.col_state", "st.col_n", "st.col_mean", "st.col_sd", "st.col_grubbs"].forEach((k) => cell(head, t(k), "th")); table.appendChild(head);
    Object.entries(r.states).forEach(([name, s]) => {
      const tr = el("tr"); cell(tr, name); cell(tr, String(s.n)); cell(tr, sig(s.mean, 5)); cell(tr, sig(s.sd, 4));
      const g = s.grubbs;
      cell(tr, g.g === null ? "–" : t(g.outlier ? "st.outlier" : "st.no_outlier", { g: sig(g.g, 4), crit: sig(g.g_crit, 4), value: sig(g.value, 5) })).className = g.outlier ? "status-alarm" : "";
      table.appendChild(tr);
    });
    box.appendChild(table);
    box.appendChild(el("p", r.variance_differs ? "status-warning" : "status-ok", t("st.bartlett", { stat: sig(r.bartlett.statistic, 4), df: r.bartlett.df, p: sig(r.bartlett.p, 3), result: t(r.variance_differs ? "st.differs" : "st.same") })));
    box.appendChild(el("p", r.location_differs ? "status-warning" : "status-ok", t("st.fisher", { stat: sig(r.fisher.statistic, 4), df1: r.fisher.df1, df2: r.fisher.df2, p: sig(r.fisher.p, 3), result: t(r.location_differs ? "st.differs" : "st.same") })));
    if (r.skipped.length) box.appendChild(el("p", "muted", t("st.skipped", { states: r.skipped.join(", ") })));
  }
  function renderModelSuggestion() {
    const box = $("#a-model-suggestion"); box.replaceChildren();
    const r = state.modelSuggestion;
    if (!r) return;
    const g = r.groups;
    if (!r.model) {
      box.appendChild(el("p", "status-warning", t("tm.not_enough", { k: g.k, min: r.min_groups, values: r.min_values })));
      return;
    }
    const card = el("div", "ok-box");
    card.appendChild(el("p", "strong", t("tm.suggestion", { model: t("analysis.model_" + r.model), confidence: t("tm.conf_" + r.confidence) })));
    card.appendChild(el("p", "", t(g.blocks ? "tm.grouping_blocks" : "tm.grouping_subgroups", { k: g.k, n: g.n })));
    card.appendChild(el("p", "", t("tm.why") + " " + r.reasons.map((x) => t("tm.reason_" + x)).join("; ")));
    const ev = r.evidence, p = (v) => (v < 0.001 ? "< 0.001" : sig(v, 2));
    const ul = el("ul");
    [t("tm.ev_location", { anova: p(ev.location.anova_p), ratio: sig(ev.location.between_to_within, 2), trend: p(ev.location.trend_p), r2: sig(ev.location.trend_r2, 2), after: p(ev.location.after_trend_p) }),
      t("tm.ev_variation", { disp: p(ev.variation.dispersion_p), ratio: sig(ev.variation.dispersion_ratio, 2), sd: sig(ev.variation.sd_ratio, 3) }),
      t("tm.ev_shape", { inst: p(ev.shape.instantaneous_normal_p), res: p(ev.shape.resulting_normal_p), modes: ev.shape.modes, skew: sig(ev.shape.skewness, 2), kurt: sig(ev.shape.kurtosis, 3) })]
      .forEach((x) => ul.appendChild(el("li", "", x)));
    card.appendChild(ul);
    if (r.alternatives.length) card.appendChild(el("p", "", t("tm.alternatives", { list: r.alternatives.map((a) => `${t("analysis.model_" + a.model)} (${t("tm.when_" + a.when)})`).join("; ") })));
    r.hints.forEach((h) => card.appendChild(el("p", h.agrees ? "muted" : "status-warning", t(h.agrees ? "tm.hint_agrees" : "tm.hint_disagrees", { hint: t("tm.hint_" + h.hint), models: h.models.join(", ") }))));
    card.appendChild(el("p", "muted", t(r.in_statistical_control ? "tm.implication_control" : "tm.implication_no_control")));
    const use = el("button", "primary", t("tm.use", { model: r.model }));
    use.type = "button";
    use.addEventListener("click", () => { $("#a-model").value = r.model; });
    card.appendChild(use);
    box.appendChild(card);
  }
  async function runAnalysis(ev) {
    ev.preventDefault();
    await guarded(async () => {
      const body = buildAnalysisBody();
      state.result = await post(`/api/datasets/${state.dataset.id}/analyze`, body);
      fillMsaSelect($("#rp-msa"), $("#rp-msa").value);
      fillReportPlanSelect();
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
    const ys = part.values.concat(part.values2 || [], flat(part.lcl), flat(part.ucl), flat(part.center), flat(part.wlcl ?? null), flat(part.wucl ?? null)).filter((v) => v !== null);
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
      if (Array.isArray(v)) {
        const pts = [];
        v.forEach((val, i) => { pts.push(`${Math.max(ml, X(i) - half)},${Y(val)}`, `${Math.min(W - mr, X(i) + half)},${Y(val)}`); });
        root.appendChild(svg("polyline", { class: cls, points: pts.join(" "), fill: "none" }));
        return;
      }
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
    if (part.values2) {  // a second series on the same limits: the lower CUSUM
      root.appendChild(svg("polyline", { class: "series2", points: part.values2.map((v, i) => `${X(i)},${Y(v)}`).join(" ") }));
      part.values2.forEach((v, i) => {
        const c = svg("circle", { class: "dot2" + (part.alarms2 && part.alarms2.includes(i) ? " alarm" : ""), cx: X(i), cy: Y(v), r: n > 300 ? 1.8 : 3.2 });
        c.appendChild(svg("title", {}, t("result.point", { label: part.labels[i] || i + 1, value: sig(v) })));
        root.appendChild(c);
      });
    }
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
    const locKey = { imr: "result.series_location_imr", "median-r": "result.series_location_median" }[ch.kind] || "result.series_location_xbar";
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
        measurement_system_id: $("#rp-msa").value ? Number($("#rp-msa").value) : null,
        control_plan_id: $("#rp-plan").value ? Number($("#rp-plan").value) : null,
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
  const COUNT_KINDS = ["p", "np", "c", "u"];
  const isCount = (kind) => COUNT_KINDS.includes(kind);
  const COUNT_RULES = ["beyond_limits", "run", "trend"];
  const ACC_KINDS = ["acc-xbar", "acc-median", "acc-x"];
  const SEQ_KINDS = ["cusum", "ewma"];
  const SEQ_RULES = ["shift_up", "shift_down"];
  const isTol = (kind) => ACC_KINDS.includes(kind) || kind === "pre";
  const BASE_KIND = { "acc-xbar": "xbar-s", "acc-median": "median-r", "acc-x": "imr", zmr: "imr", "ext-xbar": "xbar-s" };
  const SHAPE_KINDS = ["ext-xbar", "pearson"];
  const DEP_KINDS = ["ar", "multistream"];
  const VEC_KINDS = ["t2", "mewma", "mcusum"];
  const isVec = (kind) => VEC_KINDS.includes(kind);
  const lines = (text) => text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l !== "");
  const numbers = (text) => text.split(/[\s,;]+/).filter((x) => x !== "").map(Number);
  // the extra settings of the extended limits and the Pearson chart, from the fields with the prefix "me" or "nl"
  function shapeExtra(prefix, kind, type) {
    const num = (id) => { const v = $(`#${prefix}-${id}`).value.trim(); return v === "" ? null : Number(v); };
    if (kind === "ext-xbar") {
      return type === "parameters" ? { sigma_out: num("sigma-out"), u_out: num("u-out") } : { method: $(`#${prefix}-ext-method`).value, u_out: num("u-out") };
    }
    if (kind === "pearson") {
      if (type !== "parameters") return { method: $(`#${prefix}-pear-method`).value };
      const out = { skew: num("skew"), kurt: num("kurt") };
      if (num("sigma-within") !== null) out.sigma_within = num("sigma-within");
      return out;
    }
    return {};
  }
  function syncShapeFields(prefix, kind, type) {
    const shape = SHAPE_KINDS.includes(kind);
    $$(`.${prefix}-shape`).forEach((e) => {
      const rightKind = e.classList.contains(`${prefix}-ext`) ? kind === "ext-xbar" : e.classList.contains(`${prefix}-pear`) ? kind === "pearson" : true;
      const rightSource = e.classList.contains(`${prefix}-par`) || e.classList.contains(`${prefix}-data`)
        ? (type === "parameters" ? e.classList.contains(`${prefix}-par`) : (type === "dataset" || type === "points") && e.classList.contains(`${prefix}-data`)) : true;
      e.hidden = !(shape && rightKind && rightSource);
    });
    const note = $(`#${prefix}-shape-note`);
    if (shape) note.textContent = t(kind === "pearson" ? "mon.shape_note_pearson" : "mon.shape_note_ext");
  }
  // autocorrelated values and several streams: extra settings of the limits, from the fields with the prefix "me" or "nl"
  function depSource(prefix, kind, type, base) {
    const num = (id) => { const v = $(`#${prefix}-${id}`).value.trim(); return v === "" ? null : Number(v); };
    const names = () => $(`#${prefix}-ms-names`).value.trim() ? $(`#${prefix}-ms-names`).value.split(/[,;]/).map((x) => x.trim()) : undefined;
    const src = type === "observations" ? { type } : { ...base };
    if (kind === "ar") {
      if (type === "parameters") src.phi = numbers($(`#${prefix}-phi`).value);
      else if ($(`#${prefix}-ar-order`).value) src.order = Number($(`#${prefix}-ar-order`).value);
    } else if (kind === "multistream") {
      if (names()) src.names = names();
      if (type === "parameters") {
        src.sigma_time = num("ms-sigma-time") ?? 0;
        if ($(`#${prefix}-ms-offsets`).value.trim()) src.offsets = numbers($(`#${prefix}-ms-offsets`).value);
      } else if (type === "observations") {
        src.rows = lines($(`#${prefix}-mv-rows`).value).map(numbers);
      }
    }
    return src;
  }
  function syncDepFields(prefix, kind, type) {
    const dep = DEP_KINDS.includes(kind);
    $$(`.${prefix}-dep`).forEach((e) => {
      const rightKind = e.classList.contains(`${prefix}-ar`) ? kind === "ar" : e.classList.contains(`${prefix}-ms`) ? kind === "multistream" : true;
      const types = [];
      if (e.classList.contains(`${prefix}-par`)) types.push("parameters");
      if (e.classList.contains(`${prefix}-obs`)) types.push("observations");
      if (e.classList.contains(`${prefix}-data`)) types.push("dataset", "points");
      e.hidden = !(dep && rightKind && (types.length === 0 || types.includes(type)));
    });
    if (dep) $(`#${prefix}-dep-note`).textContent = t(kind === "ar" ? "mon.dep_note_ar" : "mon.dep_note_ms");
  }
  function partsSource(text) {  // one product per line: code; target; standard deviation
    return { type: "parts", parts: lines(text).map((l) => { const tk = l.split(/[;,\t]/).map((x) => x.trim()); return { code: tk.slice(0, -2).join(","), mu: Number(tk[tk.length - 2]), sigma: Number(tk[tk.length - 1]) }; }) };
  }
  function vectorSource(type, names, mu, cov, rows, extra) {
    const src = { type, ...extra };
    if (names.trim()) src.names = names.split(/[,;]/).map((x) => x.trim());
    if (type === "parameters") { src.mu = numbers(mu); src.cov = lines(cov).map(numbers); }
    if (type === "observations") src.rows = lines(rows).map(numbers);
    return src;
  }
  const baseKind = (kind, n) => (kind === "pearson" ? (n === 1 ? "imr" : "xbar-s") : BASE_KIND[kind] || kind);
  const PRE_RULES = ["pre_red", "pre_two_yellow_same_side", "pre_two_yellow_opposite"];
  // which rules, and which sources of limits, fit a kind of monitor
  const rulesOf = (kind) => (isVec(kind) ? ["beyond_limits"] : SEQ_KINDS.includes(kind) ? SEQ_RULES : isCount(kind) || SHAPE_KINDS.includes(kind) ? COUNT_RULES : ACC_KINDS.includes(kind) ? ["beyond_limits"] : kind === "pre" ? PRE_RULES : RULES);
  const SOURCES = (kind) => (isVec(kind) || kind === "multistream" ? ["parameters", "observations"] : kind === "zmr" ? ["parts"] : isCount(kind) ? ["rate", "counts"] : kind === "pre" ? ["tolerance"] : ["parameters", "dataset"]);
  const parseList = (text) => text.split(/[\s,;]+/).filter((x) => x !== "").map(Number);
  const RULES = ["beyond_limits", "run", "trend", "middle_third", "two_of_three_beyond_2s", "four_of_five_beyond_1s", "fifteen_within_1s"];
  const ALL_RULES = RULES.concat(PRE_RULES, SEQ_RULES);
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
    const sub = isCount(m.kind) ? "mon.sub_count" : "mon.sub";
    $("#md-title").textContent = m.name;
    const msaBox = $("#md-msa");
    msaBox.hidden = !v.msa;
    if (v.msa) {
      msaBox.textContent = t("msa.monitor_banner", { name: v.msa.name || "–", status: t("msa.gate_" + v.msa.status) });
      msaBox.className = "strong " + GATE_CLASS[v.msa.status];
    }
    $("#md-sub").textContent = t(sub, { process: m.process || "–", characteristic: m.characteristic, unit: m.unit || "–", line: m.line || "–",
      chart: t("result.kind_" + m.kind), n: m.n, rev: lim.revision, by: lim.created_by, at: when(lim.created_at) });
    // the action plan must be known
    const plan = [{ rule: "default", ...m.ocap.default }].concat(Object.entries(m.ocap.rules).map(([rule, c]) => ({ rule, ...c })));
    $("#md-ack").hidden = !(m.require_ack && !v.acknowledged);
    ocapTable($("#md-plan").firstChild || $("#md-plan").appendChild(el("table", "grid")), plan);
    renderIncident();
    // entry fields follow the subgroup size
    const box = $("#md-values");
    const count = isCount(m.kind), vec = isVec(m.kind), pv = vec ? lim.p : 0;
    const fields = vec ? m.n * pv : count ? (m.kind === "p" || m.kind === "u" ? 2 : 1) : m.n;
    if (box.dataset.n !== String(fields) || box.dataset.id !== String(m.id) || box.dataset.rev !== String(lim.revision)) {
      box.replaceChildren(); box.dataset.n = String(fields); box.dataset.id = String(m.id); box.dataset.rev = String(lim.revision);
      for (let i = 0; i < fields; i++) {
        const input = el("input"); input.type = "number"; input.step = count && !(m.kind === "u" && i === 1) ? "1" : "any"; input.id = "md-v" + i;
        if (count) input.min = "0";
        const name = m.kind === "multistream" ? lim.names[i] : vec ? `${lim.names[i % pv]}${m.n > 1 ? ` (${t("mon.obs_n", { i: Math.floor(i / pv) + 1 })})` : ""}` : count ? t(i === 0 ? (m.kind === "p" || m.kind === "np" ? "mon.count_nonconforming" : "mon.count_nonconformities") : "mon.sample_size") : t("mon.value_n", { i: i + 1 });
        input.setAttribute("aria-label", name);
        input.placeholder = count || vec || m.kind === "multistream" ? name : String(i + 1);
        input.title = name;
        input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submitPoint(); } });
        box.appendChild(input);
      }
    }
    const partBox = $("#md-part-label"), partSel = $("#md-part");
    partBox.hidden = m.kind !== "zmr";
    if (m.kind === "zmr") {
      const keep = partSel.value, codes = Object.keys(lim.parts);
      partSel.replaceChildren(...codes.map((c) => { const o = el("option", "", c); o.value = c; return o; }));
      if (codes.includes(keep)) partSel.value = keep;
    }
    $("#md-entry-card").hidden = !m.active;
    $("#md-chart-var").hidden = isCount(m.kind) || m.kind === "pre" || SEQ_KINDS.includes(m.kind) || isVec(m.kind);
    $("#md-no-spec").hidden = isCount(m.kind) || isTol(m.kind) || m.kind === "zmr" || isVec(m.kind) || DEP_KINDS.includes(m.kind);
    $("#md-zmr-note").hidden = m.kind !== "zmr"; $("#md-mv-note").hidden = !isVec(m.kind);
    $("#md-dep-note").hidden = !DEP_KINDS.includes(m.kind);
    if (DEP_KINDS.includes(m.kind)) $("#md-dep-note").textContent = t(m.kind === "ar" ? "mon.dep_note_ar" : "mon.dep_note_ms");
    $("#md-seq-note").hidden = !SEQ_KINDS.includes(m.kind);
    $("#md-ongoing-box").hidden = isCount(m.kind) || m.kind === "pre" || m.kind === "zmr" || isVec(m.kind) || DEP_KINDS.includes(m.kind);
    $("#md-pre-note").hidden = m.kind !== "pre";
    const q = v.qualification;
    $("#md-qual").hidden = !q;
    if (q) { $("#md-qual").textContent = t(q.qualified ? "mon.qualified" : "mon.not_qualified", { greens: q.greens, needed: q.needed });
      $("#md-qual").className = "strong " + (q.qualified ? "status-ok" : "status-warning"); }
    renderMonitorResult();
    drawMonitorCharts();
    renderMonitorPoints();
    renderLimits();
    syncSourceFields();
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
    if (v.monitor.kind === "cusum") {  // upper and lower CUSUM on the decision interval +-h
      const rows = v.points.filter((p) => p.valid);
      const l = v.limits.location;
      return { values: rows.map((p) => p.loc), values2: rows.map((p) => p.var), labels: rows.map((p) => p.label || `#${p.seq}`),
        lcl: l.lcl, center: l.cl, ucl: l.ucl, wlcl: null, wucl: null,
        alarms: rows.map((p, i) => ({ index: i, hit: p.alarms.some((a) => a.rule === "shift_up") })).filter((a) => a.hit),
        alarms2: rows.map((p, i) => (p.alarms.some((a) => a.rule === "shift_down") ? i : -1)).filter((i) => i >= 0) };
    }
    if (isCount(v.monitor.kind) || v.monitor.kind === "ewma") {  // limits follow the sample size (counts) or the place in the run (EWMA): one band per point
      const rows = v.points.filter((p) => p.valid && p.band);
      const col = (k) => rows.map((p) => p.band[k] ?? null);
      return { values: rows.map((p) => p.loc), labels: rows.map((p) => p.label || `#${p.seq}`), lcl: col("lcl"), center: col("cl"), ucl: col("ucl"),
        wlcl: v.limits.warn_alpha ? col("wlcl") : null, wucl: v.limits.warn_alpha ? col("wucl") : null,
        alarms: rows.map((p, i) => ({ index: i, hit: p.alarms.length > 0 })).filter((a) => a.hit) };
    }
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
    const loc = monitorPart("loc"), vr = isCount(m.kind) || m.kind === "pre" || SEQ_KINDS.includes(m.kind) || isVec(m.kind) ? null : monitorPart("var");
    const draw = (sel, part, title) => { if (part.values.length) drawChart($(sel), part, title); else $(sel).replaceChildren(el("p", "muted", t("mon.no_points"))); };
    const b = m.kind === "multistream" ? "multistream" : baseKind(m.kind, m.n);
    draw("#md-chart-loc", loc, isCount(m.kind) || m.kind === "pre" || SEQ_KINDS.includes(m.kind) || isVec(m.kind) ? t("result.kind_" + m.kind)
      : `${t("result.chart_location")} – ${t(m.kind === "ar" ? "result.series_location_ar" : b === "multistream" ? "result.series_location_multistream" : b === "imr" ? "result.series_location_imr" : b === "median-r" ? "result.series_location_median" : "result.series_location_xbar")}`);
    if (vr) draw("#md-chart-var", vr, `${t("result.chart_variation")} – ${t("result.series_variation_" + b)}`);
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
    if (r.msa && r.msa.status === "conditional") div.appendChild(el("p", "status-warning", t("msa.monitor_conditional", { name: r.msa.name })));
    if (r.status === "alarm") {
      div.appendChild(el("p", "", r.point.alarms.map((a) => `${t("mon.chart_" + a.chart)}: ${t("alarmrule." + a.rule)}`).join("; ")));
      r.point.alarms.filter((a) => a.detail).forEach((a) => {  // several characteristics: which of them carries the signal
        const d = a.detail, parts = d.names.map((nm, i) => `${nm}: z = ${sig(d.z[i], 3)}${d.contribution ? `, ${t("mon.mv_share")} ${sig(d.contribution[i], 3)}` : ""}`);
        div.appendChild(el("p", "", t(M.view.monitor.kind === "multistream" ? "mon.stream_detail" : "mon.mv_detail", { list: parts.join(" · ") })));
      });
      const table = el("table", "grid"); ocapTable(table, r.instructions); div.appendChild(table);
      div.appendChild(el("p", "strong", t("mon.next_step")));
    }
    box.appendChild(div);
  }
  async function submitPoint() {
    const m = M.view.monitor;
    const values = [];
    const fields = $$("#md-values input").length;
    for (let i = 0; i < fields; i++) {
      const raw = $("#md-v" + i).value.trim();
      if (raw === "") return showError({ code: "wrong_value_count", message: "", params: { n: isCount(m.kind) ? $$("#md-values input").length : m.n } });
      values.push(Number(raw));
    }
    const body = { values, label: $("#md-label").value };
    if (m.kind === "zmr") body.part = $("#md-part").value;
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
    if (s.type === "parts") return t("mon.src_text_parts", { n: s.n_parts });
    if (s.type === "observations") return t("mon.src_text_observations", { n: s.n_observations });
    if (s.type === "parameters" && s.p !== undefined) return t("mon.src_text_vector", { p: s.p });
    if (s.type === "points" && s.n_observations !== undefined) return t("mon.src_text_points_vec", { from: s.seq_from, to: s.seq_to, n: s.n_observations });
    if (s.type === "tolerance") return t("mon.src_text_tolerance", { lsl: sig(s.lsl, 6), usl: sig(s.usl, 6) });
    if (s.type === "parameters" && s.mu === undefined) return t("mon.src_text_sigma", { sigma: sig(s.sigma, 5) });
    if (s.type === "rate") return t("mon.src_text_rate", { rate: sig(s.rate, 6) });
    if (s.type === "counts") return t("mon.src_text_counts", { n: s.n_samples });
    return t("mon.src_text_parameters", { mu: sig(s.mu, 6), sigma: sig(s.sigma, 5) });
  }
  function renderLimits() {
    const v = M.view, lim = v.limits, box = $("#md-limits");
    box.replaceChildren();
    const row = (label, o) => `${label}: ${sig(o.lcl, 6)} / ${sig(o.cl, 6)} / ${sig(o.ucl, 6)}` + (o.wlcl !== undefined ? ` (${t("mon.warning_limit")} ${sig(o.wlcl, 6)} / ${sig(o.wucl, 6)})` : "");
    if (isCount(v.monitor.kind)) {
      box.appendChild(el("p", "", t("mon.limits_now_count", { rev: lim.revision, center: sig(lim.center, 6) })));
      box.appendChild(el("p", "muted", t("mon.limits_per_size")));
      box.appendChild(el("p", "", row(t("result.kind_" + v.monitor.kind), lim.location)));
    } else if (isVec(v.monitor.kind)) {
      const kind = v.monitor.kind;
      box.appendChild(el("p", "", t(kind === "t2" ? (lim.n_ref ? "mon.limits_t2_estimated" : "mon.limits_t2") : kind === "mcusum" ? "mon.limits_mcusum_mv" : "mon.limits_mewma_mv",
        { rev: lim.revision, p: lim.p, names: lim.names.join(", "), target: lim.target.map((x) => sig(x, 6)).join(", "), n: lim.n_ref, ucl: sig(lim.location.ucl, 5),
          lam: lim.design.lambda, k: lim.design.k, arl0: sig(lim.arl0, 4) })));
      if (kind === "mewma" || kind === "mcusum") box.appendChild(el("p", "muted", t("mon.mewma_simulated_note")));
    } else if (v.monitor.kind === "ar") {
      const d = lim.diagnostics || {};
      box.appendChild(el("p", "", t("mon.limits_ar", { rev: lim.revision, order: lim.order, phi: lim.phi.map((x) => sig(x, 4)).join(", "), mu: sig(lim.process_mean, 6), sigma: sig(lim.sigma, 5) })));
      if (d.raw_lag1 !== undefined) box.appendChild(el("p", "muted", t("mon.limits_ar_diag", { lag1: sig(d.raw_lag1, 3), p: sig(d.residual_ljung_box_p, 3), raw: sig(d.sigma_raw, 4), resid: sig(lim.sigma, 4) })));
      box.appendChild(el("p", "", row(t("result.chart_location"), lim.location)));
      box.appendChild(el("p", "", row(t("result.chart_variation"), lim.variation)));
    } else if (v.monitor.kind === "multistream") {
      box.appendChild(el("p", "", t("mon.limits_ms", { rev: lim.revision, k: lim.k, mu: sig(lim.mu, 6), sw: sig(lim.sigma_w, 5), st: sig(lim.sigma_time, 5), h: sig(lim.h, 4) })));
      box.appendChild(el("p", "", row(t("mon.chart_level"), lim.location)));
      const pt = el("table", "grid"), ph = el("tr");
      ["mon.stream", "mon.stream_offset"].forEach((k) => cell(ph, t(k), "th")); pt.appendChild(ph);
      lim.names.forEach((nm, i) => { const tr = el("tr"); cell(tr, nm); cell(tr, sig(lim.offsets[i], 5)); pt.appendChild(tr); });
      const w = el("div", "scroll"); w.appendChild(pt); box.appendChild(w);
    } else if (v.monitor.kind === "zmr") {
      box.appendChild(el("p", "", t("mon.limits_zmr", { rev: lim.revision, n: Object.keys(lim.parts).length })));
      box.appendChild(el("p", "", row(t("result.chart_location"), lim.location)));
      box.appendChild(el("p", "", row(t("result.chart_variation"), lim.variation)));
      const pt = el("table", "grid"), ph = el("tr");
      ["mon.part_code", "mon.part_mu", "mon.part_sigma"].forEach((k) => cell(ph, t(k), "th")); pt.appendChild(ph);
      Object.entries(lim.parts).forEach(([code, r]) => { const tr = el("tr"); cell(tr, code); cell(tr, sig(r.mu, 6)); cell(tr, sig(r.sigma, 5)); pt.appendChild(tr); });
      const w = el("div", "scroll"); w.appendChild(pt); box.appendChild(w);
    } else if (SEQ_KINDS.includes(v.monitor.kind)) {
      const d = lim.design, kind = v.monitor.kind;
      box.appendChild(el("p", "", kind === "cusum"
        ? t("mon.limits_cusum", { rev: lim.revision, mu: sig(lim.mu, 6), sigma: sig(lim.sigma, 5), k: sig(d.k, 3), h: sig(d.h, 4), fir: sig(d.fir, 2), arl0: sig(lim.arl0, 4) })
        : t("mon.limits_ewma", { rev: lim.revision, mu: sig(lim.mu, 6), sigma: sig(lim.sigma, 5), lam: sig(d.lambda, 3), L: sig(d.L, 4), arl0: sig(lim.arl0, 4) })));
      box.appendChild(el("p", "", row(t("result.kind_" + kind), lim.location)));
      box.appendChild(el("p", "", t("mon.arl_by_shift", { table: lim.arl.map((r) => `${r.shift}: ${sig(r.arl, 3)}`).join(" · ") })));
      box.appendChild(el("p", "muted", t(kind === "cusum" ? "mon.cusum_restart_note" : "mon.ewma_limits_note")));
    } else if (v.monitor.kind === "pre") {
      box.appendChild(el("p", "", t("mon.limits_now_pre", { rev: lim.revision })));
      box.appendChild(el("p", "", row(t("result.kind_pre"), lim.location)));
    } else {
      if (lim.acceptance) {
        const a = lim.acceptance;
        box.appendChild(el("p", "", t("mon.limits_accept", { rev: lim.revision, k: sig(a.k, 5), p: sig(a.p * 100, 4), pa: sig(a.pa * 100, 4), sigma: sig(lim.sigma, 5), ratio: sig(a.sigma_over_tolerance, 3) })));
        if (!a.variation_small_enough) box.appendChild(el("p", "status-warning", t("mon.accept_variation_warning")));
      }
      if (!lim.acceptance) box.appendChild(el("p", "", t("mon.limits_now", { rev: lim.revision, mu: sig(lim.mu, 6), sigma: sig(lim.sigma, 5) })));
      box.appendChild(el("p", "", row(t("result.chart_location"), lim.location)));
      box.appendChild(el("p", "", row(t("result.chart_variation"), lim.variation)));
      if (SHAPE_KINDS.includes(v.monitor.kind) && lim.design) {
        const d = lim.design;
        box.appendChild(el("p", "muted", v.monitor.kind === "ext-xbar"
          ? t("mon.limits_ext", { method: t("mon.ext_method_" + d.method), sin: sig(d.sigma_in, 5), sout: sig(d.sigma_out, 5), uin: sig(d.u_in, 4), uout: sig(d.u_out, 3) })
          : t("mon.limits_pearson", { method: t("mon.pear_method_" + d.method), g1: sig(d.gamma1, 3), b2: sig(d.beta2, 3), s: sig(d.sigma_plot, 5), curve: d.curve || d.family })));
      }
    }
    const table = el("table", "grid"), head = el("tr");
    ["mon.rev", "mon.col_time", "mon.col_by", "mon.source", "mon.reason"].forEach((k) => cell(head, t(k), "th")); table.appendChild(head);
    v.limits_history.forEach((h) => {
      const tr = el("tr"); cell(tr, `#${h.revision}`); cell(tr, when(h.created_at)); cell(tr, h.created_by); cell(tr, sourceText(h.source)); cell(tr, h.reason); table.appendChild(tr);
    });
    const wrap = el("div", "scroll"); wrap.appendChild(table); box.appendChild(wrap);
  }
  async function setLimits() {
    const type = $("#nl-type").value, num = (id) => Number($(id).value);
    let source = type === "points" ? { type, seq_from: num("#nl-from"), seq_to: num("#nl-to") }
      : type === "tolerance" ? { type }
      : type === "parts" ? partsSource($("#nl-parts").value)
      : isVec(M.view.monitor.kind) && type !== "points" ? vectorSource(type, $("#nl-mv-names").value, $("#nl-mv-mu").value, $("#nl-mv-cov").value, $("#nl-mv-rows").value, {})
      : type === "rate" ? { type, rate: num("#nl-rate") }
      : type === "counts" ? countsSource($("#nl-counts").value, $("#nl-sizes").value)
      : type === "parameters" ? (isTol(M.view.monitor.kind) ? { type, sigma: num("#nl-sigma") } : { type, mu: num("#nl-mu"), sigma: num("#nl-sigma") }) : { type, dataset_id: $("#nl-dataset").value };
    const kind = M.view.monitor.kind;
    if (SEQ_KINDS.includes(kind) && type !== "rate") Object.assign(source, kind === "cusum" ? { k: num("#nl-k"), fir: num("#nl-fir") } : { lambda: num("#nl-lambda") });
    if (kind === "mewma") source.lambda = num("#nl-lambda");
    if (kind === "mcusum") source.k = num("#nl-k");
    if (SHAPE_KINDS.includes(kind)) Object.assign(source, shapeExtra("nl", kind, type));
    if (DEP_KINDS.includes(kind)) source = depSource("nl", kind, type, source);
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
    ["points", "parameters", "dataset", "rate", "counts"].forEach((k) => $$(".nl-" + k).forEach((e) => { e.hidden = k !== type; }));
    if (type === "dataset" && !$("#nl-dataset").options.length) fillDatasetSelect($("#nl-dataset"));
    const count = M.view && isCount(M.view.monitor.kind);
    if (M.view) {  // only the sources that fit the kind of monitor
      const ok = SOURCES(M.view.monitor.kind).concat(M.view.monitor.kind === "pre" || M.view.monitor.kind === "zmr" ? [] : ["points"]);
      Array.from($("#nl-type").options).forEach((o) => { o.hidden = o.disabled = !ok.includes(o.value); });
      const vec = isVec(M.view.monitor.kind);
      syncShapeFields("nl", M.view.monitor.kind, type);
      syncDepFields("nl", M.view.monitor.kind, type);
      $$(".nl-parameters").forEach((e) => { e.hidden = type !== "parameters" || vec; });
      $$(".nl-mv").forEach((e) => { e.hidden = !(vec && type === "parameters"); });
      $$(".nl-obs").forEach((e) => { e.hidden = !((vec || M.view.monitor.kind === "multistream") && type === "observations"); });
      $$(".nl-parts").forEach((e) => { e.hidden = type !== "parts"; });
      if (!vec) $("#nl-mu-label").hidden = type !== "parameters" || isTol(M.view.monitor.kind);
      if ($("#nl-type").selectedOptions[0] && $("#nl-type").selectedOptions[0].disabled) { $("#nl-type").value = ok[0]; return syncSourceFields(); }
      const sizes = M.view.monitor.kind === "p" || M.view.monitor.kind === "u";
      $$(".nl-seq").forEach((e) => { e.hidden = !((SEQ_KINDS.includes(M.view.monitor.kind) || M.view.monitor.kind === "mewma" || M.view.monitor.kind === "mcusum") && e.classList.contains("nl-" + M.view.monitor.kind)); });
      $$(".nl-sizes").forEach((e) => { e.hidden = type !== "counts" || !sizes; });
    }
  }
  function countsSource(counts, sizes) {
    const src = { type: "counts", counts: parseList(counts) };
    if (sizes.trim()) src.sizes = parseList(sizes);
    return src;
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
    ["default"].concat(ALL_RULES).forEach((key) => {
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
    $("#me-accept-p").value = m.specs.accept_p ? m.specs.accept_p * 100 : 1; $("#me-accept-pa").value = m.specs.accept_pa ? m.specs.accept_pa * 100 : 99;
    ["#me-lsl", "#me-usl", "#me-accept-p", "#me-accept-pa"].forEach((sel) => { $(sel).disabled = !!monitor && isTol(m.kind); });  // the tolerance belongs to the limits
    fillRules("mon-r-", m.rules, false);
    ocapEditorRows(m.ocap);
    $("#me-ack").checked = m.require_ack; $("#me-active").checked = m.active;
    syncKindFields();
    if (!monitor) await fillDatasetSelect($("#me-dataset"));
    await fillMsaSelect($("#me-msa"), m.specs.msa_id);
    showMonitorView("#mon-editor");
  }
  function syncKindFields() {
    const kind = $("#me-kind").value, count = isCount(kind);
    if (kind === "imr" || kind === "c" || kind === "acc-x") $("#me-n").value = 1;
    else if (kind === "pre") $("#me-n").value = 2;
    else if (count) { if (!($("#me-n").dataset.kind && isCount($("#me-n").dataset.kind))) $("#me-n").value = 50; }
    else if (kind === "pearson") { if ($("#me-n").dataset.kind !== "pearson") $("#me-n").value = 1; }
    else if (kind === "ar") $("#me-n").value = 1;
    else if (kind === "multistream") { if ($("#me-n").dataset.kind !== "multistream" || Number($("#me-n").value) < 2) $("#me-n").value = 4; }
    else if (Number($("#me-n").value) < 2 || Number($("#me-n").value) > (kind === "xbar-s" || kind === "acc-xbar" || kind === "ext-xbar" ? 25 : 10)) $("#me-n").value = 5;
    if ((SEQ_KINDS.includes(kind) && !SEQ_KINDS.includes($("#me-n").dataset.kind || "")) || kind === "zmr"
      || (isVec(kind) && !isVec($("#me-n").dataset.kind || ""))) $("#me-n").value = 1;  // individual values unless asked otherwise
    $("#me-n").dataset.kind = kind;
    $("#me-n-label").textContent = t(count ? (kind === "p" || kind === "u" ? "mon.f_n_typical" : "mon.f_n_size") : "mon.f_n");
    if (!$("#me-kind").disabled) $("#me-n").disabled = kind === "imr" || kind === "c" || kind === "acc-x" || kind === "pre" || kind === "zmr" || kind === "ar";
    if (kind === "multistream") $("#me-n-label").textContent = t("mon.f_streams");
    if (isVec(kind)) $("#me-n-label").textContent = t("mon.f_m");
    const tol = isTol(kind);
    $("#me-warn-label").hidden = tol || SEQ_KINDS.includes(kind) || isVec(kind) || SHAPE_KINDS.includes(kind) || kind === "multistream"; if (tol || SEQ_KINDS.includes(kind) || isVec(kind) || SHAPE_KINDS.includes(kind) || kind === "multistream") $("#me-warn").value = "";
    $("#me-accept-box").hidden = !ACC_KINDS.includes(kind);
    const seq = SEQ_KINDS.includes(kind);
    $("#me-seq-box").hidden = !(seq || kind === "mewma" || kind === "mcusum"); $("#me-k-label").hidden = !(kind === "cusum" || kind === "mcusum");
    $("#me-fir-label").hidden = kind !== "cusum"; $("#me-lambda-label").hidden = !(kind === "ewma" || kind === "mewma");
    if (kind === "mewma" && $("#me-lambda").dataset.kind !== "mewma") $("#me-lambda").value = 0.1;
    if (kind === "ewma" && $("#me-lambda").dataset.kind === "mewma") $("#me-lambda").value = 0.2;
    $("#me-lambda").dataset.kind = kind;
    if (seq) { $("#me-n").disabled = !!$("#me-kind").disabled; }
    $$(".mon-r-runtrend").forEach((e) => { e.hidden = tol || seq || isVec(kind); if (tol || seq || isVec(kind)) $("input", e).checked = false; });
    $("#me-specs-box").hidden = count || kind === "zmr" || isVec(kind) || DEP_KINDS.includes(kind);
    if (SEQ_KINDS.includes(kind) && Number($("#me-n").value) > 25) $("#me-n").value = 1;
    const vec = isVec(kind);
    const shape = SHAPE_KINDS.includes(kind);
    $$(".mon-r-normal").forEach((e) => { e.hidden = count || tol || seq || vec || shape; if (count || tol || seq || vec || shape) $("input", e).checked = false; });
    $$("#me-ocap tr[data-key]").forEach((tr) => { tr.hidden = tr.dataset.key !== "default" && !rulesOf(kind).includes(tr.dataset.key); });
    // sources that fit the kind
    const okSources = SOURCES(kind);
    Array.from($("#me-src-type").options).forEach((o) => { o.hidden = o.disabled = !okSources.includes(o.value); });
    if ($("#me-src-type").selectedOptions[0] && $("#me-src-type").selectedOptions[0].disabled) $("#me-src-type").value = okSources[0];
    syncEditorSource();
  }
  function syncEditorSource() {
    const type = $("#me-src-type").value, kind = $("#me-kind").value;
    const vec = isVec(kind);
    syncShapeFields("me", kind, type);
    syncDepFields("me", kind, type);
    $("#me-src-dataset").hidden = type !== "dataset"; $("#me-src-parameters").hidden = type !== "parameters" || vec;
    $("#me-mv-box").hidden = !(vec && type === "parameters"); $("#me-src-rows").hidden = type !== "observations";
    $("#me-src-parts").hidden = type !== "parts";
    $("#me-src-rate").hidden = type !== "rate"; $("#me-src-counts").hidden = type !== "counts";
    $("#me-src-tolerance").hidden = type !== "tolerance";
    $("#me-mu-label").hidden = isTol(kind);
    $("#me-sizes-box").hidden = !(kind === "p" || kind === "u");
  }
  function readMonitorEditor() {
    const num = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
    const editing = M.editing !== "new" ? M.view.monitor : null;
    const warn = editing ? editing.warn_alpha : isTol($("#me-kind").value) || SEQ_KINDS.includes($("#me-kind").value) || isVec($("#me-kind").value) || SHAPE_KINDS.includes($("#me-kind").value) || $("#me-kind").value === "multistream" ? null : num("#me-warn");
    const config = {
      name: $("#me-name").value, process: $("#me-process").value, characteristic: $("#me-characteristic").value, unit: $("#me-unit").value, line: $("#me-line").value,
      kind: editing ? editing.kind : $("#me-kind").value, n: editing ? editing.n : Number($("#me-n").value), warn_alpha: warn,
      specs: isCount($("#me-kind").value) || (editing && isCount(editing.kind)) || $("#me-kind").value === "zmr" || isVec($("#me-kind").value) || DEP_KINDS.includes($("#me-kind").value) ? {} : { ...(ACC_KINDS.includes($("#me-kind").value) ? (editing ? { accept_p: editing.specs.accept_p, accept_pa: editing.specs.accept_pa } : { accept_p: num("#me-accept-p") / 100, accept_pa: num("#me-accept-pa") / 100 }) : {}), lsl: num("#me-lsl"), usl: num("#me-usl"), target_class: $("#me-class").value || null, model: $("#me-model").value || null,
        controlled_stable: editing ? editing.specs.controlled_stable : false, edition: editing ? editing.specs.edition : "draft" },
      rules: readRules("mon-r-"), ocap: readOcap(), require_ack: $("#me-ack").checked, active: $("#me-active").checked,
    };
    if (editing) config.alpha = editing.alpha; else if ($("#me-alpha").value) config.alpha = Number($("#me-alpha").value);
    config.specs = { ...config.specs, msa_id: $("#me-msa").value ? Number($("#me-msa").value) : null };
    return config;
  }
  async function saveMonitor() {
    await guarded(async () => {
      const config = readMonitorEditor();
      if (M.editing === "new") {
        const type = $("#me-src-type").value;
        let source = type === "parameters" && !isVec(config.kind) ? (isTol(config.kind) ? { type, sigma: Number($("#me-sigma").value) } : { type, mu: Number($("#me-mu").value), sigma: Number($("#me-sigma").value) })
          : type === "tolerance" ? { type }
          : type === "parts" ? partsSource($("#me-parts").value)
          : isVec(config.kind) ? vectorSource(type, $("#me-mv-names").value, $("#me-mv-mu").value, $("#me-mv-cov").value, $("#me-mv-rows").value, config.kind === "mewma" ? { lambda: Number($("#me-lambda").value) } : config.kind === "mcusum" ? { k: Number($("#me-k").value) } : {})
          : type === "rate" ? { type, rate: Number($("#me-rate").value) }
          : type === "counts" ? countsSource($("#me-counts").value, $("#me-sizes").value) : { type, dataset_id: $("#me-dataset").value };
        if (SEQ_KINDS.includes(config.kind) && type === "parameters" || SEQ_KINDS.includes(config.kind) && type === "dataset") {
          Object.assign(source, config.kind === "cusum" ? { k: Number($("#me-k").value), fir: Number($("#me-fir").value) } : { lambda: Number($("#me-lambda").value) });
        }
        if (SHAPE_KINDS.includes(config.kind) && (type === "parameters" || type === "dataset")) Object.assign(source, shapeExtra("me", config.kind, type));
        if (DEP_KINDS.includes(config.kind)) source = depSource("me", config.kind, type, source);
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
    $("#me-src-type").addEventListener("change", syncEditorSource);
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

  // ---------------------------------------------------------------- measurement systems and the MSA gate
  const MS = { view: null, editing: null, list: [] };
  const showMsaView = (which) => { ["#ms-list-view", "#ms-editor", "#ms-detail"].forEach((sel) => { $(sel).hidden = sel !== which; }); };
  const GATE_CLASS = { pass: "status-ok", conditional: "status-warning", block: "status-alarm" };
  const CHECK_CLASS = { pass: "status-ok", waived: "status-warning", warn: "status-warning", fail: "status-alarm", missing: "status-alarm", not_done: "", not_needed: "" };
  const POLICY_PCT = ["resolution_share_max", "guard_band_risk"];  // shown in percent
  async function fillReportPlanSelect() {  // only a released plan can stand in a report
    const select = $("#rp-plan"), current = select.value;
    let list = [];
    try { list = (await api("/api/plans")).plans.filter((p) => p.status === "released"); } catch (e) { /* none */ }
    select.replaceChildren();
    const none = el("option", "", t("plan.link_none")); none.value = ""; select.appendChild(none);
    list.forEach((p) => { const o = el("option", "", `${p.name} (${t("plan.state_released", { rev: p.revision })})`); o.value = String(p.id); select.appendChild(o); });
    select.value = list.some((p) => String(p.id) === current) ? current : "";
  }
  async function fillMsaSelect(select, current) {
    let list = [];
    try { list = (await api("/api/msa")).systems; } catch (e) { /* none */ }
    select.replaceChildren();
    const none = el("option", "", t("msa.link_none")); none.value = ""; select.appendChild(none);
    list.forEach((s) => { const o = el("option", "", `${s.name} (${t("msa.gate_" + s.status)})`); o.value = String(s.id); select.appendChild(o); });
    select.value = current ? String(current) : "";
  }
  async function loadMsa() {
    if (MS.view && !$("#ms-detail").hidden) return;
    await guarded(async () => { MS.list = (await api("/api/msa")).systems; });
    renderMsaList(); showMsaView("#ms-list-view");
  }
  function renderMsaList() {
    const table = $("#ms-list"); table.replaceChildren();
    const head = el("tr");
    ["msa.f_name", "msa.f_characteristic", "msa.col_gate", "msa.col_u", ""].forEach((k) => cell(head, k ? t(k) : "", "th"));
    table.appendChild(head);
    MS.list.forEach((s) => {
      const tr = el("tr");
      cell(tr, s.name); cell(tr, s.characteristic);
      const g = cell(tr, t("msa.gate_" + s.status) + (s.blocking.length ? ` (${s.blocking.map((k) => t("msa.check_" + k)).join(", ")})` : "")); g.className = GATE_CLASS[s.status];
      cell(tr, s.U === null || s.U === undefined ? "–" : sig(s.U, 4));
      const open = el("button", "", t("mon.open")); open.addEventListener("click", () => openMsa(s.id)); cell(tr, "").appendChild(open);
      table.appendChild(tr);
    });
    if (!MS.list.length) table.appendChild(el("tr")).appendChild(el("td", "muted", t("msa.none")));
  }
  async function openMsa(id) {
    await guarded(async () => { MS.view = await api(`/api/msa/${id}`); });
    if (MS.view) { renderMsa(); showMsaView("#ms-detail"); }
  }
  function checkText(key, c) {
    const p = { ...c };
    if (c.share !== undefined) { p.share = sig(c.share * 100, 3); p.limit = sig(c.limit * 100, 3); }
    if (c.pct !== undefined) p.pct = sig(c.pct, 3), p.ndc = sig(c.ndc, 3), p.basis = t("msa.basis_" + c.basis);
    if (c.cg !== undefined) p.cg = sig(c.cg, 3), p.cgk = sig(c.cgk, 3);
    const reason = c.reason ? `.${c.reason}` : "";
    const k = `msa.res.${key}.${c.result}${reason}`;
    return k in state.msgs || k in state.fallback ? t(k, p) : t(`msa.res.${key}.${c.result}`, p);
  }
  function renderMsa() {
    const { system: s, gate: g } = MS.view;
    $("#ms-title").textContent = s.name;
    $("#ms-sub").textContent = t("msa.sub", { characteristic: s.characteristic || "–", unit: s.unit || "–", resolution: s.resolution ?? "–", tolerance: s.tolerance ?? "–", rev: s.revision });
    $("#ms-status").textContent = t("msa.status_" + g.status); $("#ms-status").className = "strong " + GATE_CLASS[g.status];
    const edit = canEditStudy();
    const table = $("#ms-checks"); table.replaceChildren();
    const head = el("tr"); ["msa.col_check", "msa.col_result", "msa.col_waiver"].forEach((k) => cell(head, t(k), "th")); table.appendChild(head);
    Object.entries(g.checks).forEach(([key, c]) => {
      const tr = el("tr");
      cell(tr, t("msa.check_" + key));
      const r = cell(tr, `${t("msa.eff_" + c.effective)}: ${checkText(key, c)}`); r.className = CHECK_CLASS[c.effective] || "";
      const w = cell(tr, "");
      if (c.waiver) {
        w.appendChild(el("span", "", `${c.waiver.reason} (${c.waiver.by}, ${when(c.waiver.at)}) `));
        if (edit) { const b = el("button", "", t("msa.remove_waiver")); b.addEventListener("click", () => msaWaiver(key, null)); w.appendChild(b); }
      } else if (edit && ["warn", "fail", "missing"].includes(c.result)) {
        const b = el("button", "", t("msa.waive"));
        b.addEventListener("click", () => { const reason = window.prompt(t("msa.waive_prompt", { check: t("msa.check_" + key) })); if (reason) msaWaiver(key, reason); });
        w.appendChild(b);
      }
      table.appendChild(tr);
    });
    const u = $("#ms-uncertainty"); u.replaceChildren();
    if (g.uncertainty) {
      const x = g.uncertainty;
      u.appendChild(el("p", "", t("msa.uncertainty", { U: sig(x.U, 4), k: x.k, u: sig(x.u, 4), g: sig(x.guard_band, 4), risk: sig(x.guard_risk * 100, 3), study: x.from_study })));
      u.appendChild(el("p", "muted", t("msa.uncertainty_note")));
    }
    const st = $("#ms-studies"); st.replaceChildren();
    const sh = el("tr"); ["msa.col_no", "msa.s_kind", "msa.s_date", "msa.col_by", "msa.col_verdict", "msa.col_summary", ""].forEach((k) => cell(sh, k ? t(k) : "", "th")); st.appendChild(sh);
    s.studies.slice().reverse().forEach((x) => {
      const tr = el("tr", x.voided ? "invalid-point" : "");
      cell(tr, String(x.id)); cell(tr, t("msa.kind_" + x.kind)); cell(tr, x.date); cell(tr, x.by);
      const v = cell(tr, x.voided ? t("msa.voided") : t("msa.verdict_" + x.verdict)); v.className = x.voided ? "" : { pass: "status-ok", conditional: "status-warning", fail: "status-alarm" }[x.verdict] || "";
      if (x.voided) v.title = `${x.voided.reason} (${x.voided.by || ""})`;
      cell(tr, x.result ? studySummary(x) : x.error || "");
      const a = cell(tr, "");
      if (edit && !x.voided) { const b = el("button", "", t("msa.void")); b.addEventListener("click", () => { const reason = window.prompt(t("msa.void_prompt", { id: x.id })); if (reason) msaVoid(x.id, reason); }); a.appendChild(b); }
      st.appendChild(tr);
    });
    $("#ms-add-card").hidden = !edit;
    $("#ms-edit").hidden = !edit;
    syncStudyForm();
  }
  function studySummary(x) {
    const r = x.result;
    if (x.kind === "grr") return t("msa.sum_grr", { pct: sig(r.pct_tol ?? r.pct_tv, 3), basis: t("msa.basis_" + r.basis), ndc: sig(r.ndc, 3), ev: sig(r.sigma.ev, 3), av: sig(r.sigma.av, 3), n: `${r.parts}×${r.operators}×${r.trials}` });
    if (x.kind === "type1") return t("msa.sum_type1", { cg: sig(r.cg, 3), cgk: sig(r.cgk, 3), bias: sig(r.bias, 3), n: r.n });
    return t("msa.sum_stability", { n: r.n, signals: r.n_signals });
  }
  function syncStudyForm() {
    const kind = $("#mss-kind").value;
    $("#mss-ref-label").hidden = kind !== "type1";
    $("#mss-data-label").textContent = t("msa.data_" + kind);
    if (!$("#mss-date").value) $("#mss-date").value = new Date().toISOString().slice(0, 10);
  }
  function readStudyInput() {
    const kind = $("#mss-kind").value, text = $("#mss-data").value;
    if (kind === "grr") return { data: lines(text).map((row) => row.split(";").map(numbers)) };
    if (kind === "type1") return { reference: Number($("#mss-reference").value), values: numbers(text) };
    return { values: numbers(text) };
  }
  async function addMsaStudy() {
    await guarded(async () => {
      MS.view = await post(`/api/msa/${MS.view.system.id}/studies`, { kind: $("#mss-kind").value, date: $("#mss-date").value, note: $("#mss-note").value, input: readStudyInput() });
      $("#mss-data").value = ""; $("#mss-note").value = "";
    });
    renderMsa();
  }
  async function msaWaiver(check, reason) {
    await guarded(async () => {
      const path = `/api/msa/${MS.view.system.id}/waivers/${check}`;
      MS.view = reason === null ? await api(path, { method: "DELETE" }) : await put(path, { reason });
    });
    renderMsa();
  }
  async function msaVoid(id, reason) {
    await guarded(async () => { MS.view = await post(`/api/msa/${MS.view.system.id}/studies/${id}/void`, { reason }); });
    renderMsa();
  }
  function openMsaEditor(view) {
    MS.editing = view ? view.system.id : "new";
    const s = view ? view.system : { name: "", description: "", characteristic: "", unit: "", resolution: null, tolerance: null, policy: DEFAULT_MSA_POLICY };
    $("#ms-editor-title").textContent = view ? t("msa.edit_title", { name: s.name }) : t("msa.new");
    ["name", "description", "characteristic", "unit"].forEach((k) => { $("#mse-" + k).value = s[k] || ""; });
    $("#mse-resolution").value = s.resolution ?? ""; $("#mse-tolerance").value = s.tolerance ?? "";
    Object.entries(s.policy).forEach(([k, v]) => {
      const e = $("#msp-" + k);
      if (!e) return;
      if (e.type === "checkbox") e.checked = !!v; else e.value = POLICY_PCT.includes(k) ? v * 100 : v;
    });
    showMsaView("#ms-editor");
  }
  const DEFAULT_MSA_POLICY = { validity_months: 12, stability_months: 6, resolution_share_max: 0.05, grr_pass: 10, grr_conditional: 30, ndc_min: 5, cg_min: 1.33, require_stability: true, k: 2, guard_band_risk: 0.05, u_cal: 0 };
  function readMsaEditor() {
    const num = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
    const policy = {};
    Object.keys(DEFAULT_MSA_POLICY).forEach((k) => {
      const e = $("#msp-" + k);
      policy[k] = e.type === "checkbox" ? e.checked : POLICY_PCT.includes(k) ? Number(e.value) / 100 : Number(e.value);
    });
    return { name: $("#mse-name").value, description: $("#mse-description").value, characteristic: $("#mse-characteristic").value, unit: $("#mse-unit").value,
      resolution: num("#mse-resolution"), tolerance: num("#mse-tolerance"), policy };
  }
  async function saveMsa() {
    await guarded(async () => {
      const record = readMsaEditor();
      MS.view = MS.editing === "new" ? await post("/api/msa", { record }) : await put(`/api/msa/${MS.editing}`, { record });
      MS.saved = true;
    });
    if (MS.saved) { MS.saved = false; renderMsa(); showMsaView("#ms-detail"); }
  }
  function wireMsa() {
    $("#ms-new").addEventListener("click", () => openMsaEditor(null));
    $("#mse-save").addEventListener("click", saveMsa);
    $("#mse-cancel").addEventListener("click", () => { if (MS.editing !== "new" && MS.view) showMsaView("#ms-detail"); else { MS.view = null; loadMsa(); } });
    $("#ms-back").addEventListener("click", () => { MS.view = null; loadMsa(); });
    $("#ms-edit").addEventListener("click", () => openMsaEditor(MS.view));
    $("#mss-kind").addEventListener("change", syncStudyForm);
    $("#mss-save").addEventListener("click", addMsaStudy);
  }

  // ---------------------------------------------------------------- equipment interface, OPC UA (draft 11.1)
  const EQ = { list: [], view: null, editing: null };
  const showEqView = (which) => { ["#eq-list-view", "#eq-editor", "#eq-detail"].forEach((sel) => { $(sel).hidden = sel !== which; }); };
  const RUNTIME_CLASS = { connected: "status-ok", connecting: "status-warning", error: "status-alarm", runner_stopped: "status-alarm", disabled: "" };
  async function loadEquipment() {
    if (EQ.view && !$("#eq-detail").hidden) return;
    await guarded(async () => { const r = await api("/api/equipment/links"); EQ.list = r.links; EQ.runner = r.runner; });
    renderEqList(); showEqView("#eq-list-view");
  }
  function renderEqList() {
    $("#eq-runner").textContent = t(EQ.runner ? "eq.runner_on" : "eq.runner_off");
    const table = $("#eq-list"); table.replaceChildren();
    const head = el("tr"); ["eq.f_name", "eq.f_endpoint", "eq.col_nodes", "eq.col_state", "eq.col_readings", ""].forEach((k) => cell(head, k ? t(k) : "", "th")); table.appendChild(head);
    EQ.list.forEach((l) => {
      const tr = el("tr"); cell(tr, l.name); cell(tr, l.endpoint); cell(tr, String(l.nodes));
      const s = cell(tr, t("eq.rt_" + l.runtime) + (l.tested ? "" : ` · ${t("eq.not_tested")}`)); s.className = RUNTIME_CLASS[l.runtime] || "";
      cell(tr, t("eq.readings", { a: l.accepted, p: l.points, r: l.rejected }));
      const b = el("button", "", t("mon.open")); b.addEventListener("click", () => openEq(l.id)); cell(tr, "").appendChild(b);
      table.appendChild(tr);
    });
    if (!EQ.list.length) table.appendChild(el("tr")).appendChild(el("td", "muted", t("eq.none")));
  }
  async function openEq(id) {
    await guarded(async () => { EQ.view = await api(`/api/equipment/links/${id}`); });
    if (EQ.view) { renderEq(); showEqView("#eq-detail"); }
  }
  function renderEq() {
    const { link, runtime, counters } = EQ.view;
    $("#eq-title").textContent = link.name;
    $("#eq-sub").textContent = t("eq.sub", { endpoint: link.endpoint, rev: link.revision, interval: link.interval_ms });
    const st = $("#eq-state");
    st.textContent = t("eq.rt_" + runtime.state) + (runtime.message ? `: ${runtime.message}` : "");
    st.className = "strong " + (RUNTIME_CLASS[runtime.state] || "");
    $("#eq-enable").textContent = t(link.enabled ? "eq.disable" : "eq.enable");
    const tt = $("#eq-test-table"); tt.replaceChildren();
    const lt = link.last_test;
    if (!lt || lt.revision !== link.revision) tt.appendChild(el("tr")).appendChild(el("td", "muted", t("eq.not_tested")));
    else {
      const head = el("tr"); ["eq.col_node", "eq.col_value", "eq.col_type", "eq.col_quality", "eq.col_time", "eq.col_result"].forEach((k) => cell(head, t(k), "th")); tt.appendChild(head);
      lt.nodes.forEach((n) => {
        const tr = el("tr"); cell(tr, n.node_id); cell(tr, n.value === null ? "–" : String(n.value)); cell(tr, n.type || "–"); cell(tr, n.quality || "–"); cell(tr, n.source_time ? when(n.source_time) : "–");
        cell(tr, n.ok ? t("eq.ok") : t("eq.failed", { why: n.error || "" })).className = n.ok ? "status-ok" : "status-alarm"; tt.appendChild(tr);
      });
      const tr = el("tr"); const td = el("td", lt.ok ? "status-ok" : "status-alarm", lt.error ? `${t("eq.connect_failed")}: ${lt.error}` : t("eq.tested_by", { at: when(lt.at), by: lt.by, rev: lt.revision })); td.colSpan = 6; tr.appendChild(td); tt.appendChild(tr);
    }
    const ct = $("#eq-counters"); ct.replaceChildren();
    const ch = el("tr"); ["eq.col_node", "eq.col_accepted", "eq.col_points", "eq.col_buffered", "eq.col_rejected", "eq.col_last"].forEach((k) => cell(ch, t(k), "th")); ct.appendChild(ch);
    link.nodes.forEach((n) => {
      const c = counters[n.node_id] || {};
      const tr = el("tr"); cell(tr, `${n.node_id} → #${n.monitor_id}`); cell(tr, String(c.accepted || 0)); cell(tr, String(c.points || 0)); cell(tr, String(c.buffered || 0));
      cell(tr, Object.entries(c.rejected || {}).map(([k, v]) => `${t("eq.reason_" + k)}: ${v}`).join(", ") || "–");
      cell(tr, c.last_error ? `${when(c.last_error.at)} ${t("eq.reason_" + c.last_error.reason)}` : c.last_time ? when(c.last_time) : "–"); ct.appendChild(tr);
    });
  }
  function openEqEditor(view) {
    EQ.editing = view ? view.link.id : "new";
    const l = view ? view.link : { name: "", endpoint: "", description: "", security: "none", username: "", password_env: "", interval_ms: 1000, stale_after_s: 0, nodes: [] };
    $("#eq-editor-title").textContent = view ? t("eq.edit_title", { name: l.name }) : t("eq.new");
    $("#eqe-name").value = l.name; $("#eqe-endpoint").value = l.endpoint; $("#eqe-description").value = l.description; $("#eqe-security").value = l.security;
    $("#eqe-username").value = l.username; $("#eqe-password_env").value = l.password_env; $("#eqe-interval").value = l.interval_ms; $("#eqe-stale").value = l.stale_after_s;
    $("#eqe-nodes").value = l.nodes.map((n) => `${n.node_id} ${n.monitor_id} ${n.scale ?? 1} ${n.offset ?? 0}`).join("\n");
    showEqView("#eq-editor");
  }
  function readEqEditor() {
    const nodes = $("#eqe-nodes").value.split("\n").map((x) => x.trim()).filter(Boolean).map((x) => {
      const [node_id, monitor_id, scale, offset] = x.split(/\s+/);
      return { node_id, monitor_id: Number(monitor_id), ...(scale !== undefined ? { scale: Number(scale) } : {}), ...(offset !== undefined ? { offset: Number(offset) } : {}) };
    });
    return { name: $("#eqe-name").value, endpoint: $("#eqe-endpoint").value, description: $("#eqe-description").value, security: $("#eqe-security").value || "none",
      username: $("#eqe-username").value, password_env: $("#eqe-password_env").value, interval_ms: Number($("#eqe-interval").value), stale_after_s: Number($("#eqe-stale").value), nodes };
  }
  async function saveEq() {
    await guarded(async () => {
      const record = readEqEditor();
      EQ.view = EQ.editing === "new" ? await post("/api/equipment/links", { record }) : await put(`/api/equipment/links/${EQ.editing}`, { record });
      EQ.saved = true;
    });
    if (EQ.saved) { EQ.saved = false; renderEq(); showEqView("#eq-detail"); }
  }
  async function eqAction(action) {
    await guarded(async () => { EQ.view = await post(`/api/equipment/links/${EQ.view.link.id}/${action}`, {}); });
    renderEq();
  }
  function wireEquipment() {
    $("#eq-new").addEventListener("click", () => openEqEditor(null));
    $("#eq-edit").addEventListener("click", () => openEqEditor(EQ.view));
    $("#eq-back").addEventListener("click", () => { EQ.view = null; loadEquipment(); });
    $("#eqe-save").addEventListener("click", saveEq);
    $("#eqe-cancel").addEventListener("click", () => { if (EQ.editing !== "new" && EQ.view) showEqView("#eq-detail"); else { EQ.view = null; loadEquipment(); } });
    $("#eq-test").addEventListener("click", () => eqAction("test"));
    $("#eq-enable").addEventListener("click", () => eqAction(EQ.view.link.enabled ? "disable" : "enable"));
  }

  // ---------------------------------------------------------------- verification and validation of the software (draft 11.2)
  const VA = { cases: [], editing: null };
  async function loadValidation() {
    await guarded(async () => { VA.cases = (await api("/api/validation/cases")).cases; VA.runs = (await api("/api/validation/runs")).runs; VA.iso = (await api("/api/validation/iso11462")).examples; });
    renderValidation();
  }
  function renderValidation() {
    const runs = $("#va-runs"); runs.replaceChildren();
    const head = el("tr"); ["val.col_no", "val.col_at", "val.col_by", "val.col_engine", "val.col_verdict", "val.col_cases", "val.col_digest", ""].forEach((k) => cell(head, k ? t(k) : "", "th")); runs.appendChild(head);
    (VA.runs || []).forEach((r) => {
      const tr = el("tr"); cell(tr, String(r.id)); cell(tr, when(r.created_at)); cell(tr, r.created_by); cell(tr, r.engine_version);
      const v = cell(tr, t("val.verdict_" + r.verdict, { n: r.failed })); v.className = r.verdict === "fail" ? "status-alarm" : r.verdict === "pass" ? "status-ok" : "status-warning";
      cell(tr, String(r.cases)); cell(tr, r.digest.slice(0, 12)).title = r.digest;
      const a = cell(tr, "");
      ["en", "zh-TW"].forEach((lang) => { const l = el("a", "", t("val.report_" + lang)); l.href = `/api/validation/runs/${r.id}/report?lang=${lang}`; l.target = "_blank"; l.rel = "noopener"; a.appendChild(l); a.appendChild(document.createTextNode(" ")); });
      runs.appendChild(tr);
    });
    if (!(VA.runs || []).length) runs.appendChild(el("tr")).appendChild(el("td", "muted", t("val.no_runs")));
    const iso = $("#va-iso"); iso.replaceChildren();
    const ih = el("tr"); ["val.iso_no", "val.iso_what", "val.iso_state"].forEach((k) => cell(ih, t(k), "th")); iso.appendChild(ih);
    (VA.iso || []).forEach((e) => {
      const tr = el("tr"); cell(tr, String(e.number));
      cell(tr, t("val.iso_desc", { model: e.model, dist: e.distribution, n: e.n, size: e.subgroup_size ?? "–", lsl: e.lsl, usl: e.usl }));
      const l = e.last;
      const c = cell(tr, l ? t("val.iso_last", { pass: l.pass || 0, known: l.known || 0, info: l.info || 0, fail: l.fail || 0 }) : t("val.iso_not_run"));
      c.className = !l ? "status-warning" : l.fail ? "status-alarm" : "status-ok";
      iso.appendChild(tr);
    });
    const cases = $("#va-cases"); cases.replaceChildren();
    const ch = el("tr"); ["val.f_name", "val.f_source", "val.col_values", "val.col_expected", ""].forEach((k) => cell(ch, k ? t(k) : "", "th")); cases.appendChild(ch);
    VA.cases.forEach((c) => {
      const tr = el("tr"); cell(tr, c.name); cell(tr, c.source); cell(tr, String(c.n_values)); cell(tr, String(c.expected.length));
      const a = cell(tr, "");
      const edit = el("button", "", t("plan.edit")); edit.addEventListener("click", () => openCaseEditor(c.id)); a.appendChild(edit);
      cases.appendChild(tr);
    });
    if (!VA.cases.length) cases.appendChild(el("tr")).appendChild(el("td", "muted", t("val.no_cases")));
  }
  async function openCaseEditor(id) {
    let c = { name: "", description: "", source: "", values: [], subgroup_size: null, request: {}, expected: [] };
    if (id) await guarded(async () => { c = await api(`/api/validation/cases/${id}`); });
    VA.editing = id || "new";
    $("#va-editor-title").textContent = id ? t("val.edit_title", { name: c.name }) : t("val.new");
    $("#vae-name").value = c.name; $("#vae-description").value = c.description; $("#vae-source").value = c.source;
    $("#vae-subgroup").value = c.subgroup_size ?? "";
    const { stage, lsl, usl, ...rest } = c.request;
    $("#vae-stage").value = stage || "production"; $("#vae-lsl").value = lsl ?? ""; $("#vae-usl").value = usl ?? "";
    $("#vae-settings").value = Object.keys(rest).length ? JSON.stringify(rest) : "";
    $("#vae-values").value = c.values.join("\n");
    $("#vae-expected").value = c.expected.map((e) => `${e.path} ${e.value} ${e.tol}`).join("\n");
    $("#va-editor").hidden = false;
  }
  function readCaseEditor() {
    const num = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
    let extra = {};
    const raw = $("#vae-settings").value.trim();
    if (raw) { try { extra = JSON.parse(raw); } catch (e) { throw { code: "invalid_input", message: t("val.bad_settings"), params: { message: t("val.bad_settings") } }; } }
    const request = { ...extra, stage: $("#vae-stage").value };
    if (num("#vae-lsl") !== null) request.lsl = num("#vae-lsl");
    if (num("#vae-usl") !== null) request.usl = num("#vae-usl");
    const values = $("#vae-values").value.split(/[\s;,]+/).filter(Boolean).map(Number);
    const expected = $("#vae-expected").value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
      const [path, value, tol] = l.split(/\s+/);
      return { path, value: Number(value), ...(tol !== undefined ? { tol: Number(tol) } : {}) };
    });
    return { name: $("#vae-name").value, description: $("#vae-description").value, source: $("#vae-source").value, values, subgroup_size: num("#vae-subgroup"), request, expected };
  }
  async function saveCase() {
    await guarded(async () => {
      const record = readCaseEditor();
      if (VA.editing === "new") await post("/api/validation/cases", { record }); else await put(`/api/validation/cases/${VA.editing}`, { record });
      $("#va-editor").hidden = true;
      VA.cases = (await api("/api/validation/cases")).cases;
      VA.iso = (await api("/api/validation/iso11462")).examples;
    });
    renderValidation();
  }
  async function runValidation() {
    await guarded(async () => { await post("/api/validation/runs", {}); VA.runs = (await api("/api/validation/runs")).runs; VA.iso = (await api("/api/validation/iso11462")).examples; });
    renderValidation();
  }
  function wireValidation() {
    $("#va-run").addEventListener("click", runValidation);
    $("#va-new").addEventListener("click", () => openCaseEditor(null));
    $("#vae-save").addEventListener("click", saveCase);
    $("#vae-cancel").addEventListener("click", () => { $("#va-editor").hidden = true; });
  }

  // ---------------------------------------------------------------- control plan and SPC roles (draft 6.7, 6.8)
  const PL = { list: [], view: null, editing: null, lines: [], lineIndex: null, roles: null, person: null, people: [] };
  const showPlanView = (which) => { ["#pl-list-view", "#pl-editor", "#pl-detail"].forEach((sel) => { $(sel).hidden = sel !== which; }); };
  const RESULT_CLASS = { pass: "status-ok", warn: "status-warning", fail: "status-alarm" };
  const LINE_FIELDS = ["step", "characteristic", "unit", "method", "frequency", "reaction", "note"];
  const rolesList = () => (PL.roles ? PL.roles.roles : []);

  async function loadPlans() {
    if (PL.view && !$("#pl-detail").hidden) return;
    await guarded(async () => { PL.list = (await api("/api/plans")).plans; if (!PL.roles) PL.roles = await api("/api/spc-roles"); });
    renderPlanList(); showPlanView("#pl-list-view");
  }
  function renderPlanList() {
    const table = $("#pl-list"); table.replaceChildren();
    const head = el("tr");
    ["plan.f_name", "plan.f_part", "plan.f_process", "plan.f_phase", "plan.col_state", ""].forEach((k) => cell(head, k ? t(k) : "", "th"));
    table.appendChild(head);
    PL.list.forEach((p) => {
      const tr = el("tr");
      cell(tr, p.name); cell(tr, p.part); cell(tr, p.process); cell(tr, t("plan.phase_" + p.phase));
      const text = p.status === "released" ? t("plan.state_released", { rev: p.revision })
        : p.ready ? t("plan.state_ready") : t("plan.state_draft", { n: p.blockers, a: p.approvals.length });
      cell(tr, text).className = p.status === "released" ? "status-ok" : "status-warning";
      const open = el("button", "", t("mon.open")); open.addEventListener("click", () => openPlan(p.id)); cell(tr, "").appendChild(open);
      table.appendChild(tr);
    });
    if (!PL.list.length) table.appendChild(el("tr")).appendChild(el("td", "muted", t("plan.none")));
  }
  async function openPlan(id) {
    await guarded(async () => { PL.view = await api(`/api/plans/${id}`); });
    if (PL.view) { renderPlan(); showPlanView("#pl-detail"); }
  }
  function planCheckText(line, c) {
    const p = { ...c, name: c.name || "", missing: (c.missing || []).map((f) => t("plan.l_" + f)).join(", "),
      share: c.share !== undefined ? sig(c.share * 100, 3) : "", U: c.U !== undefined ? sig(c.U, 4) : "", guard_band: c.guard_band !== undefined ? sig(c.guard_band, 4) : "",
      tolerance: c.tolerance !== undefined ? sig(c.tolerance, 4) : "", system: c.system !== undefined ? sig(c.system, 4) : "", line: c.line !== undefined ? sig(c.line, 4) : "",
      status: c.status ? t("msa.gate_" + c.status) : "" };
    let key = `plan.chk.${c.key}.${c.result}`;
    if (c.reason) key += "." + c.reason;
    if (c.key === "reaction" && c.from_monitor) key += ".monitor";
    return t(key, p);
  }
  function renderPlan() {
    const { plan, evaluation: ev } = PL.view;
    $("#pl-title").textContent = plan.name;
    $("#pl-sub").textContent = t("plan.sub", { part: plan.part || "–", process: plan.process || "–", phase: t("plan.phase_" + plan.phase), rev: plan.content_revision });
    const v = $("#pl-verdict");
    v.textContent = plan.status === "released" ? t("plan.verdict_released", { rev: plan.released_revision }) : ev.ready ? t("plan.verdict_ready") : t("plan.verdict_blocked", { n: ev.blockers.length });
    v.className = "strong " + (plan.status === "released" || ev.ready ? "status-ok" : "status-warning");
    const table = $("#pl-lines"); table.replaceChildren();
    const head = el("tr");
    ["plan.col_no", "plan.l_step", "plan.l_characteristic", "plan.col_spec", "plan.l_control", "plan.col_sampling", "plan.l_responsible", "plan.col_checks"].forEach((k) => cell(head, t(k), "th"));
    table.appendChild(head);
    plan.lines.forEach((l, i) => {
      const tr = el("tr");
      cell(tr, String(i + 1)); cell(tr, l.step); cell(tr, l.characteristic + (l.unit ? ` [${l.unit}]` : "") + (l.class ? ` (${t("analysis.class_" + l.class)})` : ""));
      cell(tr, [l.lsl !== null ? `${t("analysis.lsl")} ${l.lsl}` : "", l.target !== null ? `${t("plan.l_target")} ${l.target}` : "", l.usl !== null ? `${t("analysis.usl")} ${l.usl}` : ""].filter(Boolean).join(", ") || "–");
      cell(tr, t("plan.control_" + l.control));
      cell(tr, [l.sample_size ? `n=${l.sample_size}` : "", l.frequency].filter(Boolean).join(", ") || "–");
      cell(tr, l.responsible.map((r) => t("roles.role_" + r)).join(", ") || "–");
      const c = cell(tr, ""); c.className = "checks-cell";
      (ev.lines[String(i + 1)] || []).forEach((x) => { const d = el("div", RESULT_CLASS[x.result], `${t("plan.chk." + x.key)}: ${planCheckText(l, x)}`); c.appendChild(d); });
      table.appendChild(tr);
    });
    if (!plan.lines.length) table.appendChild(el("tr")).appendChild(el("td", "muted", t("plan.no_lines")));
    ev.plan.filter((c) => c.key === "staffing" && c.result === "warn").forEach((c) => {
      const tr = el("tr"); const td = el("td", "status-warning", t("plan.chk.staffing.warn", { roles: c.unstaffed.map((r) => t("roles.role_" + r)).join(", ") })); td.colSpan = 8; tr.appendChild(td); table.appendChild(tr);
    });
    renderApprovals();
    const released = plan.status === "released";
    $("#pl-release-label").textContent = t(released ? "plan.withdraw_reason" : "plan.release_reason");
    $("#pl-release").textContent = t(released ? "plan.withdraw" : "plan.release");
    $("#pl-release").disabled = !released && !ev.ready;
    const hb = $("#pl-history-body"); hb.replaceChildren();
    plan.released.slice().reverse().forEach((h) => hb.appendChild(el("p", "", t("plan.history_line", { rev: h.revision, at: when(h.at), by: h.by, reason: h.reason }))));
    $("#pl-history").hidden = !plan.released.length;
  }
  function renderApprovals() {
    const { plan } = PL.view;
    const table = $("#pl-approvals"); table.replaceChildren();
    const head = el("tr"); ["roles.col_role", "plan.col_approval", ""].forEach((k) => cell(head, k ? t(k) : "", "th")); table.appendChild(head);
    PL.view.approvers.forEach((role) => {
      const tr = el("tr"); cell(tr, t("roles.role_" + role));
      const a = plan.approvals[role];
      const c = cell(tr, a ? t("plan.approved", { by: a.by, at: when(a.at), note: a.note || "–" }) : t("plan.not_approved")); c.className = a ? "status-ok" : "status-warning";
      const act = cell(tr, "");
      if (!a && plan.status === "draft" && canEditStudy()) {
        const b = el("button", "", t("plan.approve"));
        b.addEventListener("click", () => { const note = window.prompt(t("plan.approve_prompt", { role: t("roles.role_" + role) })); if (note !== null) approvePlan(role, note); });
        act.appendChild(b);
      }
      table.appendChild(tr);
    });
  }
  async function approvePlan(role, note) {
    await guarded(async () => { PL.view = await post(`/api/plans/${PL.view.plan.id}/approve`, { role, note }); });
    renderPlan();
  }
  async function releasePlan() {
    const reason = $("#pl-release-reason").value;
    const released = PL.view.plan.status === "released";
    await guarded(async () => { PL.view = await post(`/api/plans/${PL.view.plan.id}/${released ? "withdraw" : "release"}`, { reason }); $("#pl-release-reason").value = ""; });
    renderPlan();
  }
  async function fillPlanSelects() {
    const fill = async (select, path, key, label) => {
      let list = [];
      try { list = (await api(path))[key]; } catch (e) { /* none */ }
      select.replaceChildren();
      const none = el("option", "", t("plan.link_none")); none.value = ""; select.appendChild(none);
      list.forEach((x) => { const o = el("option", "", label(x)); o.value = String(x.id); select.appendChild(o); });
    };
    await fill($("#pll-msa"), "/api/msa", "systems", (s) => `${s.name} (${t("msa.gate_" + s.status)})`);
    await fill($("#pll-monitor"), "/api/monitors", "monitors", (m) => m.name);
  }
  async function openPlanEditor(view) {
    PL.editing = view ? view.plan.id : "new";
    const p = view ? view.plan : { name: "", part: "", process: "", phase: "production", description: "", lines: [] };
    if (!PL.roles) PL.roles = await api("/api/spc-roles");
    $("#pl-editor-title").textContent = view ? t("plan.edit_title", { name: p.name }) : t("plan.new");
    ["name", "part", "process", "description"].forEach((k) => { $("#ple-" + k).value = p[k] || ""; });
    $("#ple-phase").value = p.phase;
    PL.lines = JSON.parse(JSON.stringify(p.lines));
    await fillPlanSelects();
    renderLineList(); closeLineForm();
    showPlanView("#pl-editor");
  }
  function renderLineList() {
    const table = $("#ple-lines"); table.replaceChildren();
    const head = el("tr"); ["plan.col_no", "plan.l_step", "plan.l_characteristic", "plan.l_control", ""].forEach((k) => cell(head, k ? t(k) : "", "th")); table.appendChild(head);
    PL.lines.forEach((l, i) => {
      const tr = el("tr"); cell(tr, String(i + 1)); cell(tr, l.step); cell(tr, l.characteristic); cell(tr, t("plan.control_" + l.control));
      const a = cell(tr, "");
      const edit = el("button", "", t("plan.edit")); edit.addEventListener("click", () => openLineForm(i)); a.appendChild(edit);
      const del = el("button", "", t("plan.remove_line")); del.addEventListener("click", () => { PL.lines.splice(i, 1); renderLineList(); closeLineForm(); }); a.appendChild(del);
      table.appendChild(tr);
    });
  }
  function closeLineForm() { PL.lineIndex = null; $("#ple-line-form").hidden = true; }
  function openLineForm(index) {
    PL.lineIndex = index;
    const l = index === "new" ? { kind: "product_characteristic", control: "spc_chart", responsible: [], class: null } : PL.lines[index];
    LINE_FIELDS.forEach((k) => { $("#pll-" + k).value = l[k] ?? ""; });
    ["target", "lsl", "usl", "sample_size"].forEach((k) => { $("#pll-" + k).value = l[k] ?? ""; });
    $("#pll-kind").value = l.kind; $("#pll-class").value = l.class || ""; $("#pll-control").value = l.control;
    $("#pll-msa").value = l.msa_id ? String(l.msa_id) : ""; $("#pll-monitor").value = l.monitor_id ? String(l.monitor_id) : "";
    const box = $("#pll-responsible"); box.replaceChildren();
    rolesList().forEach((r) => {
      const label = el("label", "check"); const cb = el("input"); cb.type = "checkbox"; cb.value = r; cb.checked = l.responsible.includes(r);
      label.appendChild(cb); label.appendChild(el("span", "", t("roles.role_" + r))); box.appendChild(label);
    });
    $("#ple-line-title").textContent = index === "new" ? t("plan.add_line") : t("plan.edit_line", { n: index + 1 });
    $("#pll-save").textContent = t("plan.keep_line");
    $("#ple-line-form").hidden = false;
  }
  function readLine() {
    const num = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
    const line = { kind: $("#pll-kind").value, control: $("#pll-control").value, class: $("#pll-class").value || null,
      target: num("#pll-target"), lsl: num("#pll-lsl"), usl: num("#pll-usl"), sample_size: num("#pll-sample_size"),
      msa_id: $("#pll-msa").value ? Number($("#pll-msa").value) : null, monitor_id: $("#pll-monitor").value ? Number($("#pll-monitor").value) : null,
      responsible: $$("#pll-responsible input:checked").map((c) => c.value) };
    LINE_FIELDS.forEach((k) => { line[k] = $("#pll-" + k).value; });
    return line;
  }
  function keepLine() {
    const line = readLine();
    if (PL.lineIndex === "new") PL.lines.push(line); else PL.lines[PL.lineIndex] = line;
    renderLineList(); closeLineForm();
  }
  async function savePlan() {
    await guarded(async () => {
      const record = { name: $("#ple-name").value, part: $("#ple-part").value, process: $("#ple-process").value, phase: $("#ple-phase").value, description: $("#ple-description").value, lines: PL.lines };
      PL.view = PL.editing === "new" ? await post("/api/plans", { record }) : await put(`/api/plans/${PL.editing}`, { record });
      PL.saved = true;
    });
    if (PL.saved) { PL.saved = false; renderPlan(); showPlanView("#pl-detail"); }
  }
  function wirePlan() {
    $("#pl-new").addEventListener("click", () => openPlanEditor(null));
    $("#pl-edit").addEventListener("click", () => openPlanEditor(PL.view));
    $("#pl-back").addEventListener("click", () => { PL.view = null; loadPlans(); });
    $("#ple-save").addEventListener("click", savePlan);
    $("#ple-cancel").addEventListener("click", () => { if (PL.editing !== "new" && PL.view) showPlanView("#pl-detail"); else { PL.view = null; loadPlans(); } });
    $("#pll-save").addEventListener("click", keepLine);
    $("#pll-cancel").addEventListener("click", closeLineForm);
    $("#pl-release").addEventListener("click", releasePlan);
    const add = el("button", "", t("plan.add_line")); add.id = "ple-add-line"; add.type = "button";
    add.addEventListener("click", () => openLineForm("new"));
    $("#ple-lines").parentElement.after(add);
  }

  // roles
  async function loadRoles() {
    await guarded(async () => {
      PL.roles = await api("/api/spc-roles");
      PL.people = canEditStudy() ? (await api("/api/people")).people : [];
    });
    renderRoleMatrix(); renderResponsibilities(); renderPeople();
  }
  function renderRoleMatrix() {
    const table = $("#rl-matrix"); table.replaceChildren();
    const head = el("tr"); cell(head, t("roles.col_competence"), "th");
    PL.roles.roles.forEach((r) => cell(head, t("roles.role_" + r), "th")); table.appendChild(head);
    PL.roles.competences.forEach((c, ci) => {
      const tr = el("tr"); cell(tr, t("roles.comp_" + c));
      PL.roles.roles.forEach((r) => { const lv = PL.roles.matrix[r][c]; const td = cell(tr, t("roles.level_" + lv)); td.className = lv === 2 ? "status-ok" : lv === 1 ? "status-warning" : "muted"; });
      table.appendChild(tr);
    });
    const tr = el("tr"); cell(tr, t("roles.staffing"));
    PL.roles.roles.forEach((r) => { const s = PL.roles.staffing[r]; cell(tr, t("roles.staffing_cell", { q: s.qualified, a: s.assigned })).className = s.qualified ? "status-ok" : "status-warning"; });
    table.appendChild(tr);
  }
  function renderResponsibilities() {
    const box = $("#rl-resp"); box.replaceChildren();
    PL.roles.roles.forEach((r) => { const p = el("p"); p.appendChild(el("strong", "", t("roles.role_" + r) + ": ")); p.appendChild(document.createTextNode(t("roles.resp_" + r))); box.appendChild(p); });
  }
  function renderPeople() {
    const table = $("#rl-people"); table.replaceChildren();
    const head = el("tr"); ["roles.col_user", "roles.col_roles", ""].forEach((k) => cell(head, k ? t(k) : "", "th")); table.appendChild(head);
    PL.people.forEach((p) => {
      const tr = el("tr"); cell(tr, `${p.user.display_name || p.user.username} (${p.user.username})`);
      cell(tr, p.roles.map((r) => `${t("roles.role_" + r)}${p.qualification[r] ? "" : " ⚠"}`).join(", ") || "–");
      const b = el("button", "", t("mon.open")); b.addEventListener("click", () => openPerson(p.user.id)); cell(tr, "").appendChild(b);
      table.appendChild(tr);
    });
    $("#rl-person").hidden = !PL.person;
  }
  async function openPerson(id) {
    await guarded(async () => { PL.person = await api(`/api/people/${id}`); });
    renderPerson();
  }
  function renderPerson() {
    const d = PL.person; if (!d) return;
    $("#rl-person").hidden = false;
    $("#rl-person-title").textContent = `${d.user.display_name || d.user.username} (${d.user.username})`;
    const box = $("#rl-person-roles"); box.replaceChildren();
    PL.roles.roles.forEach((r) => {
      const label = el("label", "check"); const cb = el("input"); cb.type = "checkbox"; cb.value = r; cb.checked = d.roles.includes(r);
      label.appendChild(cb); label.appendChild(el("span", "", t("roles.role_" + r))); box.appendChild(label);
    });
    const table = $("#rl-comp"); table.replaceChildren();
    const head = el("tr"); ["roles.col_competence", "roles.col_needed", "roles.col_achieved", "roles.col_evidence", ""].forEach((k) => cell(head, k ? t(k) : "", "th")); table.appendChild(head);
    const needed = (c) => Math.max(0, ...d.roles.map((r) => PL.roles.matrix[r][c]));
    PL.roles.competences.forEach((c) => {
      const e = d.competences[c]; const need = needed(c); const have = e ? e.level : 0;
      const tr = el("tr"); cell(tr, t("roles.comp_" + c)); cell(tr, t("roles.level_" + need));
      cell(tr, t("roles.level_" + have)).className = have >= need ? "status-ok" : "status-alarm";
      cell(tr, e ? t("roles.evidence", { date: e.date, by: e.by, note: e.note || "–" }) : "–");
      const a = cell(tr, ""); const b = el("button", "", t("roles.record"));
      b.addEventListener("click", () => recordCompetence(c)); a.appendChild(b);
      table.appendChild(tr);
    });
  }
  async function recordCompetence(c) {
    const level = window.prompt(t("roles.prompt_level", { competence: t("roles.comp_" + c) }));
    if (level === null) return;
    const note = window.prompt(t("roles.prompt_note"));
    if (note === null) return;
    const date = new Date().toISOString().slice(0, 10);
    await guarded(async () => { PL.person = await post(`/api/people/${PL.person.user.id}/competences`, { competence: c, level: Number(level), date, note }); PL.roles = await api("/api/spc-roles"); PL.people = (await api("/api/people")).people; });
    renderRoleMatrix(); renderPeople(); renderPerson();
  }
  async function savePersonRoles() {
    const roles = $$("#rl-person-roles input:checked").map((c) => c.value);
    await guarded(async () => { PL.person = await put(`/api/people/${PL.person.user.id}/roles`, { roles }); PL.roles = await api("/api/spc-roles"); PL.people = (await api("/api/people")).people; });
    renderRoleMatrix(); renderPeople(); renderPerson();
  }
  function wireRoles() { $("#rl-person-save-roles").addEventListener("click", savePersonRoles); }

  // ---------------------------------------------------------------- machine performance studies (draft 8.1 to 8.3)
  const ST = { view: null, editing: null };
  const canEditStudy = () => state.user && ["engineer", "admin"].includes(state.user.role);
  const put = (path, body) => api(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const showStudyView = (which) => { ["#st-list-view", "#st-editor", "#st-detail"].forEach((sel) => { $(sel).hidden = sel !== which; }); };
  const EFFECTIVE_CLASS = { ok: "status-ok", warn: "status-warning", fail: "status-alarm", open: "status-alarm", deviation: "status-warning", not_applicable: "", not_done: "" };

  async function loadStudies() {
    if (ST.view && !$("#st-detail").hidden) return;  // an open study stays open when the tab is shown again
    await guarded(async () => { renderStudyList((await api("/api/studies")).studies); showStudyView("#st-list-view"); });
  }
  function renderStudyList(list) {
    const table = $("#st-list"); table.replaceChildren();
    const head = el("tr");
    ["study.f_name", "study.f_machine", "study.f_characteristic", "study.f_station", "study.col_state", ""].forEach((k) => cell(head, k ? t(k) : "", "th"));
    table.appendChild(head);
    list.forEach((s) => {
      const tr = el("tr");
      cell(tr, s.name); cell(tr, s.machine); cell(tr, s.characteristic); cell(tr, s.station);
      const text = s.closed ? t("study.state_closed", { at: when(s.closed.at) }) : s.ready ? t("study.state_ready") : t("study.state_blocked", { n: s.blockers });
      const c = cell(tr, text + (s.flagged ? ` · ${t("study.state_flagged", { n: s.flagged })}` : "")); c.className = s.closed || s.ready ? "status-ok" : "status-warning";
      const open = el("button", "", t("mon.open")); open.addEventListener("click", () => openStudy(s.id));
      cell(tr, "").appendChild(open);
      table.appendChild(tr);
    });
    if (!list.length) table.appendChild(el("tr")).appendChild(el("td", "muted", t("study.none")));
  }
  async function openStudy(id) {
    await guarded(async () => { ST.view = await api(`/api/studies/${id}`); });
    if (ST.view) { renderStudy(); showStudyView("#st-detail"); }  // after guarded: it gives the buttons their old state back
  }
  function resultText(row) {
    const r = row.result;
    if (!r) return "";
    const p = { ...r };
    if (r.result === "unknown") return t("study.res.unknown." + r.reason);
    if (r.offset_share !== undefined) p.share = sig(r.offset_share * 100, 3), p.limit = sig(r.limit * 100, 3);
    if (r.location !== undefined) { p.loc = sig(r.location, 5); p.lo = sig(r.area[0], 5); p.hi = sig(r.area[1], 5); p.range = r.range === null ? "–" : sig(r.range, 4); p.rlim = sig(r.range_limit, 4); }
    if (r.p_value !== undefined) p.p = sig(r.p_value, 3);
    if (r.cycles !== undefined) p.cycles = r.cycles === null ? "–" : r.cycles;
    const key = `study.res.${row.key}.${r.result === "fail" && r.reason ? r.reason : r.result}`;
    return key in state.msgs || key in state.fallback ? t(key, p) : t(`study.res.${row.key}.${r.result}`, p);
  }
  function renderStudy() {
    const { study: s, evaluation: ev } = ST.view;
    $("#st-title").textContent = s.name;
    $("#st-sub").textContent = t("study.sub", { machine: s.machine || "–", characteristic: s.characteristic, station: s.station || "–", rev: ST.view.study.revision });
    $("#st-verdict").textContent = s.closed ? "" : ev.ready ? t("study.verdict_ready", { flagged: ev.flagged.length }) : t("study.verdict_blocked", { n: ev.blockers.length });
    $("#st-verdict").className = "strong " + (ev.ready ? "status-ok" : "status-warning");
    const closedLine = $("#st-closed-line");
    closedLine.hidden = !s.closed;
    if (s.closed) closedLine.textContent = t("study.closed_line", { by: s.closed.by, at: when(s.closed.at), reason: s.closed.reason });
    const edit = canEditStudy();
    $("#st-edit").hidden = !edit || !!s.closed;
    $("#st-close-box").hidden = !edit;
    $("#st-close-label").textContent = t(s.closed ? "study.reopen_reason" : "study.close_reason");
    $("#st-close").textContent = t(s.closed ? "study.reopen" : "study.close");
    $("#st-close").disabled = !s.closed && !ev.ready;
    const table = $("#st-items"); table.replaceChildren();
    const head = el("tr");
    ["study.col_section", "study.col_item", "study.col_auto", "study.col_status", "study.col_note", "study.col_result"].forEach((k) => cell(head, t(k), "th"));
    table.appendChild(head);
    ev.items.forEach((row) => {
      const tr = el("tr", row.blocking ? "blocking" : "");
      cell(tr, row.section);
      cell(tr, t("study.item." + row.key) + (row.optional ? ` (${t("study.optional")})` : ""));
      cell(tr, resultText(row));
      const sel = el("select"), choices = row.auto ? ["open", "deviation", "not_applicable"] : ["open", "ok", "not_applicable", "deviation"];
      choices.forEach((c) => { const o = el("option", "", t(row.auto && c === "open" ? "study.status_auto" : "study.status_" + c)); o.value = c; sel.appendChild(o); });
      sel.value = row.status; sel.disabled = !edit || !!s.closed;
      cell(tr, "").appendChild(sel);
      const note = el("input"); note.type = "text"; note.maxLength = 2000; note.value = row.note || ""; note.disabled = !edit || !!s.closed;
      note.title = row.by ? `${row.by}, ${when(row.at)}` : "";
      const nc = cell(tr, ""); nc.appendChild(note);
      if (edit && !s.closed) {
        const save = el("button", "", t("study.save_item"));
        save.addEventListener("click", () => saveStudyItem(row.key, sel.value, note.value));
        nc.appendChild(save);
      }
      const eff = cell(tr, t("study.eff_" + row.effective)); eff.className = EFFECTIVE_CLASS[row.effective] || "";
      table.appendChild(tr);
    });
  }
  async function saveStudyItem(key, status, note) {
    await guarded(async () => { ST.view = await put(`/api/studies/${ST.view.study.id}/items/${key}`, { status, note }); });
    renderStudy();
  }
  async function closeOrReopenStudy() {
    const s = ST.view.study, reason = $("#st-close-reason").value;
    await guarded(async () => {
      ST.view = await post(`/api/studies/${s.id}/${s.closed ? "reopen" : "close"}`, { reason });
      $("#st-close-reason").value = "";
    });
    renderStudy();
  }
  async function openStudyEditor(study) {
    ST.editing = study ? study.id : "new";
    $("#st-editor-title").textContent = study ? t("study.edit_title", { name: study.name }) : t("study.new");
    const s = study || { name: "", machine: "", characteristic: "", station: "", unit: "", dataset_id: null, specs: {}, sample: {}, preproduction: {} };
    ["name", "machine", "characteristic", "station", "unit"].forEach((k) => { $("#se-" + k).value = s[k] || ""; });
    $("#se-lsl").value = s.specs.lsl ?? ""; $("#se-usl").value = s.specs.usl ?? ""; $("#se-natural").value = s.specs.natural || "";
    $("#se-reduced-by").value = s.sample.reduced_approved_by || ""; $("#se-reduced-reason").value = s.sample.reduced_reason || "";
    $("#se-wear").checked = !!s.sample.tool_wear_high; $("#se-cycles").value = s.sample.dressing_cycles ?? "";
    $("#se-pre-one").value = s.preproduction.one ?? ""; $("#se-pre-five").value = (s.preproduction.five || []).join(", ");
    const sel = $("#se-dataset"); await fillDatasetSelect(sel);
    const none = el("option", "", t("study.no_dataset")); none.value = ""; sel.insertBefore(none, sel.firstChild);
    sel.value = s.dataset_id || "";
    await fillMsaSelect($("#se-msa"), s.measurement_system_id);
    showStudyView("#st-editor");
  }
  function readStudyEditor() {
    const num = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
    const five = $("#se-pre-five").value.trim();
    return {
      name: $("#se-name").value, machine: $("#se-machine").value, characteristic: $("#se-characteristic").value, station: $("#se-station").value, unit: $("#se-unit").value,
      dataset_id: $("#se-dataset").value || null,
      measurement_system_id: $("#se-msa").value ? Number($("#se-msa").value) : null,
      specs: { lsl: num("#se-lsl"), usl: num("#se-usl"), natural: $("#se-natural").value || null },
      sample: { reduced_approved_by: $("#se-reduced-by").value, reduced_reason: $("#se-reduced-reason").value, tool_wear_high: $("#se-wear").checked, dressing_cycles: num("#se-cycles") },
      preproduction: { one: num("#se-pre-one"), five: five === "" ? null : five.split(/[\s,;]+/).filter((x) => x !== "").map(Number) },
    };
  }
  async function saveStudy() {
    await guarded(async () => {
      const record = readStudyEditor();
      ST.view = ST.editing === "new" ? await post("/api/studies", { record }) : await put(`/api/studies/${ST.editing}`, { record });
      ST.saved = true;
    });
    if (ST.saved) { ST.saved = false; renderStudy(); showStudyView("#st-detail"); }
  }
  function wireStudy() {
    $("#st-new").addEventListener("click", () => openStudyEditor(null));
    $("#se-cancel").addEventListener("click", () => { if (ST.editing !== "new" && ST.view) showStudyView("#st-detail"); else { ST.view = null; loadStudies(); } });
    $("#se-save").addEventListener("click", saveStudy);
    $("#st-back").addEventListener("click", () => { ST.view = null; loadStudies(); });
    $("#st-edit").addEventListener("click", () => openStudyEditor(ST.view.study));
    $("#st-close").addEventListener("click", closeOrReopenStudy);
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
    if (state.user) { fillMsaSelect($("#rp-msa"), $("#rp-msa").value); fillReportPlanSelect(); }
    renderModelSuggestion(); renderStateTests();
    renderTargets(); renderArl(); renderReportOut(); renderArchiveOut(); renderUserBox();
    if (state.user && M.view && !$("#mon-detail").hidden) renderMonitor();
    if (state.user && ST.view && !$("#st-detail").hidden) renderStudy();
    if (state.user && MS.view && !$("#ms-detail").hidden) renderMsa();
    if (state.user && PL.view && !$("#pl-detail").hidden) renderPlan();
    if (state.user && !$("#pl-list-view").hidden && !$("#tab-plan").hidden) renderPlanList();
    if (state.user && !$("#tab-validation").hidden) renderValidation();
    if (state.user && EQ.view && !$("#eq-detail").hidden) renderEq();
    if (state.user && !$("#eq-list-view").hidden && !$("#tab-equipment").hidden) renderEqList();
    if (state.user && PL.roles && !$("#tab-roles").hidden) { renderRoleMatrix(); renderResponsibilities(); renderPeople(); renderPerson(); }
    if (state.user && !$("#ms-list-view").hidden && !$("#tab-msa").hidden) loadMsa();
    if (state.user && !$("#st-list-view").hidden && !$("#tab-study").hidden) loadStudies();
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
    $("#a-model-suggest").addEventListener("click", suggestModel);
    $("#a-states-run").addEventListener("click", runStateTests);
    $("#t-btn").addEventListener("click", calcTargets);
    $("#l-btn").addEventListener("click", calcArl);
    $("#login-form").addEventListener("submit", (e) => { e.preventDefault(); doLogin(); });
    $("#logout-btn").addEventListener("click", doLogout);
    $("#pw-btn").addEventListener("click", () => showTab("password"));
    $("#password-form").addEventListener("submit", (e) => { e.preventDefault(); doChangePassword(); });
    $("#nu-create").addEventListener("click", createUser);
    wireMonitor();
    wireStudy();
    wireMsa();
    wirePlan();
    wireValidation();
    wireEquipment();
    wireRoles();
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

(() => {
  "use strict";

  const MONTH_FMT = new Intl.DateTimeFormat("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });
  const DATE_FMT = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
  const DAY_FMT = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "2-digit", month: "short", timeZone: "UTC" });

  const fmtDate = (iso) => DATE_FMT.format(new Date(iso + "T00:00:00Z"));
  const fmtMonth = (iso) => MONTH_FMT.format(new Date(iso.slice(0, 7) + "-01T00:00:00Z"));
  // "20 Oct 2026", "20–22 Oct 2026", "30 Nov – 04 Dec 2026"
  function fmtRange(start, end) {
    if (start === end) return fmtDate(start);
    const [a, b] = [fmtDate(start), fmtDate(end)];
    if (start.slice(0, 7) === end.slice(0, 7)) return `${a.slice(0, 2)}–${b}`;
    if (start.slice(0, 4) === end.slice(0, 4)) return `${a.slice(0, -5)} – ${b}`;
    return `${a} – ${b}`;
  }

  // filter: "text" (contains), "select" (exact), "month" (start month), "max" (numeric ≤)
  // cell: renders from the whole session; optional: hidden on mid-size screens and shown in the row details instead
  const COLUMNS = [
    { key: "code", label: "Code", filter: "text" },
    { key: "title", label: "Course", filter: "text" },
    { key: "start", label: "Dates", filterLabel: "Start month", filter: "month", cell: (s) => fmtRange(s.start, s.end) },
    { key: "durationDays", label: "Duration", filter: "select", numeric: true, render: (v) => (v === 1 ? "1 day" : `${v} days`) },
    { key: "format", label: "Format", filter: "select" },
    { key: "method", label: "Method", filter: "select", optional: true },
    { key: "type", label: "Type", filter: "select", optional: true },
    { key: "category", label: "Category", filter: "select" },
    { key: "language", label: "Language", filter: "select" },
    { key: "certification", label: "Cert. exam", filter: "select", optional: true },
    { key: "instructor", label: "Instructor", filter: "select" },
    { key: "price", label: "Price €", filter: "max", numeric: true, render: (v) => (v == null ? "" : v.toLocaleString("en-GB")) },
  ];

  const state = { sessions: [], filters: {}, q: "", sortKey: "start", sortDir: 1, open: new Set() };
  const $ = (id) => document.getElementById(id);
  const el = (tag, props = {}, ...children) => {
    const node = Object.assign(document.createElement(tag), props);
    for (const c of children) if (c != null) node.append(c);
    return node;
  };

  // ---------- URL state (shareable filtered views) ----------
  function readUrl() {
    const p = new URLSearchParams(location.search);
    for (const [k, v] of p) {
      if (k === "q") state.q = v;
      else if (k === "sort") {
        state.sortDir = v.startsWith("-") ? -1 : 1;
        state.sortKey = v.replace(/^-/, "");
      } else if (COLUMNS.some((c) => c.key === k && c.filter)) state.filters[k] = v;
    }
  }
  function writeUrl() {
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    for (const [k, v] of Object.entries(state.filters)) if (v) p.set(k, v);
    if (state.sortKey !== "start" || state.sortDir !== 1) p.set("sort", (state.sortDir < 0 ? "-" : "") + state.sortKey);
    const qs = p.toString();
    history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
  }

  // ---------- Filters ----------
  function buildFilters() {
    const grid = $("filter-grid");
    grid.replaceChildren();
    for (const col of COLUMNS.filter((c) => c.filter)) {
      const id = `f-${col.key}`;
      let control;
      if (col.filter === "text") {
        control = el("input", { id, type: "text", placeholder: "contains…", autocomplete: "off" });
      } else if (col.filter === "max") {
        control = el("input", { id, type: "number", min: 0, step: 50, placeholder: "max", inputMode: "numeric" });
      } else {
        control = el("select", { id }, el("option", { value: "", textContent: "All" }));
        const values = new Map();
        for (const s of state.sessions) {
          const raw = col.filter === "month" ? s.start.slice(0, 7) : s[col.key];
          if (raw === "" || raw == null) continue;
          const label = col.filter === "month" ? fmtMonth(s.start) : col.render ? col.render(raw) : raw;
          values.set(String(raw), label);
        }
        const keys = [...values.keys()].sort((a, b) => (col.numeric ? a - b : a.localeCompare(b)));
        for (const k of keys) control.append(el("option", { value: k, textContent: values.get(k) }));
      }
      control.value = state.filters[col.key] || "";
      control.addEventListener("input", () => {
        state.filters[col.key] = control.value.trim();
        update();
      });
      grid.append(el("label", { className: "field", htmlFor: id }, el("span", { textContent: col.filterLabel || col.label }), control));
    }
  }

  function matches(s) {
    if (state.q) {
      const hay = [s.code, s.title, s.instructor, s.category, s.description, s.role].join(" ").toLowerCase();
      if (!state.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    for (const col of COLUMNS) {
      const f = state.filters[col.key];
      if (!f || !col.filter) continue;
      const v = s[col.key];
      if (col.filter === "text" && !String(v || "").toLowerCase().includes(f.toLowerCase())) return false;
      if (col.filter === "select" && String(v) !== f) return false;
      if (col.filter === "month" && s.start.slice(0, 7) !== f) return false;
      if (col.filter === "max" && (v == null || v > Number(f))) return false;
    }
    return true;
  }

  function compare(a, b) {
    const col = COLUMNS.find((c) => c.key === state.sortKey) || COLUMNS[2];
    const va = a[col.key], vb = b[col.key];
    // Empty values always sort last, whichever direction.
    const ea = va === "" || va == null, eb = vb === "" || vb == null;
    if (ea || eb) return ea === eb ? 0 : ea ? 1 : -1;
    const r = col.numeric ? va - vb : String(va).localeCompare(String(vb), "en", { sensitivity: "base" });
    return r * state.sortDir || a.start.localeCompare(b.start) || a.code.localeCompare(b.code);
  }

  // ---------- Table ----------
  function buildHead() {
    const head = $("head");
    head.replaceChildren();
    const sortSel = $("sort-mobile");
    sortSel.replaceChildren();
    for (const col of COLUMNS) {
      const btn = el("button", { type: "button", className: "sort", textContent: col.label });
      btn.addEventListener("click", () => setSort(col.key, state.sortKey === col.key ? -state.sortDir : 1));
      const th = el("th", { scope: "col" }, btn);
      th.dataset.key = col.key;
      if (col.optional) th.className = "col-opt";
      head.append(th);
      sortSel.append(
        el("option", { value: col.key, textContent: `${col.label} ↑` }),
        el("option", { value: `-${col.key}`, textContent: `${col.label} ↓` })
      );
    }
    sortSel.addEventListener("change", () => {
      const v = sortSel.value;
      setSort(v.replace(/^-/, ""), v.startsWith("-") ? -1 : 1);
    });
  }

  function setSort(key, dir) {
    state.sortKey = key;
    state.sortDir = dir;
    update();
  }

  function renderRows(list) {
    const body = $("rows");
    body.replaceChildren();
    if (!list.length) {
      body.append(el("tr", {}, el("td", { colSpan: COLUMNS.length, className: "empty", textContent: "No sessions match these filters." })));
      return;
    }
    for (const s of list) {
      const open = state.open.has(s.id);
      const tr = el("tr", { className: "row" + (open ? " is-open" : ""), tabIndex: 0 });
      tr.setAttribute("aria-expanded", open);
      for (const col of COLUMNS) {
        const v = s[col.key];
        const td = el("td");
        td.dataset.label = col.label;
        td.dataset.key = col.key;
        if (col.optional) td.classList.add("col-opt");
        if (col.key === "title") {
          td.append(el("span", { className: "title", textContent: v }));
          for (const b of (s.status || "").split(",").map((x) => x.trim()).filter(Boolean)) {
            td.append(" ", el("span", { className: `badge badge-${b.toLowerCase()}`, textContent: b }));
          }
        } else {
          td.textContent = v === "" || v == null ? "—" : col.cell ? col.cell(s) : col.render ? col.render(v) : v;
          if (v === "" || v == null) td.classList.add("muted");
        }
        tr.append(td);
      }
      const toggle = () => {
        state.open.has(s.id) ? state.open.delete(s.id) : state.open.add(s.id);
        render();
      };
      tr.addEventListener("click", (e) => { if (!e.target.closest("a")) toggle(); });
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
      body.append(tr);
      if (open) body.append(detailRow(s));
    }
  }

  function detailRow(s) {
    const days = el("ol", { className: "days" });
    for (const d of s.days || []) days.append(el("li", {}, el("strong", { textContent: DAY_FMT.format(new Date(d.date + "T00:00:00Z")) }), " ", d.time));
    const info = el("div", { className: "detail-grid" });
    if (s.description) info.append(el("div", {}, el("h3", { textContent: "About this course" }), el("p", { textContent: s.description })));
    if (s.role) info.append(el("div", {}, el("h3", { textContent: "Who should attend" }), el("p", { textContent: s.role })));
    if (s.days && s.days.length) info.append(el("div", {}, el("h3", { textContent: "Schedule" }), days));
    const facts = el("dl", { className: "facts" });
    for (const col of COLUMNS.filter((c) => c.optional)) facts.append(el("dt", { textContent: col.label }), el("dd", { textContent: s[col.key] || "—" }));
    info.append(el("div", { className: "facts-opt" }, el("h3", { textContent: "Course facts" }), facts));
    // Only direct links to the course's own page; no generic fallback.
    const link = s.link
      ? el("a", { className: "btn-primary", href: s.link, target: "_blank", rel: "noopener", textContent: "Details & registration on itce.com" })
      : el("p", { className: "no-link", textContent: "No course page on itce.com yet." });
    return el("tr", { className: "detail" }, el("td", { colSpan: COLUMNS.length }, el("div", { className: "detail-inner" }, info, link)));
  }

  // ---------- Update cycle ----------
  let list = [];
  function update() {
    writeUrl();
    list = state.sessions.filter(matches).sort(compare);
    render();
  }
  function render() {
    for (const th of $("head").children) {
      const active = th.dataset.key === state.sortKey;
      th.setAttribute("aria-sort", active ? (state.sortDir > 0 ? "ascending" : "descending") : "none");
    }
    $("sort-mobile").value = (state.sortDir < 0 ? "-" : "") + state.sortKey;
    const active = Object.values(state.filters).filter(Boolean).length + (state.q ? 1 : 0);
    $("clear").disabled = active === 0;
    $("toggle-filters").textContent = active ? `Filters (${active})` : "Filters";
    $("count").textContent = `Showing ${list.length} of ${state.sessions.length} upcoming sessions`;
    renderRows(list);
  }

  async function init() {
    readUrl();
    buildHead();
    const toggleBtn = $("toggle-filters");
    toggleBtn.addEventListener("click", () => {
      const open = document.body.classList.toggle("filters-open");
      toggleBtn.setAttribute("aria-expanded", open);
    });
    $("q").value = state.q;
    $("q").addEventListener("input", (e) => { state.q = e.target.value.trim(); update(); });
    $("clear").addEventListener("click", () => {
      state.filters = {};
      state.q = "";
      $("q").value = "";
      buildFilters();
      update();
    });

    try {
      const res = await fetch("data/sessions.json", { cache: "no-cache" });
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      // Hide sessions that already started, even if the data has not been refreshed yet.
      const today = new Date().toISOString().slice(0, 10);
      state.sessions = data.sessions.filter((s) => s.start >= today);
      $("updated").textContent = `Last updated ${fmtDate(data.updated.slice(0, 10))}.`;
    } catch (err) {
      $("rows").replaceChildren(el("tr", {}, el("td", { className: "empty", textContent: "The schedule could not be loaded. Please try again later." })));
      return;
    }
    buildFilters();
    update();
  }

  init();
})();

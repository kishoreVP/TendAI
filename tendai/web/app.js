// TendAI web UI - plain JS, no build step. All data comes from /api.
pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => (n == null ? "—" : "₹ " + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 }));
const num = (n) => (n == null ? "—" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 }));
const STATUS = {
  DOES_NOT_MEET_REQUIREMENT: { label: "Does not meet", short: "Fails", color: "#dc2626" },
  REQUIRES_HUMAN_REVIEW: { label: "Needs review", short: "Review", color: "#2563eb" },
  INSUFFICIENT_EVIDENCE: { label: "Insufficient evidence", short: "Missing", color: "#d97706" },
  MEETS_REQUIREMENT: { label: "Meets", short: "Meets", color: "#16a34a" },
  NOT_APPLICABLE: { label: "Not applicable", short: "N/A", color: "#94a3b8" },
};
const ORDER = Object.keys(STATUS);
const AVATAR = ["#4f46e5", "#0891b2", "#c026d3", "#ea580c", "#059669"];
const pill = (s) => `<span class="pill ${s}">${STATUS[s]?.label || esc(s)}</span>`;
const state = { user: lsGet("tendai_user") || "reviewer", cases: [], case: null, report: null, view: "bidders",
  bidder: null, btab: "findings", filter: "issues", cat: "", q: "" };

function lsGet(k) { try { return localStorage.getItem(k); } catch { return null; } }

async function api(url, opts = {}) {
  const r = await fetch(url, { headers: { "Content-Type": "application/json", "X-User": state.user }, ...opts });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// ------------------------------------------------------------------ bootstrap
const VIEWS = { bidders: "Bidders", compare: "Compare", tender: "Tender requirements", benchmarks: "Benchmarks", audit: "Audit" };
async function init() {
  $("#nav").innerHTML = Object.entries(VIEWS).map(([k, v]) => `<button data-v="${k}">${v}</button>`).join("");
  $("#nav").onclick = (e) => { if (e.target.dataset.v) { state.view = e.target.dataset.v; render(); } };
  $("#case").onchange = () => selectCase(+$("#case").value);
  $("#run").onchange = () => selectRun(+$("#run").value);
  $("#vclose").onclick = $("#scrim").onclick = closeViewer;
  document.addEventListener("keydown", (e) => e.key === "Escape" && closeViewer());
  await loadCases();
}

async function loadCases(keep) {
  state.cases = await api("/api/cases");
  $("#case").innerHTML = state.cases.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  if (!state.cases.length) { $("#app").innerHTML = `<div class="shell full"><div class="card pad">No cases yet - run <code>python -m tendai demo</code>.</div></div>`; return; }
  await selectCase(keep || state.cases[0].id);
}

async function selectCase(id) {
  state.case = state.cases.find((c) => c.id === id);
  $("#case").value = id;
  $("#run").innerHTML = state.case.runs.map((r) => `<option value="${r.id}">Run ${r.id} · ${r.created.slice(0, 16).replace("T", " ")}</option>`).reverse().join("");
  const last = state.case.runs.at(-1);
  state.bidder = null;
  if (last) await selectRun(last.id); else { state.report = null; render(); }
}

async function selectRun(id) {
  $("#app").innerHTML = `<div class="loading">Loading evaluation…</div>`;
  state.report = await api(`/api/runs/${id}`);
  $("#run").value = id;
  const ranked = bidderStats();
  if (!state.bidder || !ranked.find((b) => b.id === state.bidder)) state.bidder = ranked[0]?.id;
  render();
}

function render() {
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.v === state.view));
  if (!state.report && state.view !== "tender") { $("#app").innerHTML = `<div class="shell full"><div class="card pad">No evaluation run for this case yet.</div></div>`; return; }
  ({ bidders: viewBidders, compare: viewCompare, tender: viewTender, benchmarks: viewBenchmarks, audit: viewAudit })[state.view]();
}

// ------------------------------------------------------------------ helpers
function bidderStats() {
  const r = state.report;
  const list = r.bidders.map((b, i) => {
    const res = r.results.filter((x) => x.bidder_id === b.id);
    const counts = Object.fromEntries(ORDER.map((s) => [s, res.filter((x) => x.status === s).length]));
    return { ...b, color: AVATAR[i % AVATAR.length], results: res, counts, total: r.summary.quoted_totals[b.name] || null };
  });
  [...list].filter((b) => b.total).sort((a, b) => a.total - b.total).forEach((b, i) => (b.rank = i + 1));
  return list;
}

function verdict(c) {
  if (c.DOES_NOT_MEET_REQUIREMENT) return ["DOES_NOT_MEET_REQUIREMENT", `${c.DOES_NOT_MEET_REQUIREMENT} requirement(s) not met`];
  if (c.REQUIRES_HUMAN_REVIEW || c.INSUFFICIENT_EVIDENCE) return ["REQUIRES_HUMAN_REVIEW", "No failures found - open items need review"];
  return ["MEETS_REQUIREMENT", "All evaluated requirements met"];
}

function bar(c) {
  const n = Object.values(c).reduce((a, b) => a + b, 0) || 1;
  return `<div class="bar">${ORDER.map((s) => c[s] ? `<span title="${STATUS[s].label}: ${c[s]}" style="width:${(100 * c[s]) / n}%;background:${STATUS[s].color}"></span>` : "").join("")}</div>`;
}

function donut(c) {
  const n = Object.values(c).reduce((a, b) => a + b, 0) || 1, R = 52, C = 2 * Math.PI * R;
  let off = 0;
  const arcs = ORDER.filter((s) => c[s]).map((s) => {
    const len = (C * c[s]) / n, a = `<circle r="${R}" cx="66" cy="66" fill="none" stroke="${STATUS[s].color}" stroke-width="16" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-off}" transform="rotate(-90 66 66)"/>`;
    off += len; return a;
  }).join("");
  return `<svg class="donut" viewBox="0 0 132 132">${arcs}<text x="66" y="62" text-anchor="middle" font-size="26" font-weight="800" fill="#141a2e">${c.MEETS_REQUIREMENT || 0}</text>
    <text x="66" y="82" text-anchor="middle" font-size="11" fill="#6b7389">of ${n} met</text></svg>`;
}

const req = (key) => state.report.requirements.find((q) => q.requirement_id === key);
const docOf = (id) => state.case.documents.find((d) => d.id === id);
const firstPdf = (regions) => (regions || []).find((g) => g.page && g.bbox);

function snapshot(docId, regions, label) {
  const doc = docOf(docId), g = firstPdf(regions), args = `${docId}, ${esc(JSON.stringify(regions || []))}, ${esc(JSON.stringify(label))}`;
  if (doc && doc.kind === "pdf" && g)
    return `<img class="snap" loading="lazy" alt="highlighted source" title="Click to open ${esc(doc.filename)} p.${g.page}"
      src="/api/documents/${docId}/snippet.png?page=${g.page}&bbox=${g.bbox.join(",")}" onclick="openRegions(${args})">`;
  if (doc && (regions || []).length)
    return `<a onclick="openRegions(${args})">Open ${esc(doc.filename)} ${regions[0].sheet ? "· " + esc(regions[0].sheet) + " row " + regions[0].row : ""} ↗</a>`;
  return `<div class="small muted">No located source</div>`;
}

// ------------------------------------------------------------------ bidders view
function viewBidders() {
  const list = bidderStats(), b = list.find((x) => x.id === state.bidder) || list[0];
  if (!b) { $("#app").innerHTML = `<div class="shell full"><div class="card pad">No bidders in this case.</div></div>`; return; }
  const rail = list.map((x) => `<div class="card bidder ${x.id === b.id ? "sel" : ""}" onclick="state.bidder=${x.id};render()">
      <div class="row1"><div class="avatar" style="background:${x.color}">${esc(x.name.slice(0, 2).toUpperCase())}</div>
        <div><b>${esc(x.name)}</b><div class="small muted">${money(x.total)}</div></div>
        ${x.rank ? `<span class="rank ${x.rank === 1 ? "l1" : ""}">L${x.rank}</span>` : ""}</div>
      ${bar(x.counts)}
      <div class="small muted">${ORDER.filter((s) => x.counts[s]).map((s) => `<span style="color:${STATUS[s].color};font-weight:600">${x.counts[s]}</span> ${STATUS[s].short.toLowerCase()}`).join(" · ")}</div></div>`).join("");
  const [vs, vtext] = verdict(b.counts), cover = coverage(b);
  $("#app").innerHTML = `<div class="shell"><aside class="rail"><h4>Bidders · ${esc(state.case.name)}</h4>${rail}
      <div class="notice small">Decision support only. Statuses are <b>proposals</b> until a named reviewer records a decision.</div></aside>
    <section><div class="card pad hero">${donut(b.counts)}
      <div><h2>${esc(b.name)}</h2><span class="verdict ${vs}">${vtext}</span>
        <div class="facts"><div class="fact"><span class="small muted">Quoted (excl. taxes)</span><b>${money(b.total)}</b></div>
          <div class="fact"><span class="small muted">Price rank</span><b>${b.rank ? "L" + b.rank + " of " + list.filter((x) => x.rank).length : "—"}</b></div>
          <div class="fact"><span class="small muted">vs benchmark</span><b>${cover}</b></div>
          <div class="fact"><span class="small muted">Human decisions</span><b>${b.results.filter((r) => r.reviewed).length} / ${b.results.length}</b></div></div></div>
      <div style="display:grid;gap:8px"><a class="btn primary" href="/api/runs/${state.report.run.id}/bidders/${b.id}/evidence-pack.pdf" target="_blank">⬇ Evidence pack (PDF)</a>
        <a class="btn" href="/api/runs/${state.report.run.id}/export.xlsx">⬇ Excel matrix</a></div></div>
      <div class="tabs">${[["findings", "Findings"], ["commercial", "Commercial"], ["documents", "Documents"]].map(([k, v]) =>
        `<button class="${state.btab === k ? "active" : ""}" onclick="state.btab='${k}';render()">${v}</button>`).join("")}</div>
      <div id="btab"></div></section></div>`;
  ({ findings: tabFindings, commercial: tabCommercial, documents: tabDocuments })[state.btab](b);
}

function coverage(b) {
  const mine = state.report.prices.filter((p) => p.bidder_id === b.id && p.ratio_to_benchmark);
  if (!mine.length) return "no comparable ref.";
  const avg = mine.reduce((a, p) => a + p.ratio_to_benchmark, 0) / mine.length;
  return `${Math.round(avg * 100)}% <span class="small muted">(${mine.length} item${mine.length > 1 ? "s" : ""})</span>`;
}

function tabFindings(b) {
  const cats = [...new Set(state.report.requirements.map((q) => q.category))];
  const groups = { issues: ["DOES_NOT_MEET_REQUIREMENT", "REQUIRES_HUMAN_REVIEW", "INSUFFICIENT_EVIDENCE"], all: ORDER, ...Object.fromEntries(ORDER.map((s) => [s, [s]])) };
  const chip = (k, label, n) => `<span class="chip ${state.filter === k ? "on" : ""}" onclick="state.filter='${k}';tabFindings(currentBidder())">${label}${n != null ? ` <b>${n}</b>` : ""}</span>`;
  const issues = b.counts.DOES_NOT_MEET_REQUIREMENT + b.counts.REQUIRES_HUMAN_REVIEW + b.counts.INSUFFICIENT_EVIDENCE;
  const rows = b.results.filter((r) => groups[state.filter].includes(r.status))
    .filter((r) => { const q = req(r.requirement_key); return (!state.cat || q.category === state.cat) && (!state.q || (q.requirement_id + q.title + q.normalised_requirement).toLowerCase().includes(state.q.toLowerCase())); })
    .sort((x, y) => ORDER.indexOf(x.status) - ORDER.indexOf(y.status));
  $("#btab").innerHTML = `<div class="chips">${chip("issues", "Open issues", issues)}${ORDER.slice(0, 4).map((s) => chip(s, STATUS[s].short, b.counts[s])).join("")}${chip("all", "All")}
    <select class="chip" onchange="state.cat=this.value;tabFindings(currentBidder())"><option value="">All categories</option>${cats.map((c) => `<option ${c === state.cat ? "selected" : ""}>${c}</option>`).join("")}</select>
    <input class="search" placeholder="Search requirement…" value="${esc(state.q)}" oninput="state.q=this.value;clearTimeout(window._qt);window._qt=setTimeout(()=>tabFindings(currentBidder()),250)"></div>
    ${rows.map(findingCard).join("") || `<div class="card pad muted">Nothing in this filter.</div>`}`;
}
const currentBidder = () => bidderStats().find((x) => x.id === state.bidder);

function findingCard(r) {
  const q = req(r.requirement_key), rule = q.evaluation_rule, ev = r.evidence;
  const required = q.expected_value || (rule.permitted_values || []).join(" / ") || (rule.op === "document" ? "Document must be submitted" + (rule.must_mention ? ` (with ${rule.must_mention.join(", ")})` : "") : rule.value) || q.normalised_requirement;
  const offered = ev.length ? [...new Set(ev.map((e) => e.raw_value))].join("  |  ") : "Not found in submission";
  const ai = q.extraction_method.startsWith("llm") ? `<span class="tag ai">AI-proposed requirement</span>` : "";
  const evAi = ev.some((e) => e.evidence_type === "ai_inferred") ? `<span class="tag ai">AI-extracted value</span>` : "";
  const more = ev.slice(1).map((e, i) => `<a class="small" onclick='openRegions(${e.source_document_id}, ${esc(JSON.stringify(e.source_regions))}, ${esc(JSON.stringify("Evidence " + (i + 2)))})'>+ ${esc(e.evidence_type.replaceAll("_", " "))}: ${esc(e.raw_value)} (${esc(docOf(e.source_document_id)?.filename)} p.${e.source_page ?? "?"})</a>`).join("<br>");
  return `<div class="card finding ${r.status}"><div class="stripe"></div><div class="body">
    <div class="fhead">${pill(r.status)}${r.reviewed ? `<span class="pill MEETS_REQUIREMENT">✔ decided: ${STATUS[r.final_status]?.label}</span>` : ""}
      <b>${esc(q.title)}</b><span class="id">${esc(q.requirement_id)} · ${esc(q.category.replaceAll("_", " "))}</span></div>
    <div class="vs">
      <div class="side"><div class="lbl">Tender requires · p.${q.source_page ?? "?"} ${q.source_clause ? "cl. " + esc(q.source_clause) : ""}</div>
        <div class="val">${esc(rule.op === "document" || rule.op === "provided" ? "" : rule.op + " ")}${esc(required)}</div>${snapshot(q.source_document_id, q.source_regions, "Tender: " + q.requirement_id)}</div>
      <div class="side"><div class="lbl">Bidder submitted ${ev[0] ? "· " + esc(ev[0].evidence_type.replaceAll("_", " ")) : ""}</div>
        <div class="val" style="color:${ev.length ? "inherit" : "var(--insuf)"}">${esc(offered)}</div>
        ${ev[0] ? snapshot(ev[0].source_document_id, ev[0].source_regions, "Bidder evidence") : ""}${more ? `<div style="margin-top:6px">${more}</div>` : ""}</div></div>
    <div class="why"><span class="tag rule">rule</span>${esc(r.explanation)} ${ai}${evAi}</div>
    ${q.flags?.length ? `<div class="flag">⚑ ${q.flags.map(esc).join(" · ")}</div>` : ""}
    <details class="decide"><summary>${r.reviewed ? `Decided by ${esc(r.reviewer)}: ${esc(r.review_reason)}` : "Record a reviewer decision"}</summary>
      <div class="decide-row"><select id="dec${r.id}">${ORDER.map((s) => `<option value="${s}" ${s === (r.final_status || r.auto_status) ? "selected" : ""}>${STATUS[s].label}</option>`).join("")}</select>
      <input id="why${r.id}" placeholder="Reason (required)" size="40"><button class="btn primary" onclick="decide(${r.id})">Save decision</button></div></details>
  </div></div>`;
}

async function decide(id) {
  try {
    await api(`/api/results/${id}/decision`, { method: "POST", body: JSON.stringify({ final_status: $("#dec" + id).value, reason: $("#why" + id).value }) });
    await selectRun(state.report.run.id);
  } catch (e) { alert(e.message); }
}

function tabCommercial(b) {
  const r = state.report, mine = r.prices.filter((p) => p.bidder_id === b.id), all = bidderStats().filter((x) => x.total);
  const max = Math.max(...all.map((x) => x.total));
  const totals = all.sort((x, y) => x.total - y.total).map((x) => `<div style="display:grid;grid-template-columns:200px 1fr 120px;gap:10px;align-items:center;margin:6px 0">
      <span style="font-weight:${x.id === b.id ? 700 : 400}">L${x.rank} · ${esc(x.name)}</span>
      <div class="cmp" style="margin:0"><i style="width:${(100 * x.total) / max}%;background:${x.id === b.id ? "var(--accent)" : "#c7cbe0"}"></i></div><b>${money(x.total)}</b></div>`).join("");
  const rows = mine.map((p) => {
    const bm = p.benchmark, ref = bm.summary?.median ?? bm.partial[0]?.normalised_rate, kind = bm.summary ? "comparable" : bm.partial.length ? "partial" : null;
    const ratio = ref && p.quoted_rate ? p.quoted_rate / ref : null, hi = Math.max(p.quoted_rate || 0, ref || 0) * 1.15 || 1;
    // colour only like-for-like comparisons; a partial (different scope) reference stays neutral
    const col = !ratio || kind !== "comparable" ? "#94a3b8" : ratio <= 1.1 ? "var(--meets)" : ratio <= 1.25 ? "var(--insuf)" : "var(--fails)";
    const refs = [...bm.comparable.map((e) => ({ ...e, k: "comparable" })), ...bm.partial.map((e) => ({ ...e, k: "partial" }))].slice(0, 3);
    return `<tr><td><a onclick="openNode(${p.tender_node_id})">${esc(p.sl)}</a></td><td style="max-width:360px">${esc(p.description.slice(0, 140))}${p.description.length > 140 ? "…" : ""}</td>
      <td>${num(p.qty)} ${esc(p.unit)}</td><td>${p.node_id ? `<a onclick="openNode(${p.node_id}, '${p.quoted_column}')">${num(p.quoted_rate)}</a>` : "—"}<div class="small muted">${money(p.amount)}</div></td>
      <td>${ref ? `<b>${num(ref)}</b> <span class="small muted">${kind}</span>` : `<span class="small muted">no comparable reference</span>`}
        ${ratio ? `<div class="cmp"><i style="width:${(100 * p.quoted_rate) / hi}%;background:${col}"></i><em style="left:${(100 * ref) / hi}%"></em></div><div class="small" style="color:${col};font-weight:700">${Math.round(ratio * 100)}% of ${kind === "comparable" ? "reference" : "partial reference (indicative only)"}</div>` : ""}
        ${refs.map((e) => `<div class="small"><a onclick="openBench(${e.benchmark_id})">${esc(e.code)}</a> ${num(e.normalised_rate)} · ${esc(e.source_type)}${(e.notes || []).length ? ` · <span class="flag">${esc(e.notes[0])}</span>` : ""}</div>`).join("")}
        ${bm.excluded.length ? `<details class="small"><summary class="muted">${bm.excluded.length} excluded reference(s)</summary>${bm.excluded.slice(0, 4).map((e) => `<div class="muted">${esc(e.code)}: ${esc(e.reason)}</div>`).join("")}</details>` : ""}</td></tr>`;
  }).join("");
  $("#btab").innerHTML = `<div class="card pad" style="margin-bottom:14px"><h3>Quoted totals (excluding taxes)</h3>${totals}</div>
    <div class="notice">References are compared only when technical attributes don't conflict. <b>Partial</b> = different scope (e.g. SOR material rate vs supply-and-fix item): indicative only. No tax / escalation / location adjustment without a documented basis. Bar marker = reference rate.</div>
    <div class="card"><table><tr><th>Sl</th><th>BOQ item</th><th>Qty</th><th>Quoted rate</th><th>Benchmark</th></tr>${rows || `<tr><td colspan=5 class="muted">No priced BOQ found for this bidder.</td></tr>`}</table></div>`;
}

function tabDocuments(b) {
  const docs = state.case.documents.filter((d) => d.bidder_id === b.id);
  $("#btab").innerHTML = `<div class="card"><table><tr><th>Document</th><th>Type</th><th>Status</th><th>SHA-256</th><th>Extraction notes</th></tr>
    ${docs.map((d) => `<tr><td><a onclick="openDoc(${d.id})">${esc(d.filename)}</a></td><td>${d.kind.toUpperCase()}</td><td>${esc(d.status)}</td>
      <td class="small muted">${d.sha256.slice(0, 16)}…</td><td class="flag">${d.warnings.map(esc).join("<br>") || '<span class="muted">—</span>'}</td></tr>`).join("")}</table></div>`;
}

// ------------------------------------------------------------------ compare view
function viewCompare() {
  const r = state.report, list = bidderStats(), byKey = {};
  r.results.forEach((x) => ((byKey[x.requirement_key] ||= {})[x.bidder_id] = x));
  $("#app").innerHTML = `<div class="shell full"><div class="card"><table><tr><th>Requirement</th><th>Tender value</th>${list.map((b) => `<th>${esc(b.name)}</th>`).join("")}</tr>
    ${r.requirements.map((q) => `<tr><td><b>${esc(q.title)}</b><div class="small muted">${esc(q.requirement_id)} · ${esc(q.category)}</div></td>
      <td class="small">${esc(q.expected_value || q.evaluation_rule.value || (q.evaluation_rule.permitted_values || []).join(", ") || q.evaluation_rule.op)}</td>
      ${list.map((b) => { const x = byKey[q.requirement_id]?.[b.id]; return x ? `<td style="cursor:pointer" onclick="state.bidder=${b.id};state.view='bidders';state.btab='findings';state.filter='all';state.q='${esc(q.requirement_id)}';render()">${pill(x.status)}<div class="small muted">${esc(x.evidence.map((e) => e.raw_value).join(" | ").slice(0, 50))}</div></td>` : "<td></td>"; }).join("")}</tr>`).join("")}</table></div></div>`;
}

// ------------------------------------------------------------------ tender view
async function viewTender() {
  const reqs = await api(`/api/cases/${state.case.id}/requirements`), cats = [...new Set(reqs.map((r) => r.data.category))];
  const tdocs = state.case.documents.filter((d) => d.role !== "bidder");
  $("#app").innerHTML = `<div class="shell full"><div class="card pad">
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px"><h2 style="margin-right:auto">Requirement template</h2>
      ${state.case.templates.map((t) => `<span class="chip">v${t.version} · ${t.count} reqs · ${esc(t.approved_by)}</span>`).join("")}
      <button class="btn primary" onclick="freeze()">Freeze approved → new version & run</button></div>
    <div class="chips"><input class="search" id="rq" placeholder="Search…"><select class="chip" id="rcat"><option value="">All categories</option>${cats.map((c) => `<option>${c}</option>`).join("")}</select>
      <select class="chip" id="rst"><option value="">Any review state</option><option>draft</option><option>approved</option><option>rejected</option></select>
      <span class="small muted">Source documents: ${tdocs.map((d) => `<a onclick="openDoc(${d.id})">${esc(d.filename)}</a>`).join(", ")}</span></div>
    <table id="rtable"></table></div></div>`;
  const draw = () => {
    const q = $("#rq").value.toLowerCase(), cat = $("#rcat").value, st = $("#rst").value;
    $("#rtable").innerHTML = `<tr><th>Requirement</th><th>Rule</th><th>Source</th><th>Review</th></tr>` + reqs.filter((r) => (!cat || r.data.category === cat) && (!st || r.review_status === st) && (!q || JSON.stringify(r.data).toLowerCase().includes(q))).map((r) => {
      const d = r.data, rule = d.evaluation_rule;
      return `<tr><td><b>${esc(d.title)}</b> <span class="small muted">${esc(r.key)} · ${esc(d.category)} · ${esc(d.mandatory_status)}</span>
        <div class="small">${esc(d.normalised_requirement)}</div>
        ${d.extraction_method.startsWith("llm") ? `<span class="tag ai">AI-proposed (${esc(d.extraction_method.slice(4))})</span>` : `<span class="tag rule">${esc(d.extraction_method)}</span>`}
        ${d.flags.map((f) => `<div class="flag">⚑ ${esc(f)}</div>`).join("")}${d.applicability_conditions ? `<div class="small muted">Applies: ${esc(d.applicability_conditions)}</div>` : ""}</td>
        <td><code class="small">${esc(rule.op)} ${esc(rule.value ?? (rule.permitted_values || []).join(", "))} ${esc(rule.unit || "")}</code></td>
        <td style="width:260px">${snapshot(d.source_document_id, d.source_regions, "Tender " + r.key)}</td>
        <td style="white-space:nowrap"><span class="pill ${r.review_status === "approved" ? "MEETS_REQUIREMENT" : r.review_status === "rejected" ? "DOES_NOT_MEET_REQUIREMENT" : "NOT_APPLICABLE"}">${r.review_status}</span><br>
          <button class="btn" onclick="reviewReq(${r.id},'approved')">✓</button> <button class="btn" onclick="reviewReq(${r.id},'rejected')">✕</button> <button class="btn" onclick="editReq(${r.id})">✎</button>
          ${r.history.length ? `<div class="small muted">${r.history.length} change(s)</div>` : ""}</td></tr>`;
    }).join("");
  };
  ["#rq", "#rcat", "#rst"].forEach((s) => ($(s).oninput = draw));
  window._reqs = reqs; draw();
}

async function reviewReq(id, status) {
  const comment = prompt(`Comment for '${status}':`);
  if (comment === null) return;
  await api(`/api/requirements/${id}/review`, { method: "POST", body: JSON.stringify({ status, comment }) });
  viewTender();
}

async function editReq(id) {
  const r = window._reqs.find((x) => x.id === id);
  const text = prompt("Edit the normalised requirement and rule (JSON). The verbatim tender text cannot be edited.",
    JSON.stringify({ normalised_requirement: r.data.normalised_requirement, evaluation_rule: r.data.evaluation_rule }));
  if (!text) return;
  try { await api(`/api/requirements/${id}/review`, { method: "POST", body: JSON.stringify({ changes: JSON.parse(text), comment: "edited in UI" }) }); } catch (e) { alert(e.message); }
  viewTender();
}

async function freeze() {
  try {
    const t = await api(`/api/cases/${state.case.id}/templates`, { method: "POST" });
    $("#app").innerHTML = `<div class="loading">Template v${t.version} frozen - running evaluation…</div>`;
    const { id } = await api(`/api/templates/${t.id}/runs`, { method: "POST" });
    await loadCases(state.case.id); await selectRun(id); state.view = "bidders"; render();
  } catch (e) { alert(e.message); }
}

// ------------------------------------------------------------------ benchmarks / audit
async function viewBenchmarks() {
  $("#app").innerHTML = `<div class="shell full"><div class="card pad"><div class="chips"><h2 style="margin-right:auto">Benchmarks</h2><input class="search" id="bq" placeholder="Search code or description (e.g. MCB, 630)"></div><div id="btable"></div></div></div>`;
  const draw = async () => {
    const d = await api(`/api/cases/${state.case.id}/benchmarks?q=${encodeURIComponent($("#bq").value)}&limit=200`);
    $("#btable").innerHTML = `<p class="small muted">${d.total} records</p><table><tr><th>Code</th><th>Description</th><th>Unit</th><th>Rate</th><th>Source</th></tr>` +
      d.items.map((b) => `<tr><td><a onclick="openBench(${b.id})">${esc(b.code)}</a></td><td>${esc(b.normalised_description)}<div class="small muted">${esc(Object.entries(b.attributes).map(([k, v]) => k + ": " + v).join(" · "))}</div></td>
        <td>${esc(b.unit)}</td><td><b>${num(b.rate)}</b></td><td class="small">${esc(b.source_type)} · ${esc(b.effective_date || "")}<div class="muted">${esc(b.tax_basis || "tax basis not stated")}</div></td></tr>`).join("") + "</table>";
  };
  let t; $("#bq").oninput = () => { clearTimeout(t); t = setTimeout(draw, 250); }; draw();
}

async function viewAudit() {
  const rows = await api("/api/audit?limit=300");
  $("#app").innerHTML = `<div class="shell full"><div class="card"><table><tr><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Entity</th><th>Detail</th></tr>` +
    rows.map((a) => `<tr><td class="small">${a.ts.slice(0, 19).replace("T", " ")}</td><td>${esc(a.actor)}</td><td><b>${esc(a.action)}</b></td><td>${esc(a.entity)} ${esc(a.entity_id)}</td><td class="small muted">${esc(JSON.stringify(a.detail)).slice(0, 220)}</td></tr>`).join("") + "</table></div></div>";
}

// ------------------------------------------------------------------ source viewer
const viewer = { doc: null, pdf: null, page: 1, regions: [] };
$("#prev").onclick = () => viewer.pdf && viewer.page > 1 && renderPage(viewer.page - 1);
$("#next").onclick = () => viewer.pdf && viewer.page < viewer.pdf.numPages && renderPage(viewer.page + 1);
function closeViewer() { $("#drawer").classList.remove("open"); $("#scrim").classList.remove("open"); }

async function openNode(id, col) { const n = await api(`/api/nodes/${id}`); openRegions(n.doc_id, n.regions.map((g) => ({ ...g, col_letter: col })), n.ref || n.kind); }
async function openDoc(id) { openRegions(id, [], ""); }
async function openBench(id) { const b = await api(`/api/benchmarks/${id}`); openRegions(b.doc_id, b.source_regions, `Benchmark ${b.code}`); }

async function openRegions(docId, regions, label) {
  $("#drawer").classList.add("open"); $("#scrim").classList.add("open");
  $("#vbody").innerHTML = `<div class="loading">Loading document…</div>`; $("#vnote").textContent = "";
  if (!viewer.doc || viewer.doc.id !== docId) {
    viewer.doc = await api(`/api/documents/${docId}`); viewer.pdf = null;
    if (viewer.doc.kind === "pdf") viewer.pdf = await pdfjsLib.getDocument(`/api/documents/${docId}/file`).promise;
  }
  viewer.regions = regions || [];
  $("#vtitle").textContent = `${viewer.doc.filename}${label ? " — " + label : ""}`;
  $("#vdownload").href = `/api/documents/${docId}/file`;
  if (viewer.doc.kind === "pdf") {
    const pages = [...new Set(viewer.regions.map((g) => g.page).filter(Boolean))];
    if (pages.length > 1) $("#vnote").innerHTML = "Highlighted on pages: " + pages.map((p) => `<a onclick="renderPage(${p})">${p}</a>`).join(", ");
    if (viewer.regions.length && viewer.regions.every((g) => !g.bbox)) $("#vnote").textContent = "No precise region for this item - showing the page only.";
    await renderPage(pages[0] || 1);
  } else if (["xls", "xlsx", "csv"].includes(viewer.doc.kind)) renderSheet();
  else renderParagraphs();
}

async function renderPage(n) {
  viewer.page = n;
  const page = await viewer.pdf.getPage(n), body = $("#vbody"), base = page.getViewport({ scale: 1 });
  const scale = Math.min(2, (body.clientWidth - 30) / base.width), vp = page.getViewport({ scale }); // applies /Rotate like stored boxes
  body.innerHTML = `<div class="pagewrap" style="width:${vp.width}px"><canvas></canvas></div>`;
  const canvas = body.querySelector("canvas"), dpr = window.devicePixelRatio || 1;
  canvas.width = vp.width * dpr; canvas.height = vp.height * dpr; canvas.style.width = vp.width + "px"; canvas.style.height = vp.height + "px";
  await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp, transform: [dpr, 0, 0, dpr, 0, 0] }).promise;
  let first = null;
  viewer.regions.filter((g) => g.page === n && g.bbox).forEach((g) => {
    const [x0, y0, x1, y1] = g.bbox, d = document.createElement("div");
    d.className = "hl"; Object.assign(d.style, { left: x0 * scale - 3 + "px", top: y0 * scale - 3 + "px", width: (x1 - x0) * scale + 6 + "px", height: (y1 - y0) * scale + 6 + "px" });
    body.querySelector(".pagewrap").appendChild(d); first ||= d;
  });
  $("#vpage").textContent = `page ${n} / ${viewer.pdf.numPages}`;
  if (first) first.scrollIntoView({ block: "center" });
}

async function renderSheet() {
  const rows = await api(`/api/documents/${viewer.doc.id}/nodes?kind=row&limit=2000`), hit = viewer.regions[0] || {};
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r.meta.cells || {})))].sort((a, b) => a.length - b.length || a.localeCompare(b)).slice(0, 30);
  $("#vpage").textContent = hit.sheet ? `${hit.sheet} · row ${hit.row ?? ""}${hit.col_letter ? " · col " + hit.col_letter : ""}` : "";
  $("#vbody").innerHTML = `<table class="sheet"><tr><th>#</th>${cols.map((c) => `<th>${c}</th>`).join("")}</tr>` + rows.map((r) => {
    const on = r.meta.row === hit.row;
    return `<tr class="${on ? "hit" : ""}" ${on ? 'id="sheethit"' : ""}><td>${r.meta.row}</td>${cols.map((c) => `<td class="${on && hit.col_letter === c ? "hitcell" : ""}" title="${esc(r.meta.cells[c])}">${esc(r.meta.cells[c] || "")}</td>`).join("")}</tr>`;
  }).join("") + "</table>";
  (document.querySelector(".sheet td.hitcell") || document.getElementById("sheethit"))?.scrollIntoView({ block: "center", inline: "center" });
}

async function renderParagraphs() {
  const nodes = await api(`/api/documents/${viewer.doc.id}/nodes?limit=2000`), want = new Set(viewer.regions.map((g) => g.paragraph));
  $("#vnote").textContent = "DOCX: positions are paragraph references (no reliable page coordinates).";
  $("#vbody").innerHTML = `<div class="card pad">` + nodes.map((n) => `<p style="${n.regions.some((g) => want.has(g.paragraph)) ? "background:#fff4c2" : ""}">${esc(n.text)}</p>`).join("") + "</div>";
}

init().catch((e) => ($("#app").innerHTML = `<div class="shell full"><div class="card pad flag">${esc(e.message)}</div></div>`));

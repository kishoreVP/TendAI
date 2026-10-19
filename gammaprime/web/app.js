// GammaPrime web UI: plain JS, no build step. All data comes from /api.
pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (url, opts = {}) => {
  const r = await fetch(url, { headers: { "Content-Type": "application/json", "X-User": state.user }, ...opts });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const pill = (s) => `<span class="pill ${s}">${esc((s || "").replaceAll("_", " ").toLowerCase())}</span>`;
const state = { user: localStorageGet("gp_user") || "reviewer", cases: [], case: null, run: null, report: null, tab: "overview" };

function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }

const TABS = { overview: "Overview", requirements: "Requirements", matrix: "Compliance matrix", commercial: "Commercial",
  benchmarks: "Benchmarks", audit: "Audit" };

async function init() {
  $("#tabs").innerHTML = Object.entries(TABS).map(([k, v]) => `<button data-tab="${k}">${v}</button>`).join(" ");
  $("#tabs").onclick = (e) => { if (e.target.dataset.tab) { state.tab = e.target.dataset.tab; render(); } };
  $("#case").onchange = () => selectCase(+$("#case").value);
  $("#run").onchange = () => selectRun(+$("#run").value);
  await loadCases();
}

async function loadCases(keepCase) {
  state.cases = await api("/api/cases");
  $("#case").innerHTML = state.cases.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  if (!state.cases.length) { $("#content").innerHTML = "<p>No cases yet. Run <code>python -m gammaprime demo</code> or upload files via the API.</p>"; return; }
  await selectCase(keepCase || state.cases[0].id);
}

async function selectCase(id) {
  state.case = state.cases.find((c) => c.id === id);
  $("#case").value = id;
  $("#run").innerHTML = state.case.runs.map((r) => `<option value="${r.id}">run ${r.id} (${r.created.slice(0, 16)})</option>`).join("") || "<option>no runs</option>";
  const last = state.case.runs.at(-1);
  if (last) await selectRun(last.id); else { state.report = null; render(); }
}

async function selectRun(id) {
  $("#status").textContent = "loading run…";
  state.report = await api(`/api/runs/${id}`);
  state.run = id; $("#run").value = id; $("#status").textContent = "";
  render();
}

function render() {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === state.tab));
  ({ overview, requirements, matrix, commercial, benchmarks, audit })[state.tab]();
}

// ------------------------------------------------------------------ overview
function overview() {
  const c = state.case, r = state.report;
  if (!r) { $("#content").innerHTML = "<p>No evaluation run for this case yet.</p>"; return; }
  const s = r.summary;
  const docs = c.documents.map((d) => `<tr><td><a onclick="openDoc(${d.id})">${esc(d.filename)}</a></td><td>${d.role}${d.bidder_id ? " / " + esc(c.bidders.find((b) => b.id === d.bidder_id)?.name) : ""}</td>
    <td>${d.status}</td><td class="muted">${d.sha256.slice(0, 12)}</td><td class="flag">${d.warnings.length ? d.warnings.length + " warning(s)" : ""}</td></tr>`).join("");
  const bidders = Object.entries(s.status_by_bidder).map(([b, counts]) => `<tr><td><b>${esc(b)}</b></td><td>${Object.entries(counts).map(([k, v]) => `${pill(k)} ${v}`).join(" ")}</td>
    <td>${s.quoted_totals[b] ? "₹ " + s.quoted_totals[b].toLocaleString("en-IN") : "—"}</td></tr>`).join("");
  $("#content").innerHTML = `
  <div class="notice">Decision support only. Automatic statuses are <b>proposals</b>; a result is final only after a named reviewer records a decision.
  Template v${r.template.version} approved by <b>${esc(r.template.approved_by)}</b>.</div>
  <div class="grid">
    <div class="card stat"><b>${s.requirements}</b>requirements in template</div>
    <div class="card stat"><b>${s.reviewed_results} / ${s.total_results}</b>results with a human decision</div>
    <div class="card stat"><b>${s.requirement_flags}</b>requirements with flags (duplicates, conflicts)</div>
    <div class="card stat"><b>${s.benchmark_coverage.with_comparable_reference} + ${s.benchmark_coverage.with_partial_reference}</b>of ${s.benchmark_coverage.items} BOQ items with comparable + partial benchmarks</div>
    <div class="card stat"><b>${s.extraction_warnings}</b>extraction warnings</div>
  </div>
  <div class="card"><h3>Bidders (proposed statuses)</h3><table><tr><th>Bidder</th><th>Status counts</th><th>Quoted total (excl. taxes)</th></tr>${bidders}</table></div>
  <div class="card"><h3>Source documents</h3><table><tr><th>File</th><th>Role</th><th>Status</th><th>SHA-256</th><th></th></tr>${docs}</table></div>
  <div class="card"><h3>Exports</h3>${["xlsx", "pdf", "csv", "json"].map((f) => `<a href="/api/runs/${state.run}/export.${f}">${f.toUpperCase()}</a>`).join(" · ")}
   · <button onclick="rerun()">Re-run evaluation on template v${r.template.version}</button></div>`;
}

async function rerun() {
  $("#status").textContent = "running…";
  const { id } = await api(`/api/templates/${state.report.template.id}/runs`, { method: "POST" });
  await loadCases(state.case.id); await selectRun(id);
}

// -------------------------------------------------------------- requirements
async function requirements() {
  const reqs = await api(`/api/cases/${state.case.id}/requirements`);
  const cats = [...new Set(reqs.map((r) => r.data.category))];
  $("#content").innerHTML = `<div class="filters">
    <input id="rq" placeholder="search"> <select id="rcat"><option value="">all categories</option>${cats.map((c) => `<option>${c}</option>`).join("")}</select>
    <select id="rst"><option value="">all review states</option><option>draft</option><option>approved</option><option>rejected</option></select>
    <button class="primary" onclick="freeze()">Freeze approved → new template version</button>
    ${state.case.templates.map((t) => `<span class="tag">v${t.version}: ${t.count} reqs by ${esc(t.approved_by)}</span>`).join("")}</div>
    <table id="rtable"></table>`;
  const draw = () => {
    const q = $("#rq").value.toLowerCase(), cat = $("#rcat").value, st = $("#rst").value;
    const rows = reqs.filter((r) => (!cat || r.data.category === cat) && (!st || r.review_status === st) &&
      (!q || JSON.stringify(r.data).toLowerCase().includes(q)));
    $("#rtable").innerHTML = `<tr><th>Key</th><th>Category</th><th>Requirement (normalised)</th><th>Rule</th><th>Mandatory</th><th>Source</th><th>Review</th></tr>` +
      rows.map((r) => { const d = r.data, rule = d.evaluation_rule;
        const ai = d.extraction_method.startsWith("llm") ? `<span class="tag ai">AI-proposed</span>` : `<span class="tag rule">${esc(d.extraction_method)}</span>`;
        return `<tr><td>${esc(r.key)}</td><td>${esc(d.category)}</td><td>${esc(d.normalised_requirement)}<br>${ai}
          ${d.flags.map((f) => `<div class="flag">⚑ ${esc(f)}</div>`).join("")}${d.applicability_conditions ? `<div class="muted">Applies: ${esc(d.applicability_conditions)}</div>` : ""}</td>
          <td><code>${esc(rule.op)} ${esc(rule.value ?? (rule.permitted_values || []).join(", "))} ${esc(rule.unit || "")}</code></td><td>${esc(d.mandatory_status)}</td>
          <td><a onclick='openRegions(${d.source_document_id}, ${JSON.stringify(d.source_regions)}, ${JSON.stringify("Tender clause " + (d.source_clause || ""))})'>p.${d.source_page ?? "?"} ${esc(d.source_clause || "")}</a></td>
          <td>${esc(r.review_status)}<br><button onclick="reviewReq(${r.id},'approved')">approve</button> <button onclick="reviewReq(${r.id},'rejected')">reject</button>
          <button onclick="editReq(${r.id})">edit</button>${r.history.length ? `<div class="muted">${r.history.length} change(s)</div>` : ""}</td></tr>`; }).join("");
  };
  ["#rq", "#rcat", "#rst"].forEach((s) => $(s).oninput = draw);
  window._reqs = reqs; draw();
}

async function reviewReq(id, status) {
  const comment = prompt(`Comment for ${status}:`) ?? null;
  await api(`/api/requirements/${id}/review`, { method: "POST", body: JSON.stringify({ status, comment }) });
  requirements();
}

async function editReq(id) {
  const r = window._reqs.find((x) => x.id === id);
  const text = prompt("Edit normalised requirement + rule as JSON (verbatim source text cannot be edited):",
    JSON.stringify({ normalised_requirement: r.data.normalised_requirement, evaluation_rule: r.data.evaluation_rule }));
  if (!text) return;
  try { await api(`/api/requirements/${id}/review`, { method: "POST", body: JSON.stringify({ changes: JSON.parse(text), comment: "edited in UI" }) }); }
  catch (e) { alert(e.message); }
  requirements();
}

async function freeze() {
  try { const t = await api(`/api/cases/${state.case.id}/templates`, { method: "POST" }); alert(`Template v${t.version} frozen. Run it from the overview of a new run, or:`);
    const { id } = await api(`/api/templates/${t.id}/runs`, { method: "POST" }); await loadCases(state.case.id); await selectRun(id); }
  catch (e) { alert(e.message); }
}

// -------------------------------------------------------------------- matrix
function matrix() {
  const r = state.report; if (!r) return overview();
  const bidders = r.bidders.map((b) => b.name);
  const byKey = {};
  r.results.forEach((x) => ((byKey[x.requirement_key] ||= {})[x.bidder] = x));
  const cats = [...new Set(r.requirements.map((q) => q.category))];
  $("#content").innerHTML = `<div class="filters"><input id="mq" placeholder="search requirement"><select id="mcat"><option value="">all categories</option>${cats.map((c) => `<option>${c}</option>`).join("")}</select>
    <select id="mst"><option value="">any status</option>${["MEETS_REQUIREMENT", "DOES_NOT_MEET_REQUIREMENT", "INSUFFICIENT_EVIDENCE", "REQUIRES_HUMAN_REVIEW"].map((s) => `<option>${s}</option>`).join("")}</select></div>
    <table id="mtable"></table><div id="finding"></div>`;
  const draw = () => {
    const q = $("#mq").value.toLowerCase(), cat = $("#mcat").value, st = $("#mst").value;
    const reqs = r.requirements.filter((x) => (!cat || x.category === cat) && (!q || (x.requirement_id + x.normalised_requirement).toLowerCase().includes(q)) &&
      (!st || bidders.some((b) => byKey[x.requirement_id]?.[b]?.status === st)));
    $("#mtable").innerHTML = `<tr><th>Requirement</th><th>Government value</th>${bidders.map((b) => `<th>${esc(b)}</th>`).join("")}</tr>` +
      reqs.map((x) => `<tr><td><b>${esc(x.requirement_id)}</b> <span class="muted">${esc(x.category)}</span><br>${esc(x.title)}</td>
        <td><code>${esc(x.evaluation_rule.op)} ${esc(x.expected_value || x.evaluation_rule.value || (x.evaluation_rule.permitted_values || []).join(", ") || "")}</code></td>
        ${bidders.map((b) => { const res = byKey[x.requirement_id]?.[b]; if (!res) return "<td></td>";
          const v = res.evidence.map((e) => e.raw_value).filter(Boolean).join(" | ");
          return `<td class="click" onclick="finding(${res.id})">${pill(res.status)}${res.reviewed ? " ✔" : ""}<div class="muted">${esc(v).slice(0, 60)}</div></td>`; }).join("")}</tr>`).join("");
  };
  ["#mq", "#mcat", "#mst"].forEach((s) => $(s).oninput = draw); draw();
}

function finding(id) {
  const r = state.report, res = r.results.find((x) => x.id === id), q = r.requirements.find((x) => x.requirement_id === res.requirement_key);
  const ev = res.evidence.map((e) => `<tr><td>${e.evidence_type === "ai_inferred" ? '<span class="tag ai">AI-extracted</span>' : '<span class="tag fact">extracted</span>'} ${esc(e.evidence_type.replaceAll("_", " "))}</td>
    <td><b>${esc(e.raw_value)}</b>${e.value !== null && e.value !== e.raw_value ? ` → ${esc(e.value)} ${esc(e.unit || "")}` : ""}</td>
    <td><a onclick='openRegions(${e.source_document_id}, ${JSON.stringify(e.source_regions)}, ${JSON.stringify(res.bidder + ": " + (e.source_label || ""))})'>${esc(e.source_label || "source")} ${e.source_page ? "p." + e.source_page : ""}</a>
    <div class="muted">${esc(e.claim_text).slice(0, 220)}</div></td><td class="muted">${esc(e.extraction_method)}</td></tr>`).join("");
  $("#finding").innerHTML = `<div class="card"><h3>${esc(res.bidder)} · ${esc(q.requirement_id)} ${esc(q.title)}</h3>
    <p><span class="tag fact">tender text</span> <a onclick='openRegions(${q.source_document_id}, ${JSON.stringify(q.source_regions)}, "Tender requirement")'>p.${q.source_page ?? "?"} clause ${esc(q.source_clause || "")}</a>
    <span class="muted">“${esc(q.original_clause_text).slice(0, 400)}”</span></p>
    <p><span class="tag rule">rule ${esc(q.evaluation_rule.version)}</span> <code>${esc(JSON.stringify(res.calc.rule))}</code></p>
    <p>Proposed: ${pill(res.auto_status)} ${esc(res.explanation)}</p>
    <table><tr><th>Evidence type</th><th>Value</th><th>Source</th><th>Method</th></tr>${ev || "<tr><td colspan=4 class='muted'>No evidence found</td></tr>"}</table>
    <p>Final decision: ${res.reviewed ? `${pill(res.final_status)} by ${esc(res.reviewer)} — ${esc(res.review_reason)}` : "<i>pending human review</i>"}</p>
    <select id="dec">${["MEETS_REQUIREMENT", "DOES_NOT_MEET_REQUIREMENT", "INSUFFICIENT_EVIDENCE", "REQUIRES_HUMAN_REVIEW", "NOT_APPLICABLE"].map((s) => `<option ${s === res.auto_status ? "selected" : ""}>${s}</option>`).join("")}</select>
    <input id="decwhy" placeholder="reason (required)" size="40"> <button class="primary" onclick="decide(${res.id})">Record decision</button></div>`;
  $("#finding").scrollIntoView({ behavior: "smooth" });
}

async function decide(id) {
  try { await api(`/api/results/${id}/decision`, { method: "POST", body: JSON.stringify({ final_status: $("#dec").value, reason: $("#decwhy").value }) });
    await selectRun(state.run); state.tab = "matrix"; render(); finding(id); } catch (e) { alert(e.message); }
}

// ---------------------------------------------------------------- commercial
function commercial() {
  const r = state.report; if (!r) return overview();
  const bidders = r.bidders.map((b) => b.name), items = {};
  r.prices.forEach((p) => ((items[p.sl] ||= { item: p, by: {} }).by[p.bidder] = p));
  const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 }));
  $("#content").innerHTML = `<div class="notice">Benchmarks are compared only when technical attributes do not conflict. “Partial” references differ in scope
    (e.g. material-only rate vs supply-and-fix item) and are indicative, not a basis for rejection. No tax, escalation or location adjustment is applied without a documented basis.</div>
    <table><tr><th>Sl</th><th>BOQ item</th><th>Qty</th><th>Benchmark</th>${bidders.map((b) => `<th>${esc(b)} rate</th>`).join("")}</tr>
    ${Object.values(items).map(({ item, by }) => { const bm = item.benchmark, sum = bm.summary;
      const refs = [...bm.comparable.map((e) => ({ ...e, kind: "comparable" })), ...bm.partial.map((e) => ({ ...e, kind: "partial" }))].slice(0, 4);
      return `<tr><td><a onclick="openNode(${item.tender_node_id})">${esc(item.sl)}</a></td>
      <td style="max-width:380px">${esc(item.description).slice(0, 180)}${item.description.length > 180 ? "…" : ""}</td><td>${fmt(item.qty)} ${esc(item.unit)}</td>
      <td>${sum ? `<b>${fmt(sum.median)}</b> <span class="muted">(${esc(sum.basis)}, n=${sum.n})</span><br>` : ""}
        ${refs.map((e) => `<div><span class="tag">${e.kind}</span><a onclick='openBench(${e.benchmark_id})'>${esc(e.code)}</a> ${fmt(e.normalised_rate)} <span class="muted">${esc(e.source_type)} ${esc(e.date || "")}</span>
          ${(e.notes || []).map((n) => `<div class="flag">${esc(n)}</div>`).join("")}${(e.steps || []).filter((s) => s.startsWith("unit")).map((s) => `<div class="muted">${esc(s)}</div>`).join("")}</div>`).join("")}
        ${bm.excluded.length ? `<details><summary class="muted">${bm.excluded.length} excluded</summary>${bm.excluded.slice(0, 5).map((e) => `<div class="muted">${esc(e.code)}: ${esc(e.reason)}</div>`).join("")}</details>` : ""}
        ${!refs.length && !bm.excluded.length ? '<span class="muted">no comparable reference</span>' : ""}</td>
      ${bidders.map((b) => { const p = by[b]; if (!p) return "<td></td>";
        const ratio = p.ratio_to_benchmark ? `<div class="${p.ratio_to_benchmark > 1.25 ? "flag" : "muted"}">${(p.ratio_to_benchmark * 100).toFixed(0)}% of benchmark</div>` : "";
        return `<td>${p.node_id ? `<a onclick="openNode(${p.node_id}, '${p.quoted_column}')">${fmt(p.quoted_rate)}</a>` : fmt(p.quoted_rate)}${ratio}</td>`; }).join("")}</tr>`; }).join("")}</table>`;
}

async function openBench(id) {
  const all = await api(`/api/cases/${state.case.id}/benchmarks?limit=100000`);
  const b = all.items.find((x) => x.id === id);
  if (b) openRegions(b.doc_id, b.source_regions, `Benchmark ${b.code}`);
}

async function benchmarks() {
  $("#content").innerHTML = `<div class="filters"><input id="bq" placeholder="search code or description"></div><div id="btable"></div>`;
  const draw = async () => {
    const d = await api(`/api/cases/${state.case.id}/benchmarks?q=${encodeURIComponent($("#bq").value)}&limit=300`);
    $("#btable").innerHTML = `<p class="muted">${d.total} records</p><table><tr><th>Code</th><th>Description (normalised)</th><th>Unit</th><th>Rate</th><th>Type</th><th>Tax basis</th><th>Source</th></tr>` +
      d.items.map((b) => `<tr><td>${esc(b.code)}</td><td>${esc(b.normalised_description)}<div class="muted">${esc(JSON.stringify(b.attributes))}</div></td><td>${esc(b.unit)}</td>
      <td>${b.rate.toLocaleString("en-IN")}</td><td>${esc(b.source_type)}</td><td class="muted">${esc(b.tax_basis || "not stated")}</td>
      <td><a onclick='openRegions(${b.doc_id}, ${JSON.stringify(b.source_regions)}, "Benchmark ${esc(b.code)}")'>${b.source_page ? "p." + b.source_page : "row"}</a></td></tr>`).join("") + "</table>";
  };
  let t; $("#bq").oninput = () => { clearTimeout(t); t = setTimeout(draw, 250); }; draw();
}

async function audit() {
  const rows = await api("/api/audit?limit=300");
  $("#content").innerHTML = `<table><tr><th>Time</th><th>Actor</th><th>Action</th><th>Entity</th><th>Detail</th></tr>` +
    rows.map((a) => `<tr><td>${a.ts.slice(0, 19)}</td><td>${esc(a.actor)}</td><td>${esc(a.action)}</td><td>${esc(a.entity)} ${esc(a.entity_id)}</td><td class="muted">${esc(JSON.stringify(a.detail)).slice(0, 200)}</td></tr>`).join("") + "</table>";
}

// ------------------------------------------------------------- source viewer
const viewer = { doc: null, pdf: null, page: 1, regions: [] };
$("#prev").onclick = () => viewer.pdf && viewer.page > 1 && renderPage(viewer.page - 1);
$("#next").onclick = () => viewer.pdf && viewer.page < viewer.pdf.numPages && renderPage(viewer.page + 1);
$("#vsearch").onchange = async () => {
  if (!viewer.doc) return;
  const hits = await api(`/api/documents/${viewer.doc.id}/nodes?q=${encodeURIComponent($("#vsearch").value)}&limit=50`);
  $("#vnote").innerHTML = hits.length ? hits.map((n) => `<div><a onclick='openRegions(${n.doc_id}, ${JSON.stringify(n.regions)}, "search hit")'>${n.page ? "p." + n.page : esc(n.ref || n.kind)}</a> ${esc(n.text).slice(0, 120)}</div>`).join("") : "no matches";
};

async function openNode(id, col) {
  const n = await api(`/api/nodes/${id}`);
  openRegions(n.doc_id, n.regions.map((g) => ({ ...g, col_letter: col })), n.ref || n.kind);
}
async function openDoc(id) { openRegions(id, [], ""); }

async function openRegions(docId, regions, label) {
  if (docId == null) return;
  if (!viewer.doc || viewer.doc.id !== docId) {
    viewer.doc = await api(`/api/documents/${docId}`); viewer.pdf = null;
    if (viewer.doc.kind === "pdf") viewer.pdf = await pdfjsLib.getDocument(`/api/documents/${docId}/file`).promise;
  }
  viewer.regions = regions || [];
  $("#vtitle").textContent = `${viewer.doc.filename}${label ? " — " + label : ""}`;
  $("#vdownload").href = `/api/documents/${docId}/file`;
  $("#vnote").textContent = "";
  if (viewer.doc.kind === "pdf") {
    const pages = viewer.regions.map((g) => g.page).filter(Boolean);
    if (viewer.regions.length && viewer.regions.every((g) => !g.bbox)) $("#vnote").textContent = "No precise region available for this item; showing the page only.";
    await renderPage(pages[0] || 1);
  } else if (["xls", "xlsx", "csv"].includes(viewer.doc.kind)) renderSheet();
  else renderParagraphs();
}

async function renderPage(n) {
  viewer.page = n;
  const page = await viewer.pdf.getPage(n);
  const body = $("#vbody"), base = page.getViewport({ scale: 1 });
  const scale = Math.min(2, (body.clientWidth - 20) / base.width);
  const vp = page.getViewport({ scale });  // applies the page's /Rotate, like our stored coordinates
  body.innerHTML = `<div class="pagewrap" style="width:${vp.width}px"><canvas></canvas></div>`;
  const canvas = body.querySelector("canvas"), dpr = window.devicePixelRatio || 1;
  canvas.width = vp.width * dpr; canvas.height = vp.height * dpr; canvas.style.width = vp.width + "px"; canvas.style.height = vp.height + "px";
  await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp, transform: [dpr, 0, 0, dpr, 0, 0] }).promise;
  const wrap = body.querySelector(".pagewrap");
  let first = null;
  viewer.regions.filter((g) => g.page === n && g.bbox).forEach((g) => {
    const [x0, y0, x1, y1] = g.bbox, d = document.createElement("div");
    d.className = "hl"; Object.assign(d.style, { left: x0 * scale + "px", top: y0 * scale + "px", width: (x1 - x0) * scale + "px", height: (y1 - y0) * scale + "px" });
    wrap.appendChild(d); first ||= d;
  });
  $("#vpage").textContent = `${n} / ${viewer.pdf.numPages}`;
  if (first) first.scrollIntoView({ block: "center" });
}

async function renderSheet() {
  const rows = await api(`/api/documents/${viewer.doc.id}/nodes?kind=row&limit=2000`);
  const hit = viewer.regions[0] || {};
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r.meta.cells || {})))].sort((a, b) => a.length - b.length || a.localeCompare(b)).slice(0, 30);
  $("#vpage").textContent = hit.sheet ? `${hit.sheet} row ${hit.row ?? ""}` : "";
  $("#vbody").innerHTML = `<table class="sheet"><tr><th>#</th>${cols.map((c) => `<th>${c}</th>`).join("")}</tr>` + rows.map((r) => {
    const on = r.meta.row === hit.row && (!hit.sheet || r.regions[0]?.sheet === hit.sheet);
    return `<tr class="${on ? "hit" : ""}" id="${on ? "sheethit" : ""}"><td>${r.meta.row}</td>${cols.map((c) => `<td class="${on && hit.col_letter === c ? "hitcell" : ""}" title="${esc(r.meta.cells[c])}">${esc(r.meta.cells[c] || "")}</td>`).join("")}</tr>`;
  }).join("") + "</table>";
  (document.querySelector(".sheet td.hitcell") || document.getElementById("sheethit"))?.scrollIntoView({ block: "center", inline: "center" });
}

async function renderParagraphs() {
  const nodes = await api(`/api/documents/${viewer.doc.id}/nodes?limit=2000`);
  const want = new Set(viewer.regions.map((g) => g.paragraph));
  $("#vnote").textContent = "DOCX: positions are paragraph references (no reliable page coordinates).";
  $("#vbody").innerHTML = nodes.map((n) => `<p class="${n.regions.some((g) => want.has(g.paragraph)) ? "hl-p" : ""}" style="${n.regions.some((g) => want.has(g.paragraph)) ? "background:#fff4c2" : ""}">${esc(n.text)}</p>`).join("");
}

init().catch((e) => ($("#content").innerHTML = `<p class="flag">${esc(e.message)}</p>`));

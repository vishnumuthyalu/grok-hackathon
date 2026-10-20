/* TrialRadar clinician UI (wireframe v4.1 layout). The session is shared with the MCP server via SSE. */
const $ = (id) => document.getElementById(id);
let state = null;
let revision = -1;
let markers = [];
let editingBio = -1;
let chipText = "";
let timer = null;
let ignoreForm = false;
let collapsed = false;          // the open trial is the session's selected trial; this lets the clinician fold it locally
let prevPct = {};
let modalText = "";
let modalFile = "trialradar-draft.txt";
let map = null;
let mapLayer = null;
const reduceMotion = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

/* Time saved: PLACEHOLDER constants from the wireframe, not sourced benchmarks. */
const ASSUMPTION_MIN_SEARCH_SETUP = 15;
const ASSUMPTION_MIN_PER_TRIAL_SCREEN = 5;
const ASSUMPTION_MIN_PER_CRITERION = 2;
const ASSUMPTION_MIN_REVIEW_PER_RESULT = 1;
const STRONG_MATCH_PCT = 70;
const EVIDENCE_TIER = {
  "Phase 1": { lvl: "Early", bars: 1 }, "Phase 1/2": { lvl: "Early", bars: 1 },
  "Phase 2": { lvl: "Mid", bars: 2 }, "Phase 2/3": { lvl: "Mid", bars: 2 },
  "Phase 3": { lvl: "Late", bars: 3 }, "Phase 4": { lvl: "Late", bars: 3 },
};
const RESULTS_PLACEHOLDER = "Results: not linked yet";

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

async function api(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

/* ===================== PATIENT FORM (rail) ===================== */
function patientFromForm() {
  const num = (id) => {
    const raw = $(id).value.trim();
    if (!raw) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  };
  const priors = $("priors").value.split(";").map((part) => part.trim()).filter(Boolean);
  const p = (state && state.patient) || {};
  return {
    id: p.id || null,
    age: num("age"),
    sex: $("sex").value || null,
    cancer_type: $("cancer").value.trim() || null,
    stage: $("stage").value.trim() || null,
    ecog: num("ecog"),
    biomarkers: markers.slice(),
    prior_treatments: priors,
    city: $("city").value.trim() || null,
    last_scan_days_ago: num("scan"),
    notes: $("notes").value.trim() || null,
    brain_mets: p.brain_mets ?? null,
    kidney_adequate: p.kidney_adequate ?? null,
    prior_immunotherapy: p.prior_immunotherapy ?? null,
    measurable_disease: p.measurable_disease ?? null,
    line_of_therapy: p.line_of_therapy ?? null,
  };
}

function fillForm(patient) {
  ignoreForm = true;
  const p = patient || {};
  $("age").value = p.age ?? "";
  $("sex").value = p.sex || "";
  $("cancer").value = p.cancer_type || "";
  $("stage").value = p.stage || "";
  $("ecog").value = p.ecog ?? "";
  $("priors").value = (p.prior_treatments || []).join("; ");
  $("city").value = p.city || "";
  $("scan").value = p.last_scan_days_ago ?? "";
  $("notes").value = p.notes || "";
  markers = (p.biomarkers || []).slice();
  editingBio = -1;
  renderBioEditor();
  ignoreForm = false;
}

function renderBioEditor() {
  const ed = $("bio-ed");
  ed.innerHTML = markers.map((b, i) => (i === editingBio
    ? `<span class="bchip"><input data-bio-edit="${i}" value="${esc(b)}" aria-label="Edit biomarker"></span>`
    : `<span class="bchip"><button type="button" data-bio="${i}" aria-label="Edit ${esc(b)}">${esc(b)}</button><button type="button" class="x" data-bio-x="${i}" aria-label="Remove ${esc(b)}">×</button></span>`)).join("")
    + `<input class="add" id="bio-add" placeholder="+ add" value="${esc(chipText)}" aria-label="Add biomarker">`;
  const edit = ed.querySelector("[data-bio-edit]");
  if (edit) { edit.focus(); edit.select(); }
}

function commitBioEdit(input) {
  const i = Number(input.dataset.bioEdit);
  const v = input.value.trim();
  if (v) markers[i] = v; else markers.splice(i, 1);
  editingBio = -1;
  renderBioEditor();
  schedule(false);
}

function schedule(searchAfter) {
  clearTimeout(timer);
  timer = setTimeout(() => savePatient(searchAfter), 320);
}

async function savePatient(searchAfter) {
  const patient = patientFromForm();
  try {
    const data = searchAfter
      ? await api("/api/search", { patient, live: false })
      : await api("/api/rescore", { patient, answers: answers(), what_ifs: whatIfs() });
    apply(data, false);
  } catch (error) {
    showBanner(error.message);
  }
}

function phases() {
  return ["1", "2", "3"].filter((digit) => $("ph" + digit).checked);
}
function answers() {
  return state && state.answers ? { ...state.answers } : {};
}
function whatIfs() {
  const out = {};
  for (const item of (state && state.what_ifs) || []) out[item.id] = !!item.on;
  return out;
}

/* ===================== APPLY / RENDER ===================== */
function apply(data, external) {
  if (!data) return;
  if (data.revision < revision) {
    const next = data.updated_at || "";
    const previous = (state && state.updated_at) || "";
    if (!next || next <= previous) return;
  }
  const prevSel = state && state.selected && state.selected.nct_id;
  revision = data.revision;
  state = data;
  if ((data.selected && data.selected.nct_id) !== prevSel) collapsed = false;
  if (external) fillForm(data.patient);
  render(data);
}

function render(data) {
  const banner = $("banner");
  if (data.warning || data.error) {
    setBanner(data.error || data.warning);
  } else {
    banner.hidden = true;
  }
  const when = data.retrieved_at ? timeAgo(data.retrieved_at) : "not yet";
  $("source-line").textContent = `Source: ${data.source_label || "ClinicalTrials.gov"} · checked ${when}`;
  $("mode-pill").textContent = data.demo_cache ? "Recorded demo data (offline)" : "Live ClinicalTrials.gov";
  const d = data.filters.distance_mi;
  document.querySelectorAll("#dist-seg [data-d]").forEach((b) => {
    const v = Number(b.dataset.d);
    b.classList.toggle("on", v === (d <= 25 ? 25 : d <= 100 ? 100 : 250));
  });
  for (const digit of ["1", "2", "3"]) $("ph" + digit).checked = (data.filters.phases || []).includes(digit);
  $("recruiting").classList.toggle("on", !!data.filters.recruiting_only);
  $("recruiting").setAttribute("aria-pressed", data.filters.recruiting_only ? "true" : "false");
  renderWhatIfs(data);
  renderPatientBar(data);
  renderQuestions(data);
  renderTrials(data);
  renderUnlock(data);
  renderSaved(data);
  updateMap(data);
}

function renderPatientBar(data) {
  const p = data.patient || {};
  const chips = [];
  if (p.age || p.sex) chips.push(`${p.age ?? ""}${p.sex || ""}`);
  if (p.cancer_type) chips.push(`${shortCancer(p.cancer_type)}${p.stage ? " " + p.stage : ""}`);
  for (const b of p.biomarkers || []) chips.push(b);
  if (p.city) chips.push(p.city);
  $("patient-chips").innerHTML = chips.map((c) => `<span class="chip">${esc(c)}</span>`).join("");
  $("pt-id").textContent = p.id ? `${String(p.id).slice(0, 3).toUpperCase()}•••` : "———•••";
  $("pt-src").textContent = data.patient_source === "mcp" ? "From AI assistant" : p.id ? "Patient record" : "No patient";
}

function shortCancer(text) {
  const t = String(text);
  if (/non-small cell lung/i.test(t)) return "NSCLC";
  if (/colorectal/i.test(t)) return "CRC";
  return t.length > 24 ? t.slice(0, 22) + "…" : t;
}

function renderWhatIfs(data) {
  $("whatifs").innerHTML = (data.what_ifs || []).map((item) => `
    <div class="row-ctl" title="${esc(item.detail)}"><span>${esc(item.label)}${item.hypothetical ? ` <span class="preview" style="border-style:solid">Hypothetical</span>` : ""}</span>
      <button type="button" class="tg ${item.on ? "on" : ""}" data-whatif="${esc(item.id)}" aria-pressed="${item.on}" aria-label="${esc(item.label)}"></button></div>
    <div class="muted" style="margin-top:-4px">${esc(item.detail)}</div>`).join("");
}

function renderQuestions(data) {
  const items = data.questions || [];
  $("q-sub").textContent = items.length
    ? `${data.questions_open || 0} open · each answer re-ranks the list`
    : "";
  if (!items.length) {
    $("questions").innerHTML = `<div class="muted">Open questions show up here after trials are checked.</div>`;
    return;
  }
  $("questions").innerHTML = items.map((q) => `
    <div class="q"><p>${esc(q.text)}<small>Affects ${q.affects} trial${q.affects === 1 ? "" : "s"}</small></p>
      <div class="seg" role="group" aria-label="${esc(q.text)}">${["yes", "no", "unknown"].map((v) => `<button type="button" class="t ${q.answer === v ? "on" : ""}" data-answer="${esc(q.id)}:${v}" aria-pressed="${q.answer === v}">${v === "unknown" ? "Unknown" : v[0].toUpperCase() + v.slice(1)}</button>`).join("")}</div></div>`).join("");
}

function renderUnlock(data) {
  const open = (data.questions || []).filter((q) => !q.answer || q.answer === "unknown");
  const items = open.map((q) => `${esc(q.text.replace(/\?$/, ""))} (${q.affects} trial${q.affects === 1 ? "" : "s"})`);
  $("unlock").innerHTML = `<b>What would unlock more trials:</b> ${items.length ? items.join(" · ") : "nothing open right now"}`;
}

function totalRules(data) {
  return (data.ranked || []).reduce((a, r) => a + (r.met || 0) + (r.failed || 0) + (r.unknown || 0), 0);
}

function renderSaved(data) {
  const trials = data.checked_count || 0;
  const criteria = totalRules(data);
  const shown = (data.ranked || []).length;
  const manual = trials ? ASSUMPTION_MIN_SEARCH_SETUP + trials * ASSUMPTION_MIN_PER_TRIAL_SCREEN + criteria * ASSUMPTION_MIN_PER_CRITERION : 0;
  const withTool = shown * ASSUMPTION_MIN_REVIEW_PER_RESULT;
  const mins = Math.max(0, manual - withTool);
  $("saved-hrs").textContent = `~${(mins / 60).toFixed(1)} hrs`;
  $("formula-text").textContent = `manual   = SEARCH_SETUP + trials × MIN_PER_TRIAL + criteria × MIN_PER_CRITERION
with tool = results shown × MIN_REVIEW_PER_RESULT
SEARCH_SETUP          = ${ASSUMPTION_MIN_SEARCH_SETUP} min  (placeholder)
MIN_PER_TRIAL         = ${ASSUMPTION_MIN_PER_TRIAL_SCREEN} min  (placeholder)
MIN_PER_CRITERION     = ${ASSUMPTION_MIN_PER_CRITERION} min  (placeholder)
MIN_REVIEW_PER_RESULT = ${ASSUMPTION_MIN_REVIEW_PER_RESULT} min  (placeholder)`;
  $("formula-live").innerHTML = `This search: <b>${trials}</b> trials, <b>${criteria}</b> criteria checked → manual ≈ ${manual} min, with tool ≈ ${withTool} min, saved ≈ <b>${mins} min</b>.`;
}

/* ===================== TRIAL LIST ===================== */
function pct(row) {
  const total = (row.met || 0) + (row.failed || 0) + (row.unknown || 0);
  return total ? Math.round((100 * row.met) / total) : 0;
}
function phaseShort(label) {
  return String(label || "").replace(/Early Phase 1/i, "P1").replace(/Phase /gi, "P").replace(/\s*\/\s*P?/g, "/") || "—";
}
function drugShort(row) {
  const names = (row.interventions || []).filter((n) => !/placebo|standard of care|chemotherapy$/i.test(n));
  let first = names[0] || row.interventions?.[0] || "";
  first = first.replace(/\s*\(.*?\)\s*/g, " ").trim();
  if (first.length > 26) first = first.slice(0, 24) + "…";
  return first + (names.length > 1 ? ` + ${names.length - 1}` : "");
}
function trialName(row) {
  const d = drugShort(row);
  return `${phaseShort(row.phase_label)}${d ? " · " + d : ""}`;
}
function flags(row) {
  return (row.rules || []).filter((r) => r.status === "not_met").map((r) => r.reason || r.text);
}
function nextStep(row, data) {
  const fails = (row.rules || []).filter((r) => r.status === "not_met");
  const hard = fails.find((r) => !r.reversible);
  if (hard) return { text: `Skip for now: ${row.blocking || hard.reason || hard.text}`, hard: true };
  const fix = fails[0];
  if (fix) return { text: fix.reason || fix.text, hard: false };
  const qs = new Set((row.rules || []).filter((r) => r.status === "unknown" && r.question_id).map((r) => r.question_id));
  const open = (data.questions || []).filter((q) => qs.has(q.id) && (!q.answer || q.answer === "unknown"));
  if (open.length) return { text: open.map((q) => `Confirm: ${q.text}`).join(" · "), hard: false };
  const rest = row.unknown || 0;
  const site = (row.nearest_label || "nearest").split(",")[0];
  return { text: `Refer to ${site} site` + (rest ? ` · ${rest} item${rest > 1 ? "s" : ""} to confirm at screening` : ""), hard: false };
}

function hexToRgb(h) { h = h.trim().replace("#", ""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); }
function scaleColor(p) {
  const cs = getComputedStyle(document.documentElement);
  const L = hexToRgb(cs.getPropertyValue("--elig-low")), M = hexToRgb(cs.getPropertyValue("--elig-mid")), H = hexToRgb(cs.getPropertyValue("--elig-high"));
  const mix = (a, b, t) => a.map((x, i) => Math.round(x + (b[i] - x) * t));
  const c = p <= 50 ? mix(L, M, p / 50) : mix(M, H, (p - 50) / 50);
  return `rgb(${c.join(",")})`;
}
function donut(p) {
  const r = 16, C = 2 * Math.PI * r, col = scaleColor(p);
  return `<svg width="42" height="42" viewBox="0 0 42 42" aria-hidden="true">
    <circle cx="21" cy="21" r="${r}" fill="none" stroke="var(--track)" stroke-width="7"/>
    <circle cx="21" cy="21" r="${r}" fill="none" stroke="${col}" stroke-width="7"
      stroke-dasharray="${((C * p) / 100).toFixed(2)} ${C.toFixed(2)}" transform="rotate(-90 21 21)"/>
  </svg>`;
}
function sevHTML(row) {
  const e = EVIDENCE_TIER[row.phase_label] || { lvl: "—", bars: 0 };
  return `<span class="sev" title="Study evidence: based on trial phase. ${RESULTS_PLACEHOLDER}.">
    <span class="bars" aria-hidden="true">${[1, 2, 3].map((i) => `<i class="${i <= e.bars ? "on" : ""}"></i>`).join("")}</span>
    <span class="lvl">${e.lvl}</span><span class="sub">${RESULTS_PLACEHOLDER}</span></span>`;
}
function distHTML(row) {
  const city = (row.nearest_label || "Site").replace(/, (Texas)$/, ", TX");
  return { mi: miles(row.nearest_distance_mi), city };
}

function renderTrials(data) {
  const box = $("trials");
  const rows = data.ranked || [];
  const pend = data.unchecked || [];
  // keep open rule groups across re-renders
  const openGroups = {};
  document.querySelectorAll(".trial.open details.grp").forEach((d) => { openGroups[d.dataset.g] = d.open; });
  const scrollKeep = window.scrollY;
  if (!rows.length && !pend.length) {
    box.innerHTML = `<div class="empty">Load an example (top bar) or enter a diagnosis and a city in the filters panel, then find trials.</div>`;
  } else {
    box.innerHTML = rows.map((row, i) => rowHTML(row, i, rows, data, openGroups)).join("") + pend.map(pendingHTML).join("");
  }
  rows.forEach((row) => {
    const p = pct(row);
    if (prevPct[row.nct_id] !== undefined && prevPct[row.nct_id] !== p && !reduceMotion) {
      const el = box.querySelector(`.trial[data-nct="${row.nct_id}"]`);
      if (el) { el.classList.add("changed"); setTimeout(() => el.classList.remove("changed"), 900); }
    }
    prevPct[row.nct_id] = p;
  });
  window.scrollTo(0, scrollKeep);
  const d = data.filters.distance_mi;
  $("list-title").textContent = `${rows.length} matching trial${rows.length === 1 ? "" : "s"}`;
  const tier = d <= 25 ? "Local (≤25 mi)" : d <= 100 ? `Regional (≤${d} mi)` : `Wide (≤${d} mi)`;
  const bits = [`${tier} · ranked by fit`, `${data.found_count || 0} found`];
  if (pend.length) bits.push(`${pend.length} not checked yet`);
  $("list-sub").textContent = bits.join(" · ");
  const more = data.unchecked_count || 0;
  $("more-label").textContent = more
    ? `${data.checked_count || 0} checked · ${more} more trial${more === 1 ? "" : "s"} within ${d} mi not yet checked`
    : "All matching trials in this radius are checked";
  $("check-next").style.visibility = more ? "visible" : "hidden";
}

function pendingHTML(row) {
  const dist = distHTML(row);
  return `<div class="trial pending" data-nct="${esc(row.nct_id)}">
    <div class="trow" aria-label="${esc(trialName(row))}, not checked yet">
      <span class="chev" aria-hidden="true"></span>
      <span style="min-width:0">
        <div class="tname">${esc(trialName(row))}</div>
        <div class="tsum">${esc(row.brief_title)}</div>
        <div class="dist-inline">${dist.mi} · ${esc(dist.city)}</div>
        <div class="pend-note">Eligibility not checked yet</div>
      </span>
      <span class="dist"><b>${dist.mi}</b><div>${esc(dist.city)}</div></span>
      <span class="right">
        <span class="nc"><span class="ring" aria-hidden="true">–</span>Not checked yet</span>
        ${sevHTML(row)}
      </span>
    </div>
  </div>`;
}

function rowHTML(row, i, rows, data, openGroups) {
  const sel = data.selected && data.selected.nct_id === row.nct_id;
  const open = sel && !collapsed;
  const trial = sel ? data.selected : row;   // the selected trial carries the freshest detail
  const p = pct(row);
  const fl = flags(trial);
  let cue = "";
  if (fl.length) cue = `<span class="cue flag" aria-label="Flagged criterion">!<span class="tip">${fl.map(esc).join("<br>")}</span></span>`;
  else if (p >= STRONG_MATCH_PCT) cue = `<span class="cue ok" aria-label="Strong match">✓</span>`;
  const step = nextStep(trial, data);
  const dist = distHTML(row);
  const other = rows.find((r) => r.nct_id !== row.nct_id);
  const total = (row.met || 0) + (row.failed || 0) + (row.unknown || 0);
  return `<div class="trial ${open ? "open" : ""}" data-nct="${esc(row.nct_id)}">
    <button type="button" class="trow t" data-toggle="${esc(row.nct_id)}" aria-expanded="${open}"
      aria-label="#${row.rank} ${esc(trialName(row))}, ${p}% match, ${dist.mi}, ${esc(dist.city)}. Next step: ${esc(step.text)}">
      <span class="chev" aria-hidden="true">▶</span>
      <span style="min-width:0">
        <div class="tname">#${row.rank} · ${esc(trialName(row))}</div>
        <div class="tsum" title="${esc(row.brief_title)}">${esc(row.brief_title)}</div>
        <div class="dist-inline">${dist.mi} · ${esc(dist.city)}</div>
        <div class="next ${step.hard ? "skip" : ""}"><b>Next</b><span class="arr" aria-hidden="true">→</span><span>${esc(step.text)}</span></div>
        <span class="vbw">${verifyBadge(row.verification, data)}</span>
      </span>
      <span class="dist"><b>${dist.mi}</b><div>${esc(dist.city)}</div></span>
      <span class="right">
        <span class="elig" title="${row.met} of ${total} criteria met · ${row.failed} not met · ${row.unknown} need info">${donut(p)}<span class="pct">${p}% match ${cue}</span></span>
        ${sevHTML(row)}
      </span>
    </button>
    ${open ? detailHTML(trial, data, openGroups, other) : ""}
  </div>`;
}

function detailHTML(trial, data, openGroups, other) {
  const rules = trial.rules || [];
  const fl = flags(trial);
  const total = rules.length;
  const met = rules.filter((r) => r.status === "met").length;
  const grp = (key, label, icon, isOpen) => {
    const items = rules.filter((r) => r.status === key);
    if (!items.length) return "";
    const o = key in openGroups ? openGroups[key] : isOpen;
    return `<details class="grp" data-g="${key}" ${o ? "open" : ""}><summary><span class="car" aria-hidden="true">▶</span>${icon}${label}<span class="ct">${items.length}</span></summary><ul>${items.map(ruleLi).join("")}</ul></details>`;
  };
  const gi = (bg, ch) => `<span class="gicon" style="background:${bg};color:#1d1c1a" aria-hidden="true">${ch}</span>`;
  const sponsor = trial.provenance && trial.provenance.sponsor;
  return `<div class="detail">
    <p class="full-title">${esc(trial.brief_title)}</p>
    <div class="meta">
      <a href="${esc(trial.url)}" target="_blank" rel="noreferrer">${esc(trial.nct_id)} on ClinicalTrials.gov ↗</a>
      ${sponsor ? `<span>${esc(sponsor)}</span>` : ""}
      <span>${trial.overall_status === "RECRUITING" ? "Recruiting" : esc((trial.overall_status || "").toLowerCase().replace(/_/g, " "))}</span>
      <span>${met} of ${total} criteria met</span>
      <span>Nearest: ${esc(trial.nearest_facility || trial.nearest_label || "—")} · ${miles(trial.nearest_distance_mi)}</span>
    </div>
    ${provenanceBox(trial, data)}
    ${fl.length ? `<div class="notable"><span class="cue flag" aria-hidden="true">!</span><div>${fl.map(esc).join("<br>")}</div></div>` : ""}
    <div id="map" role="img" aria-label="Map of the patient and the trial sites"></div>
    <div class="groups">
      ${grp("not_met", "Not met", gi("var(--elig-low)", "✕"), true)}
      ${grp("unknown", "Needs info", gi("var(--elig-mid)", "?"), true)}
      ${grp("met", "Met", gi("var(--elig-high)", "✓"), false)}
    </div>
    ${data.summary_note ? `<div class="draft-preview"><h4>Draft from the AI assistant</h4><pre>${esc(data.summary_note)}</pre></div>` : ""}
    <div class="actions">
      <button type="button" class="btn t" id="referral" data-act="referral">Draft referral note</button>
      <button type="button" class="btn pri t" id="summary" data-act="summary">Patient-friendly summary</button>
      <button type="button" class="btn t" id="compare" data-act="compare" ${other ? "" : "disabled"}>Compare with #${other ? other.rank : ""}</button>
    </div>
  </div>`;
}

function ruleLi(rule) {
  const ev = rule.evidence && rule.evidence !== "no evidence" && rule.status !== "unknown"
    ? `<div class="ev">From record: <mark>${esc(rule.evidence)}</mark>${rule.hypothetical ? " · hypothetical" : ""}</div>` : "";
  return `<li>${rule.hypothetical ? `<div class="hypo">HYPOTHETICAL</div>` : ""}<span class="rt">${esc(rule.text)}</span>${rule.question_id && rule.status === "unknown" ? ` <span class="ql">· question above</span>` : ""}
    ${citeLine(rule)}<span class="why">${esc(rule.reason)}</span>${ev}</li>`;
}

function verifyBadge(ver, data) {
  if (!ver) return "";
  const cls = ver.score >= 90 ? "ok" : ver.score >= 60 ? "mid" : "low";
  const tip = `${ver.summary}\n\n${(data && data.verification_explanation) || ver.explanation || ""}`;
  return `<span class="vb ${cls}" title="${esc(tip)}">✓ ${esc(ver.label)} ${ver.score}/100</span>`;
}

function provenanceBox(trial, data) {
  const prov = trial.provenance;
  if (!prov) return "";
  const ver = trial.verification || {};
  return `<div class="prov">
    <div class="prov-h"><b>Source</b> ${verifyBadge(ver, data)}</div>
    <div>ClinicalTrials.gov record <code>${esc(prov.nct_id)}</code> ·
      <a class="link" href="${esc(prov.study_url)}" target="_blank" rel="noreferrer">study page ↗</a> ·
      <a class="link" href="${esc(prov.api_url)}" target="_blank" rel="noreferrer">API v2 record (JSON) ↗</a></div>
    <div class="s">Last updated on ClinicalTrials.gov: ${esc(prov.last_update || "not listed")} · ${esc(prov.retrieval_label || "")}</div>
    <div class="s">${esc(ver.summary || "")}. Score is computed by code from the record, not by AI. It checks the sources, not the trial's clinical quality.</div>
  </div>`;
}

function citeLine(rule) {
  const cite = rule.citation;
  if (!cite) return "";
  const where = cite.section ? `${esc(cite.section)} · ${esc(cite.nct_id)}` : `${esc(cite.nct_id)}`;
  const mark = cite.verified
    ? `<span class="cq ok" title="This rule text was found word-for-word (ignoring line breaks) in the stored ClinicalTrials.gov record.">✓ Verified quote</span>`
    : `<span class="cq bad" title="This text was not found word-for-word in the stored ClinicalTrials.gov record. Check the study page.">⚠ Not found in source</span>`;
  return `<div class="cite">${mark} <a class="link" href="${esc(cite.url)}" target="_blank" rel="noreferrer">[ClinicalTrials.gov · ${where}] ↗</a></div>`;
}

function updateMap(data) {
  const el = $("map");
  if (!el) { if (map) { map.remove(); map = null; } return; }
  if (!window.L || !data.origin || data.origin.lat == null) return;
  if (map && map.getContainer() !== el) { map.remove(); map = null; }
  if (!map) {
    map = L.map(el, { zoomControl: false, attributionControl: true, scrollWheelZoom: false });
    mapLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 12, attribution: "&copy; OpenStreetMap" }).addTo(map);
  }
  map.eachLayer((layer) => { if (layer !== mapLayer) map.removeLayer(layer); });
  const points = [[data.origin.lat, data.origin.lon]];
  L.circleMarker(points[0], { radius: 8, color: "#2b5797", fillColor: "#2b5797", fillOpacity: 0.9 }).bindTooltip(data.origin.label || "Patient").addTo(map);
  for (const site of (data.selected && data.selected.sites) || []) {
    points.push([site.lat, site.lon]);
    L.circleMarker([site.lat, site.lon], { radius: 6, color: "#a34a00", fillColor: "#a34a00", fillOpacity: 0.85 })
      .bindTooltip(`${site.facility || site.label || "Site"} · ${Math.round(site.distance_mi)} mi`).addTo(map);
  }
  map.fitBounds(points, { padding: [18, 18], maxZoom: 8 });
  setTimeout(() => map && map.invalidateSize(), 50);
}

function miles(value) {
  if (value == null || Number.isNaN(value)) return "—";
  if (value < 10) return `${Math.round(value * 10) / 10} mi`;
  return `${Math.round(value)} mi`;
}
function timeAgo(iso) {
  const then = new Date(iso).getTime();
  if (!then) return "just now";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return "just now";
  if (mins === 1) return "1 min ago";
  if (mins < 120) return `${mins} min ago`;
  return `${Math.round(mins / 60)} h ago`;
}
function showBanner(text) { setBanner(text); }
var bannerDismissed = null;
function setBanner(text) {
  const b = $("banner");
  if (bannerDismissed === text) { b.hidden = true; return; }
  b.hidden = false; b.setAttribute("role", "status"); b.textContent = "";
  const m = document.createElement("span"); m.className = "bmsg"; m.textContent = text;
  const x = document.createElement("button"); x.type = "button"; x.className = "bx"; x.textContent = "×";
  x.setAttribute("aria-label", "Dismiss"); x.onclick = () => { bannerDismissed = text; b.hidden = true; };
  b.append(m, x);
}
let toastTimer = null;
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

/* ===================== MODALS ===================== */
function modal(html, text, filename) {
  modalText = text || ""; modalFile = filename || "trialradar-draft.txt";
  const tools = text ? `<span class="muted">Copy or download only. Nothing is sent from TrialRadar.</span><button type="button" class="btn t" id="copy-note">Copy</button><button type="button" class="btn t" id="download-note">Download .txt</button>` : "";
  $("modal").innerHTML = html + `<div class="mbtns">${tools}<button type="button" class="btn pri t" id="close-modal">Close</button></div>`;
  $("modal-bg").classList.add("show");
  const ta = $("note-text"); if (ta) ta.scrollTop = 0;
}
function closeModal() {
  const bg = $("modal-bg");
  if (!bg.classList.contains("show")) return false;
  bg.classList.remove("show"); $("modal").innerHTML = ""; return true;
}

async function openNotes(kind) {
  const trial = state && state.selected;
  const data = await api("/api/notes", { nct_id: trial && trial.nct_id });
  const notes = data.notes;
  if (!notes) { showBanner(data.error || "Choose a checked trial first."); return; }
  const body = kind === "summary" ? notes.summary : notes.referral;
  const title = kind === "summary" ? "Patient-friendly summary" : `Draft referral note · ${trialName(trial)}`;
  modal(`<h3 id="modal-h">${esc(title)}</h3><div class="wf-note">Editable on this device. The clinician edits before use.</div><textarea id="note-text" aria-label="${esc(title)}">${esc(body)}</textarea>`,
    body, `${kind}-${trial.nct_id}.txt`);
}

function openCompare() {
  const rows = state.ranked || [];
  const left = state.selected;
  const right = rows.find((row) => row.nct_id !== (left && left.nct_id));
  if (!left || !right) return;
  const all = [left, right];
  const line = (lbl, f) => `<tr><th>${lbl}</th>${all.map((x) => `<td>${f(x)}</td>`).join("")}</tr>`;
  modal(`<h3 id="modal-h">Compare with #${right.rank}</h3><table class="cmp">
    ${line("Trial", (x) => `<b>#${x.rank} · ${esc(trialName(x))}</b><div class="muted">${esc(x.nct_id)}</div>`)}
    ${line("Eligibility", (x) => `${pct(x)}% match · ${x.met} met · ${x.failed} not met · ${x.unknown} need info`)}
    ${line("Nearest site", (x) => `${esc(x.nearest_label || "—")} · ${miles(x.nearest_distance_mi)}`)}
    ${line("Source", (x) => verifyBadge(x.verification, state))}
    ${line("Next step", (x) => esc(nextStep(x, state).text))}
    ${line("Not met", (x) => (x.rules || []).filter((r) => r.status === "not_met").map((r) => esc(r.reason || r.text)).join("<br>") || "—")}
  </table><p class="wf-note">${esc(state.disclaimer || "")}</p>`, "", "");
}

/* ===================== THEME / RAIL ===================== */
function syncThemeBtn(t) {
  const b = $("theme-btn"); b.setAttribute("aria-pressed", t === "dark");
  b.setAttribute("aria-label", t === "dark" ? "Switch to light mode" : "Switch to dark mode");
  $("theme-ico").textContent = t === "dark" ? "☀" : "☾";
}
function applyTheme(t) {
  document.documentElement.dataset.theme = t; syncThemeBtn(t);
  try { localStorage.setItem("trialradar-theme", t); } catch (e) { /* ignore */ }
  if (state) render(state);
}
function setRail(open) {
  const r = $("rail"), b = $("rail-toggle");
  r.classList.toggle("open", open); r.inert = !open; b.setAttribute("aria-expanded", open);
  if (!open && r.contains(document.activeElement)) b.focus();
}
const railOpen = () => $("rail").classList.contains("open");

/* ===================== EVENTS ===================== */
document.addEventListener("click", async (event) => {
  const t = event.target;
  try {
    const bx = t.closest("[data-bio-x]");
    if (bx) { markers.splice(Number(bx.dataset.bioX), 1); renderBioEditor(); schedule(false); return; }
    const bc = t.closest("[data-bio]");
    if (bc) { editingBio = Number(bc.dataset.bio); renderBioEditor(); return; }
    const ans = t.closest("[data-answer]");
    if (ans) {
      const [id, value] = ans.dataset.answer.split(":");
      const next = answers(); next[id] = value;
      apply(await api("/api/rescore", { answers: next, what_ifs: whatIfs() }), false);
      return;
    }
    const what = t.closest("[data-whatif]");
    if (what) {
      const fl = whatIfs(); fl[what.dataset.whatif] = !fl[what.dataset.whatif];
      apply(await api("/api/rescore", { what_ifs: fl, answers: answers() }), false);
      return;
    }
    const tg = t.closest("[data-toggle]");
    if (tg) {
      const nct = tg.dataset.toggle;
      if (state.selected && state.selected.nct_id === nct) { collapsed = !collapsed; render(state); return; }
      collapsed = false;
      apply(await api("/api/select", { nct_id: nct }), false);
      const el = document.querySelector(`.trial[data-nct="${nct}"]`);
      if (el) el.scrollIntoView({ block: "nearest", behavior: reduceMotion ? "auto" : "smooth" });
      return;
    }
    const dd = t.closest("[data-d]");
    if (dd) { apply(await api("/api/rescore", { distance_mi: Number(dd.dataset.d) }), false); return; }
    const sb = t.closest("[data-src]");
    if (sb) {
      document.querySelectorAll("#src-seg button").forEach((b) => b.classList.toggle("on", b === sb));
      $("photo-box").hidden = sb.dataset.src !== "photo";
      return;
    }
    const pv = t.closest("[data-preview]");
    if (pv) { toast(`${pv.dataset.preview} is a UI preview; not available yet.`); return; }
    if (t.id === "compare") { openCompare(); return; }
    if (t.id === "referral") { await openNotes("referral"); return; }
    if (t.id === "summary") { await openNotes("summary"); return; }
    if (t.closest("#close-modal") || t.id === "modal-bg") { closeModal(); return; }
    if (t.id === "copy-note") {
      await navigator.clipboard.writeText($("note-text").value);
      toast("Copied. Nothing was sent.");
      return;
    }
    if (t.id === "download-note") {
      const blob = new Blob([$("note-text") ? $("note-text").value : modalText], { type: "text/plain" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob); link.download = modalFile; link.click();
      return;
    }
    if (t.id === "scrim") { setRail(false); return; }
    const info = t.closest("#saved-info");
    if (info && !t.closest(".pop")) { info.classList.toggle("open"); return; }
    if (!t.closest(".pop")) $("saved-info").classList.remove("open");
  } catch (error) {
    showBanner(error.message);
  }
});

$("bio-ed").addEventListener("keydown", (e) => {
  if (e.target.id === "bio-add" && e.key === "Enter") {
    e.preventDefault();
    const v = e.target.value.trim();
    if (v) { markers.push(v); chipText = ""; renderBioEditor(); $("bio-add").focus(); schedule(false); }
  }
  if (e.target.dataset.bioEdit !== undefined) {
    if (e.key === "Enter") { e.preventDefault(); commitBioEdit(e.target); }
    if (e.key === "Escape") { editingBio = -1; renderBioEditor(); }
  }
});
$("bio-ed").addEventListener("input", (e) => { if (e.target.id === "bio-add") chipText = e.target.value; });
$("bio-ed").addEventListener("focusout", (e) => {
  if (e.target.dataset && e.target.dataset.bioEdit !== undefined && editingBio >= 0) commitBioEdit(e.target);
});
$("patient-form").addEventListener("input", (e) => {
  if (ignoreForm || e.target.closest("#bio-ed")) return;
  schedule(false);
});
$("find").addEventListener("click", () => savePatient(true));
$("check-next").addEventListener("click", async () => {
  try { apply(await api("/api/check", { next_batch: true, top_n: 5 }), false); } catch (error) { showBanner(error.message); }
});
$("refresh-live").addEventListener("click", async () => {
  try {
    apply(await api("/api/search", { patient: patientFromForm(), live: true }), true);
    toast("Searched ClinicalTrials.gov");
  } catch (error) { showBanner(error.message); }
});
for (const digit of ["1", "2", "3"]) {
  $("ph" + digit).addEventListener("change", async () => { apply(await api("/api/rescore", { phases: phases() }), false); });
}
$("recruiting").addEventListener("click", async () => {
  const on = !$("recruiting").classList.contains("on");
  apply(await api("/api/rescore", { recruiting_only: on }), false);
});
$("extract").addEventListener("click", async () => {
  const file = $("file").files[0];
  try {
    let data;
    if (file) {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch("/api/upload", { method: "POST", body });
      if (!response.ok) throw new Error(await response.text());
      data = await response.json();
    } else {
      data = await api("/api/extract", { note_text: $("paste").value });
    }
    apply(data, true);
    if (data.patient && data.patient.cancer_type && data.patient.city) {
      apply(await api("/api/search", { patient: data.patient }), true);
      apply(await api("/api/check", { top_n: 5 }), true);
    }
  } catch (error) { showBanner(error.message); }
});
$("examples").addEventListener("change", async () => {
  const id = $("examples").value;
  if (!id) return;
  try {
    apply(await api(`/api/examples/${id}`, {}), true);
    toast("Example loaded");
  } catch (error) { showBanner(error.message); }
  $("examples").value = "";
});
$("rail-toggle").addEventListener("click", () => setRail(!railOpen()));
$("theme-btn").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !closeModal()) {
    $("saved-info").classList.remove("open");
    if (railOpen() && innerWidth <= 900) setRail(false);
  }
});

async function boot() {
  syncThemeBtn(document.documentElement.dataset.theme || "light");
  const examples = await fetch("/api/examples").then((response) => response.json());
  for (const item of examples.examples) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label;
    $("examples").appendChild(option);
  }
  apply(await fetch("/api/session").then((response) => response.json()), true);
  const events = new EventSource("/api/events");
  events.onmessage = (message) => {
    const data = JSON.parse(message.data);
    if (!data || data.revision === revision) return;
    if (data.revision < revision && !((data.updated_at || "") > ((state && state.updated_at) || ""))) return;
    const drafted = data.summary_note && !(state && state.summary_note);
    const typing = document.activeElement && document.activeElement.closest("#patient-form, #photo-box");
    if (typing && data.patient_source !== "mcp") {
      revision = data.revision; state = data; render(data); return;
    }
    apply(data, true);
    if (data.patient_source === "mcp") toast("Updated from the AI assistant");
    if (drafted) toast("Draft notes are on screen");
  };
}

boot().catch((error) => showBanner(error.message));

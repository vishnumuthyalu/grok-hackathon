/* TrialRadar doctor UI. The session is shared with the MCP server via SSE. */
const $ = (id) => document.getElementById(id);
let state = null;
let revision = -1;
let markers = [];
let map = null;
let mapLayer = null;
let chipText = "";
let timer = null;
let ignoreForm = false;

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

function patientFromForm() {
  const num = (id) => {
    const raw = $(id).value.trim();
    if (!raw) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  };
  const priors = $("priors").value.split(";").map((part) => part.trim()).filter(Boolean);
  return {
    id: state && state.patient ? state.patient.id : null,
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
    brain_mets: state && state.patient ? state.patient.brain_mets : null,
    kidney_adequate: state && state.patient ? state.patient.kidney_adequate : null,
    prior_immunotherapy: state && state.patient ? state.patient.prior_immunotherapy : null,
    measurable_disease: state && state.patient ? state.patient.measurable_disease : null,
    line_of_therapy: state && state.patient ? state.patient.line_of_therapy : null,
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
  renderChips();
  ignoreForm = false;
}

function renderChips() {
  const box = $("chips");
  box.innerHTML = markers.map((chip, index) => (
    `<span class="chip">${esc(chip)} <button type="button" data-remove="${index}" aria-label="Remove ${esc(chip)}">×</button></span>`
  )).join("") + `<span class="chip add"><input id="chip-add" placeholder="+ add" value="${esc(chipText)}" aria-label="Add biomarker"></span>`;
  const input = $("chip-add");
  input.addEventListener("input", () => { chipText = input.value; });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      const value = input.value.trim();
      if (value) {
        markers.push(value);
        chipText = "";
        renderChips();
        schedule(false);
      }
    }
  });
}

function phases() {
  return ["1", "2", "3"].filter((digit) => $("ph" + digit).checked);
}

function answers() {
  return state && state.answers ? { ...state.answers } : {};
}

function whatIfs() {
  const map = {};
  for (const item of (state && state.what_ifs) || []) map[item.id] = !!item.on;
  return map;
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

function apply(data, external) {
  if (!data) return;
  // A cleared session used to restart the counter at 1, and the open page
  // dropped every later event. A newer timestamp still counts.
  if (data.revision < revision) {
    const next = data.updated_at || "";
    const previous = (state && state.updated_at) || "";
    if (!next || next <= previous) return;
  }
  revision = data.revision;
  state = data;
  if (external) fillForm(data.patient);
  render(data);
}

function render(data) {
  const banner = $("banner");
  if (data.warning || data.error) {
    banner.hidden = false;
    banner.textContent = data.error || data.warning;
  } else {
    banner.hidden = true;
  }
  $("q-count").textContent = data.questions_open ? `${data.questions_open} open` : "";
  $("list-count").textContent = `${data.found_count || 0} found, ${data.checked_count || 0} checked · ranked by fit`;
  const when = data.retrieved_at ? timeAgo(data.retrieved_at) : "not yet";
  $("source-line").textContent = `${data.source_label || "ClinicalTrials.gov"} · last checked ${when}`;
  $("mode-pill").textContent = data.demo_cache ? "Recorded demo data" : "Live ClinicalTrials.gov";
  $("distance").value = data.filters.distance_mi;
  $("distance-val").textContent = `${data.filters.distance_mi} mi`;
  for (const digit of ["1", "2", "3"]) {
    $("ph" + digit).checked = (data.filters.phases || []).includes(digit);
  }
  $("recruiting").classList.toggle("on", !!data.filters.recruiting_only);
  $("recruiting").setAttribute("aria-pressed", data.filters.recruiting_only ? "true" : "false");
  renderQuestions(data);
  renderTrials(data);
  renderDetail(data);
  updateMap(data);
}

function renderQuestions(data) {
  const box = $("questions");
  const items = data.questions || [];
  if (!items.length) {
    box.innerHTML = `<div class="empty">Unknown rules show up here after trials are checked.</div>`;
    return;
  }
  box.innerHTML = items.map((item) => `
    <div class="q">
      <p>${esc(item.text)} <span class="meta">· affects ${item.affects} trial${item.affects === 1 ? "" : "s"}</span></p>
      <div class="yn">
        ${["yes", "no", "unknown"].map((answer) => `
          <button type="button" data-answer="${esc(item.id)}:${answer}" class="${item.answer === answer ? "on" : ""}">${label(answer)}</button>
        `).join("")}
      </div>
    </div>`).join("");
}

function label(answer) {
  if (answer === "yes") return "Yes";
  if (answer === "no") return "No";
  return "Unknown";
}

function renderTrials(data) {
  const box = $("trial-list");
  const rows = data.ranked || [];
  if (!rows.length && !(data.unchecked || []).length) {
    box.innerHTML = `<div class="empty">Load an example or enter a cancer type and a city, then find trials.</div>`;
    $("more-label").textContent = "";
    return;
  }
  const selected = data.selected && data.selected.nct_id;
  const pending = (data.unchecked || []).map((row) => `
    <button type="button" class="row pending" data-nct="${esc(row.nct_id)}">
      <div class="rk">·</div>
      <div>
        <div class="tt">${esc(row.brief_title)}</div>
        <div class="mt"><code>${esc(row.nct_id)}</code><span>${esc(row.phase_label)}</span><span>${esc(row.nearest_label || "Site")} · ${miles(row.nearest_distance_mi)}</span></div>
        <div class="pills"><span class="p pn">Not checked</span></div>
      </div>
    </button>`).join("");
  box.innerHTML = rows.map((row) => {
    const tag = row.tag === "best_fit" ? `<span class="tag top">Best fit</span>`
      : row.tag === "near_miss" ? `<span class="tag a">Near miss</span>`
      : row.tag === "excluded" ? `<span class="tag x">Excluded</span>` : "";
    const blocking = row.failed >= 2
      ? `<div class="exr">${esc(row.blocking || "")}</div>`
      : row.blocking ? `<div class="nm">${esc(row.blocking)}</div>` : "";
    return `<button type="button" class="row ${row.nct_id === selected ? "sel" : ""} ${row.tag === "excluded" ? "ex" : ""}" data-nct="${esc(row.nct_id)}">
      <div class="rk">${row.rank}</div>
      <div>
        <div class="tt">${esc(row.brief_title)} ${tag}</div>
        <div class="mt"><code>${esc(row.nct_id)}</code><span>${esc(row.phase_label)}</span><span>${esc(row.nearest_label || "Site")} · ${miles(row.nearest_distance_mi)}</span></div>
        <div class="pills"><span class="p pg">${row.met} met</span><span class="p ${row.failed ? "pr" : "pn"}">${row.failed} failed</span><span class="p pa">${row.unknown} unknown</span></div>
        ${blocking}
      </div>
    </button>`;
  }).join("") + pending;
  const more = data.unchecked_count || 0;
  $("more-label").textContent = more
    ? `${more} more trial${more === 1 ? "" : "s"} found within ${data.filters.distance_mi} mi, not yet checked`
    : "All matching trials in this radius are checked";
}

function renderDetail(data) {
  const box = $("detail");
  const trial = data.selected;
  if (!trial) {
    box.innerHTML = `<div class="empty">Select a checked trial to see each rule and the evidence it used.</div><div id="map"></div>`;
    return;
  }
  const rules = organize(trial.rules || []);
  const ruleHtml = rules.shown.map(ruleCard).join("") + (rules.extra ? `
    <div class="rule"><span class="p pg">Met ×${rules.extra}</span><div><span>${esc(rules.extraText)}</span></div></div>` : "");
  const what = (data.what_ifs || []).map((item) => `
    <div class="wi">
      <button type="button" class="tg ${item.on ? "on" : ""}" data-whatif="${esc(item.id)}" aria-pressed="${item.on}"></button>
      <div><div class="t">${esc(item.label)}</div><div class="s">${esc(item.detail)}</div></div>
    </div>`).join("");
  const other = (data.ranked || []).find((row) => row.nct_id !== trial.nct_id);
  box.innerHTML = `
    <div class="kicker">#${trial.rank} · ${esc(trial.nct_id)} · ${esc(trial.phase_label)}</div>
    <div class="dt">${esc(trial.brief_title)}</div>
    <div style="margin-top:5px"><a class="link" href="${esc(trial.url)}" target="_blank" rel="noreferrer">View on ClinicalTrials.gov ↗</a></div>
    <div id="map" role="img" aria-label="Map of the patient and the nearest trial site"></div>
    <div class="rules">${ruleHtml || `<div class="empty">No rules were returned for this study.</div>`}</div>
    ${what}
    ${data.summary_note ? `<div class="draft-preview"><h3>Draft from the assistant</h3><pre>${esc(data.summary_note)}</pre></div>` : ""}
    <div class="btns">
      <button type="button" class="btn" id="compare" ${other ? "" : "disabled"}>Compare with #${other ? other.rank : ""}</button>
      <button type="button" class="btn" id="referral">Draft referral note</button>
      <button type="button" class="btn pri" id="summary">Patient-friendly summary</button>
    </div>`;
}

function organize(rules) {
  const order = { not_met: 0, unknown: 1, met: 2 };
  const sorted = rules.slice().sort((a, b) => order[a.status] - order[b.status]);
  const shown = [];
  const hidden = [];
  let mets = 0;
  for (const rule of sorted) {
    const quietMet = rule.status === "met" && (rule.boilerplate || mets >= 4);
    const quietUnknown = rule.status === "unknown" && rule.boilerplate;
    if (quietMet || quietUnknown) hidden.push(rule);
    else {
      shown.push(rule);
      if (rule.status === "met") mets += 1;
    }
  }
  const extraMets = hidden.filter((rule) => rule.status === "met");
  return {
    shown,
    extra: extraMets.length,
    extraText: extraMets.slice(0, 3).map((rule) => rule.text).join(" · "),
  };
}

function ruleCard(rule) {
  const pill = rule.status === "met" ? "pg" : rule.status === "not_met" ? "pr" : "pa";
  const word = rule.status === "met" ? "Met" : rule.status === "not_met" ? "Not met" : "Unknown";
  const evidence = rule.evidence && rule.evidence !== "no evidence"
    ? `<div class="ev">Evidence: <mark>${esc(rule.evidence)}</mark>${rule.hypothetical ? " — hypothetical" : ""}</div>`
    : "";
  return `<div class="rule"><span class="p ${pill}">${word}</span><div>
    ${rule.hypothetical ? `<div class="hypo">HYPOTHETICAL</div>` : ""}
    <b>${esc(rule.text)}</b><span>${esc(rule.reason)}</span>${evidence}
  </div></div>`;
}

function updateMap(data) {
  const el = $("map");
  if (!el || !window.L || !data.origin || data.origin.lat == null) return;
  if (map && map.getContainer() !== el) {
    map.remove();
    map = null;
  }
  if (!map) {
    map = L.map(el, { zoomControl: false, attributionControl: true });
    mapLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 12,
      attribution: "&copy; OpenStreetMap",
    }).addTo(map);
  }
  map.eachLayer((layer) => { if (layer !== mapLayer) map.removeLayer(layer); });
  const points = [[data.origin.lat, data.origin.lon]];
  L.circleMarker(points[0], { radius: 8, color: "#0e8a87", fillColor: "#0e8a87", fillOpacity: 0.9 })
    .bindTooltip(data.origin.label || "Patient")
    .addTo(map);
  const sites = (data.selected && data.selected.sites) || [];
  for (const site of sites) {
    points.push([site.lat, site.lon]);
    L.circleMarker([site.lat, site.lon], { radius: 6, color: "#b7740a", fillColor: "#b7740a", fillOpacity: 0.85 })
      .bindTooltip(`${site.facility || site.label || "Site"} · ${Math.round(site.distance_mi)} mi`)
      .addTo(map);
  }
  map.fitBounds(points, { padding: [18, 18], maxZoom: 8 });
  setTimeout(() => map.invalidateSize(), 50);
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
  return `${mins} min ago`;
}

function showBanner(text) {
  $("banner").hidden = false;
  $("banner").textContent = text;
}

function toast(text) {
  const el = $("toast");
  el.hidden = false;
  el.textContent = text;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 2200);
}

async function openNotes(kind) {
  const data = await api("/api/notes", { nct_id: state.selected && state.selected.nct_id });
  const notes = data.notes;
  if (!notes) {
    showBanner(data.error || "Choose a checked trial first.");
    return;
  }
  const body = kind === "summary" ? notes.summary : notes.referral;
  const title = kind === "summary" ? "Patient-friendly summary" : "Draft referral note";
  $("modal").innerHTML = `
    <div class="modal-back">
      <div class="modal" role="dialog" aria-modal="true">
        <h3>${title}</h3>
        <p class="note">Editable on this laptop. Copy or download only. Nothing is sent to a patient or a site.</p>
        <textarea id="note-text">${esc(body)}</textarea>
        <div class="btns">
          <button type="button" class="btn" id="copy-note">Copy</button>
          <button type="button" class="btn" id="download-note">Download</button>
          <button type="button" class="btn pri" id="close-modal">Close</button>
        </div>
      </div>
    </div>`;
}

function openCompare() {
  const rows = state.ranked || [];
  const left = state.selected;
  const right = rows.find((row) => row.nct_id !== (left && left.nct_id)) || rows[1];
  if (!left || !right) return;
  const column = (trial) => `
    <div>
      <div class="kicker">${esc(trial.nct_id)} · ${trial.met} met · ${trial.failed} failed · ${trial.unknown} unknown</div>
      <div class="tt">${esc(trial.brief_title)}</div>
      <div class="rules">${(trial.rules || []).filter((rule) => !rule.boilerplate || rule.status !== "unknown").slice(0, 12).map(ruleCard).join("")}</div>
    </div>`;
  $("modal").innerHTML = `
    <div class="modal-back"><div class="modal" role="dialog" aria-modal="true">
      <h3>Compare rule by rule</h3>
      <div class="compare">${column(left)}${column(right)}</div>
      <p class="note">${esc(state.disclaimer)}</p>
      <div class="btns"><button type="button" class="btn pri" id="close-modal">Close</button></div>
    </div></div>`;
}

document.addEventListener("click", async (event) => {
  const remove = event.target.closest("[data-remove]");
  if (remove) {
    markers.splice(Number(remove.dataset.remove), 1);
    renderChips();
    schedule(false);
    return;
  }
  const answer = event.target.closest("[data-answer]");
  if (answer) {
    const [id, value] = answer.dataset.answer.split(":");
    const next = answers();
    next[id] = value;
    apply(await api("/api/rescore", { answers: next, what_ifs: whatIfs() }), false);
    return;
  }
  const what = event.target.closest("[data-whatif]");
  if (what) {
    const flags = whatIfs();
    flags[what.dataset.whatif] = !flags[what.dataset.whatif];
    apply(await api("/api/rescore", { what_ifs: flags, answers: answers() }), false);
    return;
  }
  const trial = event.target.closest("[data-nct]");
  if (trial) {
    apply(await api("/api/select", { nct_id: trial.dataset.nct }), false);
    return;
  }
  if (event.target.id === "compare") openCompare();
  if (event.target.id === "referral") openNotes("referral");
  if (event.target.id === "summary") openNotes("summary");
  if (event.target.id === "close-modal") $("modal").innerHTML = "";
  if (event.target.id === "copy-note") {
    await navigator.clipboard.writeText($("note-text").value);
    toast("Copied. Nothing was sent.");
  }
  if (event.target.id === "download-note") {
    const blob = new Blob([$("note-text").value], { type: "text/plain" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "trialradar-draft.txt";
    link.click();
  }
});

$("patient-form").addEventListener("input", () => {
  if (ignoreForm) return;
  schedule(false);
});

$("find").addEventListener("click", () => savePatient(true));
$("check-next").addEventListener("click", async () => {
  apply(await api("/api/check", { next_batch: true, top_n: 5 }), false);
});
$("refresh-live").addEventListener("click", async () => {
  apply(await api("/api/search", { patient: patientFromForm(), live: true }), true);
  toast("Searched ClinicalTrials.gov");
});
$("distance").addEventListener("input", () => {
  $("distance-val").textContent = `${$("distance").value} mi`;
  clearTimeout(timer);
  timer = setTimeout(async () => {
    apply(await api("/api/rescore", { distance_mi: Number($("distance").value) }), false);
  }, 200);
});
for (const digit of ["1", "2", "3"]) {
  $("ph" + digit).addEventListener("change", async () => {
    apply(await api("/api/rescore", { phases: phases() }), false);
  });
}
$("recruiting").addEventListener("click", async () => {
  const on = !$("recruiting").classList.contains("on");
  apply(await api("/api/rescore", { recruiting_only: on }), false);
});
$("src-typed").addEventListener("click", () => {
  $("src-typed").classList.add("on");
  $("src-photo").classList.remove("on");
  $("photo-box").hidden = true;
});
$("src-photo").addEventListener("click", () => {
  $("src-photo").classList.add("on");
  $("src-typed").classList.remove("on");
  $("photo-box").hidden = false;
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
  } catch (error) {
    showBanner(error.message);
  }
});
$("examples").addEventListener("change", async () => {
  const id = $("examples").value;
  if (!id) return;
  apply(await api(`/api/examples/${id}`, {}), true);
  $("examples").value = "";
  toast("Example loaded");
});

async function boot() {
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
      revision = data.revision;
      state = data;
      render(data);
      return;
    }
    apply(data, true);
    if (data.patient_source === "mcp") toast("Updated from the AI assistant");
    if (drafted) toast("Draft notes are on screen");
  };
}

boot().catch((error) => showBanner(error.message));

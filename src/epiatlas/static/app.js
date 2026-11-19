// EpiAtlas screen. Two pages:
//   Patient - every test of one patient together
//   Machine - the computer's blind reading, compared with the doctors only when asked
// Colours: amber = doctors, teal = machine. Plain JavaScript, no internet needed.

// ---------- Small helpers ----------
function $(selector) {
  return document.querySelector(selector);
}

// Make text safe to put inside HTML (so a file name can never run as code)
function esc(text) {
  const safe = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(text ?? "").replace(/[&<>"']/g, (ch) => safe[ch]);
}

function cssColor(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// Ask the server for something; show its error message if it fails
async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || response.statusText);
  }
  return response;
}

async function getJSON(path, options) {
  const response = await api(path, options);
  return response.json();
}

// 75 seconds -> "1:15"
function formatTime(seconds) {
  const minutes = Math.floor(seconds / 60);
  const rest = String(Math.floor(seconds % 60)).padStart(2, "0");
  return `${minutes}:${rest}`;
}

function median(numbers) {
  const sorted = [...numbers].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] || 1;
}

function capitalize(text) {
  text = String(text || "");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// "EEG T7-Ref" -> ["T7"];  "A1-A2" -> ["A1", "A2"]
function contactsOf(channel) {
  const clean = channel.toUpperCase().replace(/^(EEG|SEEG|POL)\s+/, "").replace(/[-_ ]?(REF|AV|AVG|LE)$/, "");
  return clean.split("-").filter((c) => c !== "");
}

function option(value, label, current) {
  const selected = value === current ? "selected" : "";
  return `<option value="${esc(value)}" ${selected}>${esc(label)}</option>`;
}

function arrowIcon(path) {
  return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"></path></svg>`;
}

// ---------- What the screen remembers ----------
const state = {
  patient: null,    // patient folder name
  catalog: null,    // the list of the patient's files
  page: "patient",
  summary: null,    // page 1 table
  selectedRow: 0,
  eeg: {},          // the open recording on page 1
  image: {},        // the open images on page 1
  machine: {},      // page 2: which recording, comparison, export
};

const RECORDING_KIND = { seeg: "sEEG", eeg: "Scalp EEG", meg: "MEG" };
const RECORDING_STATE = { ictal: "seizure", interictal: "interictal", unknown: "recording" };

function patientURL() {
  return `/api/patients/${encodeURIComponent(state.patient)}`;
}

function filesOf(kind, modality) {
  return state.catalog.items.filter((item) => item.kind === kind && (!modality || item.modality === modality));
}

// Recordings with seizures first, sEEG before scalp
function recordings() {
  return filesOf("ephys").sort((a, b) => {
    if (a.state !== b.state) return a.state === "ictal" ? -1 : 1;
    if (a.modality !== b.modality) return a.modality === "seeg" ? -1 : 1;
    return 0;
  });
}

function recordingName(recording) {
  return `${RECORDING_KIND[recording.modality] || recording.modality} ${RECORDING_STATE[recording.state]}`;
}

// ---------- Start ----------
async function start() {
  const patients = await getJSON("/api/patients");
  if (patients.length === 0) {
    $("#main").innerHTML = '<div class="empty">No patients yet. Put one folder per patient in data/raw/ and reload.</div>';
    return;
  }
  $("#patient").innerHTML = patients.map((p) => option(p.id, p.id)).join("");
  $("#patient").onchange = (event) => openPatient(event.target.value);
  $("#tabs").onclick = (event) => {
    const button = event.target.closest("[data-page]");
    if (button) {
      state.page = button.dataset.page;
      showPage();
    }
  };
  openPatient(patients[0].id);
}

async function openPatient(patientId) {
  Object.assign(state, { patient: patientId, summary: null, selectedRow: 0, eeg: {}, image: {}, machine: {} });
  // Rescan the folder every time, so newly added files appear after a reload
  state.catalog = await getJSON(`${patientURL()}/ingest`, { method: "POST" });
  showPage();
}

function showPage() {
  for (const button of document.querySelectorAll("#tabs button")) {
    button.setAttribute("aria-selected", button.dataset.page === state.page);
  }
  $("#main").innerHTML = '<div class="wrap" id="view"><div class="empty">Loading…</div></div>';
  const draw = state.page === "patient" ? pagePatient : pageMachine;
  draw().catch((error) => {
    $("#view").innerHTML = `<div class="empty">Could not load: ${esc(error.message)}</div>`;
  });
}

// ================= Page 1: Patient =================

// What clicking a table row opens on the right
function targetFor(row) {
  if (row.item) return { kind: "ephys", id: row.item };
  const test = row.test.toLowerCase();
  const mri = filesOf("image", "mri")[0];
  for (const modality of ["pet", "ct", "fmri"]) {
    const image = filesOf("image", modality)[0];
    if (test.includes(modality) && image) {
      // Show it on top of the MRI when there is one
      return mri ? { kind: "image", base: mri.id, overlay: image.id } : { kind: "image", base: image.id, overlay: "" };
    }
  }
  if (test.includes("mri") && mri) return { kind: "image", base: mri.id, overlay: "" };
  if (test.includes("neuropsych")) {
    const scores = filesOf("document", "neuropsychology")[0];
    if (scores) return { kind: "doc", id: scores.id };
  }
  if (test.includes("semiolog")) return { kind: "semiology" };
  return null;
}

// Files that no table row opens, shown as "Also:" links
function otherFiles(rows) {
  const opened = rows.map((row) => JSON.stringify(targetFor(row))).join(" ");
  const mri = filesOf("image", "mri")[0];
  const links = [];
  for (const image of filesOf("image")) {
    if (image.note || opened.includes(image.id)) continue;
    const overlay = image.modality === "mri" ? "" : image.id;
    links.push([image.modality.toUpperCase(), { kind: "image", base: (mri || image).id, overlay }]);
  }
  for (const file of filesOf("document")) {
    if (file.modality !== "neuropsychology") links.push([file.name, { kind: "doc", id: file.id }]);
  }
  for (const video of filesOf("video")) {
    links.push([video.name, { kind: "doc", id: video.id }]);
  }
  return links;
}

function tableRow(row, index) {
  const selected = index === state.selectedRow ? "on" : "";
  const detail = row.detail ? `<br><span class="muted" style="font-size:13px">${esc(row.detail)}</span>` : "";
  return `<tr data-row="${index}" class="${selected}">
    <td><button class="rowbtn">${esc(row.test)}</button></td>
    <td><strong>${esc(row.points_to)}</strong>${detail}</td></tr>`;
}

// One group of the table: blind readings, or readings from reports
function tableGroup(source, title, verdict) {
  const colour = source === "machine" ? "mach" : "clin";
  let html = `<tr class="group t-${colour}"><th colspan="2"><span class="dot ${colour}"></span>${title}</th></tr>`;
  let count = 0;
  state.summary.rows.forEach((row, index) => {
    if (row.source === source) {
      html += tableRow(row, index);
      count += 1;
    }
  });
  if (count === 0) return "";
  if (verdict) html += `<tr class="group"><td colspan="2" class="verdict">${esc(verdict)}</td></tr>`;
  return html;
}

async function pagePatient() {
  if (!state.summary) state.summary = await getJSON(`${patientURL()}/summary`);
  const summary = state.summary;
  const clinical = summary.clinical;
  const links = otherFiles(summary.rows);

  const demoTag = clinical.synthetic ? '<span class="tag">Synthetic demo</span>' : "";
  const hypothesis = clinical.clinical_hypothesis
    ? `<span class="hyp push">Doctors think: <strong>${esc(clinical.clinical_hypothesis)}</strong></span>` : "";
  const linkButtons = links.map(([label], i) => `<button class="linkbtn" data-other="${i}">${esc(label)}</button>`).join("");

  $("#view").innerHTML = `
    <div class="bar"><h1 style="font-size:28px">${esc(state.patient)}</h1>${demoTag}${hypothesis}</div>
    <div class="split">
      <section class="card list">
        <table class="rows">
          ${tableGroup("machine", "Blind: the machine read the raw data", summary.agreement.blind)}
          ${tableGroup("clinician", "From reports: read by doctors", summary.agreement.report)}
        </table>
        <p class="muted" style="padding:0 10px;font-size:14px">Doctors' readings can sway each other
          (the EEG reader may know the MRI result). Blind readings cannot.</p>
        ${links.length ? `<div class="others"><span class="muted">Also:</span>${linkButtons}</div>` : ""}
      </section>
      <section class="card viewer" id="viewer"></section>
    </div>`;

  $(".rows").onclick = (event) => {
    const row = event.target.closest("[data-row]");
    if (row) {
      state.selectedRow = Number(row.dataset.row);
      pagePatient();
    }
  };
  if ($(".others")) {
    $(".others").onclick = (event) => {
      const button = event.target.closest("[data-other]");
      if (button) show(links[Number(button.dataset.other)][1]);
    };
  }
  show(summary.rows.length ? targetFor(summary.rows[state.selectedRow]) : null);
}

// Open something in the right-hand viewer
function show(target) {
  if (!target) {
    $("#viewer").innerHTML = '<p class="muted">No file for this test in the patient folder.</p>';
    return;
  }
  $("#viewer").innerHTML = '<p class="muted">Loading…</p>';
  const viewers = { ephys: showEEG, image: showImage, doc: showDocument, semiology: showSemiology };
  viewers[target.kind](target).catch((error) => {
    $("#viewer").innerHTML = `<p class="muted">Could not open: ${esc(error.message)}</p>`;
  });
}

// ================= Page 2: Machine =================
async function pageMachine() {
  const list = recordings();
  if (list.length === 0) {
    $("#view").innerHTML = '<div class="empty">No recordings to read.</div>';
    return;
  }
  if (!state.summary) state.summary = await getJSON(`${patientURL()}/summary`);
  const machine = state.machine;
  if (!list.some((r) => r.id === machine.id)) {
    Object.assign(machine, { id: list[0].id, comparison: null, saved: null });
  }

  const result = await getJSON(`${patientURL()}/analysis/${machine.id}`);
  const row = state.summary.rows.find((r) => r.item === machine.id);
  const isSeizure = Boolean(result.ictal);

  // The machine's answer in one sentence
  let sentence = "Nothing clear found in this recording.";
  if (row && isSeizure) {
    sentence = `Seizure starts in ${row.detail} (${row.points_to.toLowerCase()})`;
    if (row.onset_s != null) sentence += ` at ${formatTime(Math.round(row.onset_s))}`;
  } else if (row) {
    sentence = `Most spikes in ${row.detail} (${row.points_to.toLowerCase()})`;
  }
  const edgeWarning = row?.edge?.length
    ? `<p class="warn">${esc(row.edge.join(", "))} ${row.edge.length > 1 ? "are" : "is"} at the outer edge of the electrodes.
       The real start may be outside the recorded area.</p>` : "";

  // Its top 5 channels, as bars
  const scoreOf = (r) => (isSeizure ? r.score : r.per_min);
  const top = (isSeizure ? result.ictal : result.interictal).filter((r) => scoreOf(r) > 0).slice(0, 5);
  const highest = top.length ? scoreOf(top[0]) : 1;
  const doctorsContacts = new Set(machine.comparison?.clinical || []);
  let bars = "";
  top.forEach((r, i) => {
    // After the reveal, channels the doctors also marked get an amber edge
    const doctorsToo = machine.comparison && contactsOf(r.channel).some((c) => doctorsContacts.has(c));
    const label = isSeizure ? r.score.toFixed(2) : `${r.per_min}/min`;
    bars += `<div class="rank" ${doctorsToo ? 'style="box-shadow:inset 3px 0 var(--clin);padding-left:8px"' : ""}>
      <span class="muted">${i + 1}</span>
      <span><strong>${esc(r.channel)}</strong><small>${esc(r.region || "")}</small></span>
      <span class="bar" style="flex-wrap:nowrap;gap:10px">
        <span class="fill" style="width:${Math.max(3, (scoreOf(r) / highest) * 100)}%"></span>${label}</span></div>`;
  });

  const exportNote = machine.saved ? `Saved: ${esc(machine.saved.path)}`
    : '<span class="muted">Saves a copy of the recording with these findings as markers. The original is not changed.</span>';
  // Shown only after the reveal, because it contains the doctors' areas
  const study = machine.comparison ? studyCard(await getJSON("/api/study")) : "";

  $("#view").innerHTML = `
    <div class="bar"><h1 style="font-size:28px">Machine reading</h1>
      <select id="recording" class="push" aria-label="Recording">${list.map((r) => option(r.id, recordingName(r), machine.id)).join("")}</select></div>
    <p class="muted">Reads only the raw signal. It does not know what the doctors think.</p>
    <section class="card" style="border-color:var(--mach-line)">
      <p class="big t-mach">${esc(sentence)}</p>${edgeWarning}${bars}
    </section>
    <section class="card" style="border-color:var(--clin-line)">${comparisonHTML(machine.comparison)}</section>
    <div class="bar"><button class="btn" id="send">Send to CURRY</button><span class="notice">${exportNote}</span></div>
    ${study}`;

  $("#recording").onchange = (event) => {
    state.machine = { id: event.target.value };
    pageMachine();
  };
  if ($("#reveal")) {
    $("#reveal").onclick = async () => {
      machine.comparison = await getJSON(`${patientURL()}/analysis/${machine.id}/compare`);
      pageMachine();
    };
  }
  $("#send").onclick = async (event) => {
    event.target.disabled = true;
    event.target.textContent = "Saving…";
    machine.saved = await getJSON(`${patientURL()}/analysis/${machine.id}/export`, { method: "POST" });
    pageMachine();
  };
}

function pills(contacts, colour) {
  if (!contacts.length) return '<span class="muted">None</span>';
  return contacts.map((c) => `<span class="pill ${colour}">${esc(c)}</span>`).join("");
}

function comparisonHTML(comparison) {
  if (!comparison) {
    return `<div class="bar"><span>The doctors' sEEG reading is hidden until you choose to compare.</span>
      <button class="btn reveal push" id="reveal">Show doctors' reading</button></div>`;
  }
  if (comparison.note) return `<p>${esc(comparison.note)}</p>`;
  return `<p class="big">Agree on ${comparison.found.length} of ${comparison.clinical.length} contacts</p>
    <div class="compare">
      <div><strong class="t-clin">Doctors</strong><div class="pills">${pills(comparison.clinical, "clin")}</div></div>
      <div><strong class="t-mach">Machine</strong><div class="pills">${pills(comparison.machine_contacts, "mach")}</div></div>
    </div>`;
}

// Across past patients: the outside answer is the surgery outcome, not anyone's reading
function studyCard(study) {
  const percent = (share) => (share == null ? "–" : `${Math.round(share * 100)}%`);
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  if (study.n === 0) {
    return `<section class="card"><h2 style="font-size:18px">Across past patients</h2>
      <p class="muted">No patient has a surgery outcome yet. Add "surgery" (resected_contacts, engel_class)
        to a patient's clinical JSON.</p></section>`;
  }
  let rows = "";
  for (const p of study.patients) {
    rows += `<tr><td>${esc(p.patient)}</td><td>${esc(p.engel)} ${p.seizure_free ? "seizure-free" : "still has seizures"}</td>
      <td>${percent(p.machine_removed)}</td><td>${percent(p.doctors_removed)}</td></tr>`;
  }
  const warning = study.n < 30
    ? `<p class="muted" style="font-size:14px">Only ${plural(study.n, "patient")} with a known outcome.
       This needs tens of patients before it means anything.</p>` : "";
  return `<section class="card"><h2 style="font-size:18px">Across past patients</h2>
    <p>In the ${plural(study.n_seizure_free, "seizure-free patient")}, the start area was fully removed for
      <strong class="t-mach">the machine in ${study.machine_removed_in_seizure_free}</strong> and
      <strong class="t-clin">the doctors in ${study.doctors_removed_in_seizure_free}</strong>.</p>
    <table><tr><th>Patient</th><th>Outcome (Engel)</th><th>Machine's area removed</th><th>Doctors' area removed</th></tr>${rows}</table>
    ${warning}</section>`;
}

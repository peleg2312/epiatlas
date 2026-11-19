// EpiAtlas viewers: what opens on the right of page 1 (EEG traces, brain images, documents, semiology).
// Uses the helpers and the 'state' from app.js, which is loaded first.

// ---------- EEG / sEEG viewer ----------
async function showEEG({ id }) {
  const eeg = state.eeg;
  const recording = filesOf("ephys").find((r) => r.id === id);
  if (eeg.id !== id) {
    // New recording: fixed, standard display (bipolar for sEEG, average for scalp EEG)
    Object.assign(eeg, { id, seconds: 10, gain: 1, scale: null, montage: recording.modality === "seeg" ? "bipolar" : "average" });
    eeg.info = await getJSON(`${patientURL()}/ephys/${id}/info`);
    eeg.analysis = await getJSON(`${patientURL()}/analysis/${id}`);
    // Open 4 s before the machine's seizure onset, or at the start
    const onsets = (eeg.analysis.ictal || []).filter((r) => r.score > 0 && r.onset_s != null).map((r) => r.onset_s);
    eeg.start = onsets.length ? Math.max(0, Math.floor(Math.min(...onsets)) - 4) : 0;
  }

  $("#viewer").innerHTML = `
    <div class="bar"><h2 style="font-size:18px">${esc(recordingName(recording))}</h2>
      <div class="bar push" style="gap:6px">
        <button class="btn icon" id="smaller" aria-label="Smaller">−</button>
        <button class="btn icon" id="bigger" aria-label="Bigger">+</button>
        <button class="btn icon" id="back" aria-label="Back">${arrowIcon("M15 18l-6-6 6-6")}</button>
        <strong id="range" style="min-width:110px;text-align:center"></strong>
        <button class="btn icon" id="forward" aria-label="Forward">${arrowIcon("M9 18l6-6-6-6")}</button>
      </div></div>
    <canvas id="eeg" tabindex="0" aria-label="EEG traces. Left and right arrow keys move."></canvas>
    <div class="marks" id="marks"></div>
    <canvas id="strip" aria-label="Whole recording. Click to jump."></canvas>`;

  $("#back").onclick = () => movePage(-1);
  $("#forward").onclick = () => movePage(1);
  $("#bigger").onclick = () => { eeg.gain *= 1.5; drawEEG(); };
  $("#smaller").onclick = () => { eeg.gain /= 1.5; drawEEG(); };
  $("#eeg").onkeydown = (event) => {
    if (event.key === "ArrowRight") movePage(1);
    if (event.key === "ArrowLeft") movePage(-1);
  };
  $("#strip").onclick = (event) => {
    const box = event.target.getBoundingClientRect();
    const clickedTime = ((event.clientX - box.left) / box.width) * eeg.info.duration;
    jumpTo(clickedTime - eeg.seconds / 2);
  };
  await loadWindow();
}

function jumpTo(time) {
  const eeg = state.eeg;
  eeg.start = Math.max(0, Math.min(eeg.info.duration - eeg.seconds, time));
  loadWindow();
}

function movePage(direction) {
  jumpTo(state.eeg.start + direction * state.eeg.seconds);
}

async function loadWindow() {
  const eeg = state.eeg;
  const query = new URLSearchParams({ start: eeg.start, dur: eeg.seconds, montage: eeg.montage, notch: 50 });
  eeg.window = await getJSON(`${patientURL()}/ephys/${eeg.id}/window?${query}`);
  drawEEG();
}

// Prepare a canvas for sharp drawing on high-resolution screens
function canvasContext(canvas) {
  const ratio = devicePixelRatio || 1;
  canvas.width = canvas.clientWidth * ratio;
  canvas.height = canvas.clientHeight * ratio;
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  return ctx;
}

function verticalLine(ctx, x, top, bottom) {
  ctx.beginPath();
  ctx.moveTo(x, top);
  ctx.lineTo(x, bottom);
  ctx.stroke();
}

function drawEEG() {
  const eeg = state.eeg;
  const data = eeg.window;
  const canvas = $("#eeg");
  if (!data || !canvas) return;
  $("#range").textContent = `${formatTime(data.start)} – ${formatTime(data.start + eeg.seconds)}`;

  const ctx = canvasContext(canvas);
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const labelWidth = 72;
  const bottom = 20;
  const rowHeight = (height - bottom) / data.channels.length;
  const pixelsPerSecond = (width - labelWidth - 4) / eeg.seconds;
  const xOf = (time) => labelWidth + (time - data.start) * pixelsPerSecond;
  const onScreen = (time) => time >= data.start && time <= data.start + eeg.seconds;

  // Grid line and time label every second
  ctx.font = "12px system-ui";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 1;
  ctx.strokeStyle = cssColor("--hair");
  ctx.fillStyle = cssColor("--muted");
  for (let s = Math.ceil(data.start); s <= data.start + eeg.seconds; s++) {
    verticalLine(ctx, xOf(s), 0, height - bottom);
    ctx.fillText(formatTime(s), Math.min(xOf(s) - 12, width - 30), height - 8);
  }

  // Traces. The vertical size is set once per recording from the typical channel's spread.
  if (!eeg.scale) {
    const spreads = data.data.map((channel) => {
      const mean = channel.reduce((a, b) => a + b, 0) / channel.length;
      return Math.sqrt(channel.reduce((a, b) => a + (b - mean) ** 2, 0) / channel.length);
    });
    eeg.scale = median(spreads) * 6;
  }
  const pixelsPerMicrovolt = (rowHeight * eeg.gain) / eeg.scale;
  ctx.strokeStyle = cssColor("--ink");
  data.channels.forEach((name, i) => {
    const middle = (i + 0.5) * rowHeight;
    ctx.fillStyle = cssColor("--muted");
    ctx.fillText(name.replace(/^EEG /, "").replace(/-Ref$/i, "").slice(0, 9), 2, middle);
    ctx.beginPath();
    data.data[i].forEach((value, j) => {
      const x = labelWidth + (j / data.sfreq) * pixelsPerSecond;
      const y = middle - value * pixelsPerMicrovolt;
      if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });

  const marks = [];

  // Doctors' marks (amber lines across all channels)
  ctx.strokeStyle = cssColor("--clin");
  ctx.lineWidth = 2;
  for (const note of eeg.info.annotations) {
    if (!onScreen(note.onset)) continue;
    verticalLine(ctx, xOf(note.onset), 0, height - bottom);
    marks.push(`<span class="t-clin">${formatTime(note.onset)} ${esc(note.text)}</span>`);
  }

  // Machine's seizure onset (thick teal mark on each channel where it starts)
  ctx.strokeStyle = cssColor("--mach");
  ctx.fillStyle = cssColor("--mach");
  ctx.lineWidth = 4;
  const channelsByOnset = {};
  for (const row of eeg.analysis.ictal || []) {
    if (row.score <= 0 || row.onset_s == null || !onScreen(row.onset_s)) continue;
    channelsByOnset[row.onset_s] = (channelsByOnset[row.onset_s] || []).concat(row.channel);
    const i = data.channels.indexOf(row.channel);
    if (i >= 0) verticalLine(ctx, xOf(row.onset_s), i * rowHeight + 2, (i + 1) * rowHeight - 2);
  }
  for (const [time, channels] of Object.entries(channelsByOnset)) {
    marks.push(`<span class="t-mach">${formatTime(Number(time))} Machine: seizure starts on ${esc(channels.join(", "))}</span>`);
  }

  // Machine's spikes (small teal dots)
  for (const row of eeg.analysis.interictal || []) {
    const i = data.channels.indexOf(row.channel);
    if (i < 0) continue;
    for (const time of row.times_s.filter(onScreen)) {
      ctx.beginPath();
      ctx.arc(xOf(time), i * rowHeight + 6, 3, 0, 2 * Math.PI);
      ctx.fill();
    }
  }
  $("#marks").innerHTML = marks.join("");
  drawStrip();
}

// The strip under the traces: the whole recording, with the visible part boxed
function drawStrip() {
  const eeg = state.eeg;
  const canvas = $("#strip");
  const ctx = canvasContext(canvas);
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const xOf = (time) => (time / eeg.info.duration) * width;

  ctx.fillStyle = cssColor("--hair");
  ctx.fillRect(0, 11, width, 12);
  ctx.fillStyle = "rgba(23,50,77,0.12)";
  ctx.strokeStyle = cssColor("--ink");
  ctx.lineWidth = 1.5;
  ctx.fillRect(xOf(eeg.start), 2, xOf(eeg.seconds), height - 4);
  ctx.strokeRect(xOf(eeg.start), 2, xOf(eeg.seconds), height - 4);

  ctx.lineWidth = 3;
  ctx.strokeStyle = cssColor("--clin");
  for (const note of eeg.info.annotations) verticalLine(ctx, xOf(note.onset), 1, height - 1);
  ctx.strokeStyle = cssColor("--mach");
  for (const row of eeg.analysis.ictal || []) {
    if (row.score > 0 && row.onset_s != null) verticalLine(ctx, xOf(row.onset_s), 1, height - 1);
  }
}
addEventListener("resize", () => { if ($("#eeg")) drawEEG(); });

// ---------- Image viewer (MRI, with PET / CT / fMRI on top) ----------
const AXIS_NUMBER = { sagittal: 0, coronal: 1, axial: 2 };
// Side labels of each view: left, right, top, bottom of the screen
const VIEW_LABELS = { axial: ["R", "L", "A", "P"], coronal: ["R", "L", "S", "I"], sagittal: ["A", "P", "S", "I"] };

// "Hot" colour scale for the overlay: dark red -> yellow -> white
function hotColour(value) {
  const t = value / 255;
  return [Math.min(1, 3 * t), Math.min(1, Math.max(0, 3 * t - 1)), Math.max(0, 3 * t - 2)].map((c) => c * 255);
}

// Voxel (i, j, k) -> pixel (x, y) in a view, flipped the same way as the server's slices
function voxelToPixel(view, [i, j, k]) {
  const [sizeX, sizeY, sizeZ] = state.image.info.shape;
  if (view === "axial") return [sizeX - 1 - i, sizeY - 1 - j];
  if (view === "coronal") return [sizeX - 1 - i, sizeZ - 1 - k];
  return [sizeY - 1 - j, sizeZ - 1 - k];
}

async function showImage({ base, overlay }) {
  const image = state.image;
  const nameOf = (id) => filesOf("image").find((i) => i.id === id)?.modality.toUpperCase();
  if (image.base !== base) {
    image.base = base;
    image.position = null;
    image.info = await getJSON(`${patientURL()}/image/${base}/info`);
    image.contacts = await getJSON(`${patientURL()}/image/${base}/contacts`);
  }
  image.overlay = overlay;
  if (!image.position) image.position = image.info.shape.slice(0, 3).map((size) => Math.floor(size / 2));

  const title = overlay ? `${nameOf(overlay)} on ${nameOf(base)}` : nameOf(base);
  const hint = "Click to move, scroll to change slice" + (image.contacts.length ? ". Orange dots: electrode contacts" : "");
  let wells = "";
  for (const [view, [left, right, top, bottom]] of Object.entries(VIEW_LABELS)) {
    wells += `<div class="well" id="w-${view}"><canvas aria-label="${view} slice"></canvas>
      <b style="left:8px;top:50%">${left}</b><b style="right:8px;top:50%">${right}</b>
      <b style="top:6px">${top}</b><b style="bottom:6px">${bottom}</b></div>`;
  }
  $("#viewer").innerHTML = `<div class="bar"><h2 style="font-size:18px">${esc(title)}</h2>
      <span class="muted push" style="font-size:13px">${hint}</span></div>
    <div class="wells">${wells}</div>`;
  for (const view of Object.keys(VIEW_LABELS)) listenToView(view);
  await drawSlices();
}

function listenToView(view) {
  const image = state.image;
  const canvas = $(`#w-${view} canvas`);
  const [sizeX, sizeY, sizeZ] = image.info.shape;

  // Click: move the crosshair there (the other two views follow)
  canvas.onclick = (event) => {
    const box = canvas.getBoundingClientRect();
    const col = Math.floor(((event.clientX - box.left) / box.width) * canvas.width);
    const row = Math.floor(((event.clientY - box.top) / box.height) * canvas.height);
    const p = image.position;
    if (view === "axial") { p[0] = sizeX - 1 - col; p[1] = sizeY - 1 - row; }
    if (view === "coronal") { p[0] = sizeX - 1 - col; p[2] = sizeZ - 1 - row; }
    if (view === "sagittal") { p[1] = sizeY - 1 - col; p[2] = sizeZ - 1 - row; }
    drawSlices();
  };
  // Scroll: next / previous slice
  canvas.onwheel = (event) => {
    event.preventDefault();
    const axis = AXIS_NUMBER[view];
    const next = image.position[axis] + Math.sign(event.deltaY);
    image.position[axis] = Math.max(0, Math.min(image.info.shape[axis] - 1, next));
    drawSlices();
  };
}

async function fetchSlice(view, overlayId) {
  const image = state.image;
  let url = `${patientURL()}/image/${image.base}/slice?axis=${view}&index=${image.position[AXIS_NUMBER[view]]}`;
  if (overlayId) url += `&overlay=${overlayId}`;
  const response = await api(url);
  return { rows: Number(response.headers.get("X-Rows")), cols: Number(response.headers.get("X-Cols")),
           pixels: new Uint8Array(await response.arrayBuffer()) };
}

let latestDraw = 0;
async function drawSlices() {
  const image = state.image;
  const thisDraw = ++latestDraw;
  // Ask for all slices at the same time (faster than one after another)
  const slices = await Promise.all(Object.keys(VIEW_LABELS).map(async (view) => ({
    view,
    base: await fetchSlice(view),
    overlay: image.overlay ? await fetchSlice(view, image.overlay) : null,
  })));
  if (thisDraw !== latestDraw) return;  // the user scrolled again meanwhile; a newer draw is coming

  const [sizeX, sizeY, sizeZ] = image.info.zooms;  // millimetres per voxel
  for (const { view, base, overlay } of slices) {
    const canvas = $(`#w-${view} canvas`);
    if (!canvas) return;
    canvas.width = base.cols;
    canvas.height = base.rows;
    // Keep real proportions (voxels are not always square)
    const [mmPerCol, mmPerRow] = { axial: [sizeX, sizeY], coronal: [sizeX, sizeZ], sagittal: [sizeY, sizeZ] }[view];
    const wide = base.cols * mmPerCol >= base.rows * mmPerRow;
    canvas.style.aspectRatio = `${base.cols * mmPerCol} / ${base.rows * mmPerRow}`;
    canvas.style.width = wide ? "100%" : "auto";
    canvas.style.height = wide ? "auto" : "100%";

    // Grey image, with the overlay mixed in at 50% where it exists (0 = transparent)
    const ctx = canvas.getContext("2d");
    const picture = ctx.createImageData(base.cols, base.rows);
    for (let i = 0; i < base.pixels.length; i++) {
      let [r, g, b] = [base.pixels[i], base.pixels[i], base.pixels[i]];
      if (overlay && overlay.pixels[i]) {
        const [hr, hg, hb] = hotColour(overlay.pixels[i]);
        r = (r + hr) / 2;
        g = (g + hg) / 2;
        b = (b + hb) / 2;
      }
      picture.data.set([r, g, b, 255], i * 4);
    }
    ctx.putImageData(picture, 0, 0);

    // Crosshair
    const [cx, cy] = voxelToPixel(view, image.position);
    ctx.strokeStyle = "rgba(90, 210, 190, 0.55)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(cx + 0.5, 0); ctx.lineTo(cx + 0.5, base.rows);
    ctx.moveTo(0, cy + 0.5); ctx.lineTo(base.cols, cy + 0.5);
    ctx.stroke();

    // Electrode contacts within 2 slices of this one
    ctx.fillStyle = "#ffb347";
    ctx.strokeStyle = "#000";
    ctx.lineWidth = 0.5;
    const axis = AXIS_NUMBER[view];
    for (const contact of image.contacts) {
      if (Math.abs(contact.ijk[axis] - image.position[axis]) > 2) continue;
      const [x, y] = voxelToPixel(view, contact.ijk);
      ctx.beginPath();
      ctx.arc(x + 0.5, y + 0.5, 1.6, 0, 2 * Math.PI);
      ctx.fill();
      ctx.stroke();
    }
  }
}

// ---------- Documents ----------
// A JSON document as tables
function jsonToHTML(value) {
  if (Array.isArray(value)) {
    if (value.length && typeof value[0] === "object") {
      const keys = Object.keys(value[0]);
      const header = keys.map((k) => `<th>${esc(k.replaceAll("_", " "))}</th>`).join("");
      const rows = value.map((row) => `<tr>${keys.map((k) => `<td dir="auto">${esc(row[k])}</td>`).join("")}</tr>`).join("");
      return `<table><tr>${header}</tr>${rows}</table>`;
    }
    return esc(value.join(", "));
  }
  if (value && typeof value === "object") {
    const rows = Object.entries(value).map(([key, inner]) =>
      `<tr><th>${esc(capitalize(key.replaceAll("_", " ")))}</th><td dir="auto">${jsonToHTML(inner)}</td></tr>`);
    return `<table>${rows.join("")}</table>`;
  }
  return esc(value);
}

// A CSV file as a table; low neuropsychology scores (z <= -1.5) are marked
function csvToHTML(text) {
  const lines = text.trim().split(/\r?\n/);
  const table = lines.map((line) => line.match(/("([^"]|"")*"|[^,]*)(,|$)/g).slice(0, -1)
    .map((cell) => cell.replace(/,$/, "").replace(/^"|"$/g, "").replaceAll('""', '"')));
  const header = table[0];
  const zColumn = header.findIndex((h) => /^z/i.test(h.trim()));
  let html = `<table><tr>${header.map((h) => `<th>${esc(h)}</th>`).join("")}</tr>`;
  for (const row of table.slice(1)) {
    html += "<tr>";
    row.forEach((cell, i) => {
      const low = i === zColumn && Number(cell) <= -1.5;
      html += low ? `<td><strong class="t-clin">${esc(cell)}</strong> <span class="muted">low</span></td>`
                  : `<td dir="auto">${esc(cell)}</td>`;
    });
    html += "</tr>";
  }
  return html + "</table>";
}

async function showDocument({ id }) {
  const file = state.catalog.items.find((item) => item.id === id);
  const url = `${patientURL()}/file/${id}`;
  let body;
  if (file.kind === "video") body = `<video controls src="${url}"></video>`;
  else if (file.format === "json") body = jsonToHTML(await getJSON(url));
  else if (file.format === "csv") body = csvToHTML(await (await api(url)).text());
  else if (["txt", "md"].includes(file.format)) body = `<pre dir="auto">${esc(await (await api(url)).text())}</pre>`;
  else if (file.format === "pdf") body = `<iframe src="${url}" title="${esc(file.name)}"></iframe>`;
  else if (["png", "jpg", "jpeg"].includes(file.format)) body = `<img src="${url}" alt="${esc(file.name)}" style="max-width:100%">`;
  else body = `<a class="btn" href="${url}" target="_blank" rel="noopener">Open ${esc(file.name)}</a>`;
  $("#viewer").innerHTML = `<h2 style="font-size:18px">${esc(capitalize(file.modality))}</h2>
    <div class="doc" style="overflow-x:auto">${body}</div>`;
}

async function showSemiology() {
  const clinical = state.summary.clinical;
  const video = filesOf("video")[0];
  let timeline = "";
  for (const event of clinical.semiology || []) {
    const time = event.t_s != null ? formatTime(event.t_s) : "";
    timeline += `<span class="muted">${time}</span><span>${esc(capitalize(event.sign))}</span>`;
  }
  $("#viewer").innerHTML = `<h2 style="font-size:18px">Semiology</h2>
    ${clinical.aura ? `<p>Aura: ${esc(clinical.aura)}</p>` : ""}
    <div style="display:grid;grid-template-columns:48px 1fr;row-gap:6px">${timeline}</div>
    ${video ? `<video controls src="${patientURL()}/file/${video.id}" style="width:100%;max-height:420px"></video>` : ""}`;
}

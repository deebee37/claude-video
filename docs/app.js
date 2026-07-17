/* Easy Video Editor -- runs entirely in the browser via ffmpeg.wasm.
   Engine files are self-hosted in vendor/ (no CDN). */
"use strict";

const $ = (id) => document.getElementById(id);
const statusEl = $("status"), detailEl = $("statusDetail"), dotEl = $("statusDot");
const logEl = $("log"), logBox = $("logBox");
const fileBtn = $("fileBtn"), fileInput = $("fileInput"), fileNameEl = $("fileName");
const opSelect = $("opSelect"), trimFields = $("trimFields");
const runBtn = $("runBtn"), jobCard = $("jobCard"), jobStatus = $("jobStatus");
const resultCard = $("resultCard"), preview = $("preview"), saveBtn = $("saveBtn");
const engineBar = $("engineBar"), engineFill = $("engineFill");

let ffmpeg = null;
let chosenFile = null;
let lastBlobUrl = null;

function log(msg) {
  const t = new Date().toISOString().slice(11, 19);
  logEl.textContent += `[${t}] ${msg}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}
function fail(stage, msg) {
  dotEl.className = "dot error";
  statusEl.textContent = "ERROR";
  detailEl.innerHTML = `<span class="err">${stage}: ${msg}</span>`;
  logBox.open = true;
  log(`FAIL ${stage}: ${msg}`);
}
function setStage(name, detail) {
  statusEl.textContent = name;
  detailEl.textContent = detail || "";
  log(`stage: ${name}${detail ? " -- " + detail : ""}`);
}

window.addEventListener("error", (e) => log(`window.onerror: ${e.message} @ ${e.filename}:${e.lineno}`));
window.addEventListener("unhandledrejection", (e) => log(`unhandled rejection: ${e.reason}`));

/* ---------- engine boot ---------- */

async function fetchWithProgress(url, onPct) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${url} -> HTTP ${resp.status}`);
  const total = Number(resp.headers.get("Content-Length")) || 0;
  if (!resp.body || !total) return await resp.blob();
  const reader = resp.body.getReader();
  const chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.length;
    onPct(Math.round((got / total) * 100));
  }
  return new Blob(chunks);
}

async function boot() {
  setStage("BOOTING…", "Getting things ready.");

  if (window.__engineScriptFailed || typeof FFmpegWASM === "undefined") {
    fail("BOOT", "The engine script did not load. Check the Network tab for a 404 on vendor/ffmpeg.js (the vendor folder must be uploaded next to index.html).");
    return;
  }

  setStage("CHECKING FILES…", "Making sure all app pieces are here.");
  const coreURL = new URL("vendor/ffmpeg-core.js", location.href).href;
  const wasmURL = new URL("vendor/ffmpeg-core.wasm", location.href).href;

  try {
    setStage("DOWNLOADING ENGINE…", "One-time download (~31 MB). Next time it's instant.");
    engineBar.style.display = "block";
    const wasmBlob = await fetchWithProgress(wasmURL, (pct) => {
      engineFill.style.width = pct + "%";
      detailEl.textContent = `One-time download: ${pct}% of ~31 MB. Next time it's instant.`;
    });
    engineFill.style.width = "100%";

    setStage("STARTING…", "Waking up the video engine.");
    ffmpeg = new FFmpegWASM.FFmpeg();
    ffmpeg.on("log", ({ message }) => log(`ffmpeg: ${message}`));

    const loadPromise = ffmpeg.load({
      coreURL,
      wasmURL: URL.createObjectURL(wasmBlob),
    });
    const timeout = new Promise((_, rej) =>
      setTimeout(() => rej(new Error("engine did not start within 90 seconds -- the worker may be blocked; try reloading, or a different browser")), 90000));
    await Promise.race([loadPromise, timeout]);

    engineBar.style.display = "none";
    dotEl.className = "dot ready";
    setStage("READY ✅", "Pick a video to begin.");
    updateRunButton();
  } catch (err) {
    fail("ENGINE", String(err && err.message || err));
  }
}

/* ---------- UI wiring ---------- */

fileBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  chosenFile = fileInput.files[0] || null;
  fileNameEl.textContent = chosenFile ? `Chosen: ${chosenFile.name} (${fmtSize(chosenFile.size)})` : "No video chosen yet.";
  updateRunButton();
});
opSelect.addEventListener("change", () => {
  trimFields.style.display = opSelect.value === "trim" ? "block" : "none";
});

function updateRunButton() {
  runBtn.disabled = !(ffmpeg && ffmpeg.loaded && chosenFile);
}

function fmtSize(n) {
  if (n < 1024 * 1024) return Math.round(n / 1024) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}

function parseTime(text) {
  const t = (text || "").trim();
  if (!t) return null;
  if (/^\d+(\.\d+)?$/.test(t)) return parseFloat(t);
  const m = t.match(/^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$/);
  if (!m) return null;
  return (parseInt(m[1] || "0", 10) * 3600) + (parseInt(m[2], 10) * 60) + parseFloat(m[3]);
}

function outExt(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (ext === "webm") return "webm";
  if (ext === "mkv") return "mkv";
  return "mp4";
}

/* ---------- running an edit ---------- */

runBtn.addEventListener("click", async () => {
  if (!ffmpeg || !ffmpeg.loaded || !chosenFile) return;

  const op = opSelect.value;
  let args, outName, niceName;
  const ext = outExt(chosenFile.name);
  const base = chosenFile.name.replace(/\.[^.]+$/, "") || "video";
  const inName = "input." + (chosenFile.name.split(".").pop() || "mp4").toLowerCase();

  if (op === "trim") {
    const start = parseTime($("startInput").value);
    const end = parseTime($("endInput").value);
    if (start === null || end === null) { alert("Please type both times, like 0:02 and 0:08"); return; }
    if (end <= start) { alert('"Keep until" must be after "Keep from".'); return; }
    outName = "output." + ext;
    niceName = `${base}_trimmed.${ext}`;
    args = ["-ss", String(start), "-t", String(end - start), "-i", inName, "-c", "copy", outName];
  } else {
    outName = "output." + ext;
    niceName = `${base}_muted.${ext}`;
    args = ["-i", inName, "-c:v", "copy", "-an", outName];
  }

  runBtn.disabled = true;
  resultCard.style.display = "none";
  jobCard.style.display = "block";

  try {
    jobStatus.textContent = "Reading your video…";
    log(`job: ${op} -> ffmpeg ${args.join(" ")}`);
    const data = new Uint8Array(await chosenFile.arrayBuffer());
    await ffmpeg.writeFile(inName, data);

    jobStatus.textContent = "Working… (short clips take a second or two)";
    const rc = await ffmpeg.exec(args);
    if (rc !== 0) throw new Error(`ffmpeg exited with code ${rc} -- open Details below for the exact message`);

    const out = await ffmpeg.readFile(outName);
    if (!out || out.length === 0) throw new Error("the edit finished but the result was empty");

    const mime = { mp4: "video/mp4", webm: "video/webm", mkv: "video/x-matroska" }[ext];
    if (lastBlobUrl) URL.revokeObjectURL(lastBlobUrl);
    lastBlobUrl = URL.createObjectURL(new Blob([out.buffer], { type: mime }));

    preview.src = lastBlobUrl;
    saveBtn.href = lastBlobUrl;
    saveBtn.download = niceName;
    jobCard.style.display = "none";
    resultCard.style.display = "block";
    log(`job done: ${niceName} (${fmtSize(out.length)})`);

    await ffmpeg.deleteFile(inName).catch(() => {});
    await ffmpeg.deleteFile(outName).catch(() => {});
  } catch (err) {
    jobCard.style.display = "block";
    jobStatus.innerHTML = `<span class="err">Something went wrong: ${String(err && err.message || err)}</span>`;
    logBox.open = true;
    log(`job FAILED: ${err && err.stack || err}`);
  } finally {
    runBtn.disabled = false;
    updateRunButton();
  }
});

/* ---------- service worker (speeds up every visit after the first) ---------- */

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").then(
    () => log("service worker registered"),
    (e) => log(`service worker failed (app still works): ${e}`));
}

boot();

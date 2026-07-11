#!/usr/bin/env python3
"""
web_edit.py -- full browser-based UI for scripts/edit.py.

Zero dependencies: Python standard library only (no Flask, no pip installs).
Works on Windows, macOS, Linux, and Replit.

Usage:
    python3 scripts/web_edit.py           # then open http://localhost:5000
    PORT=8080 python3 scripts/web_edit.py # custom port

Features:
  * Every edit.py operation, grouped by category (trim/cut, size, speed,
    audio, enhance, style, text/watermark, combine two videos, convert)
  * Background jobs -- big videos don't hang the browser; the job page
    auto-refreshes until the edit finishes
  * In-browser preview of results (with seeking) + download
  * Library page listing everything in output/
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import (EDIT_SCRIPT, OUTPUT_DIR, REPO_ROOT, build_output_path,
                   choose_output_suffix, fmt_duration, fmt_file_size,
                   probe_video)

VERSION = "2.0.0"
INPUT_DIR = REPO_ROOT / "input"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
PREVIEWABLE = (".mp4", ".webm", ".mov")
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
EDIT_TIMEOUT_SECS = 1800  # 30 min
LOOK_NAMES = ["cinematic", "moody", "warm", "cool", "bw",
              "vintage", "teal-orange", "film"]

# ---------------------------------------------------------------------------
# Operation registry.
#
# Field spec:
#   {"name","label","type"} where type is:
#     "text"   -> text input; value appended after the op flag (arg) or
#                 after the field's own "flag" (extra)
#     "select" -> dropdown; "options": [...]
#     "file"   -> upload; "kind": video|audio|image; saved path appended
#   args   = values appended right after the operation flag, in order
#   extras = optional fields, each with its own "flag"; appended only
#            when a value is provided ("required": True forces a value)
#   "optional_value": main flag takes 0-or-1 values (vignette, grain)
# ---------------------------------------------------------------------------
OPERATIONS: list[dict] = [
    # --- Trim & Cut ---
    {"key": "trim", "cat": "Trim & Cut", "name": "Trim (keep a section)", "flag": "--trim",
     "args": [{"name": "start", "label": "Start time (SS, MM:SS, or HH:MM:SS)", "type": "text", "placeholder": "0:05"},
              {"name": "end", "label": "End time (or 'end')", "type": "text", "placeholder": "0:30"}]},
    {"key": "cut", "cat": "Trim & Cut", "name": "Cut (remove a section)", "flag": "--cut",
     "args": [{"name": "start", "label": "Start of section to remove", "type": "text", "placeholder": "0:10"},
              {"name": "end", "label": "End of section to remove", "type": "text", "placeholder": "0:20"}]},
    # --- Size & Framing ---
    {"key": "resize", "cat": "Size & Framing", "name": "Resize", "flag": "--resize",
     "args": [{"name": "size", "label": "New size as WxH", "type": "text", "placeholder": "1280x720"}]},
    {"key": "rotate", "cat": "Size & Framing", "name": "Rotate", "flag": "--rotate",
     "args": [{"name": "degrees", "label": "Degrees", "type": "select", "options": ["90", "180", "270"]}]},
    {"key": "crop", "cat": "Size & Framing", "name": "Crop", "flag": "--crop",
     "args": [{"name": "rect", "label": "Crop as width:height:x:y", "type": "text", "placeholder": "640:480:0:0"}]},
    {"key": "letterbox", "cat": "Size & Framing", "name": "Letterbox (pad to ratio)", "flag": "--letterbox",
     "args": [{"name": "ratio", "label": "Aspect ratio", "type": "text", "placeholder": "16:9"}]},
    # --- Speed & Time ---
    {"key": "speed", "cat": "Speed & Time", "name": "Speed", "flag": "--speed",
     "args": [{"name": "factor", "label": "Speed factor (2.0 = double, 0.5 = half)", "type": "text", "placeholder": "2.0"}]},
    {"key": "fps", "cat": "Speed & Time", "name": "FPS (change frame rate)", "flag": "--fps",
     "args": [{"name": "fps", "label": "Target frame rate", "type": "text", "placeholder": "30"}]},
    {"key": "reverse", "cat": "Speed & Time", "name": "Reverse", "flag": "--reverse", "args": []},
    {"key": "loop", "cat": "Speed & Time", "name": "Loop N times", "flag": "--loop",
     "args": [{"name": "n", "label": "Number of loops", "type": "text", "placeholder": "3"}]},
    {"key": "boomerang", "cat": "Speed & Time", "name": "Boomerang (forward then back)", "flag": "--boomerang", "args": []},
    # --- Audio ---
    {"key": "mute", "cat": "Audio", "name": "Mute (remove audio)", "flag": "--mute", "args": []},
    {"key": "volume", "cat": "Audio", "name": "Volume", "flag": "--volume",
     "args": [{"name": "level", "label": "Level (1.0 = normal, 2.0 = double)", "type": "text", "placeholder": "1.5"}]},
    {"key": "normalize_audio", "cat": "Audio", "name": "Normalize audio", "flag": "--normalize-audio", "args": []},
    {"key": "fade_in", "cat": "Audio", "name": "Fade in", "flag": "--fade-in",
     "args": [{"name": "secs", "label": "Fade-in seconds", "type": "text", "placeholder": "2"}]},
    {"key": "fade_out", "cat": "Audio", "name": "Fade out", "flag": "--fade-out",
     "args": [{"name": "secs", "label": "Fade-out seconds", "type": "text", "placeholder": "2"}]},
    {"key": "replace_audio", "cat": "Audio", "name": "Replace audio track", "flag": "--replace-audio",
     "args": [{"name": "audio", "label": "Audio file (MP3/WAV/M4A...)", "type": "file", "kind": "audio"}]},
    # --- Enhance ---
    {"key": "sharpen", "cat": "Enhance", "name": "Sharpen", "flag": "--sharpen", "args": []},
    {"key": "denoise", "cat": "Enhance", "name": "Denoise", "flag": "--denoise", "args": []},
    {"key": "stabilize", "cat": "Enhance", "name": "Stabilize (shaky video)", "flag": "--stabilize", "args": []},
    {"key": "blur", "cat": "Enhance", "name": "Blur a region", "flag": "--blur",
     "args": [{"name": "rect", "label": "Region as width:height:x:y", "type": "text", "placeholder": "200:100:50:50"}]},
    # --- Style ---
    {"key": "look", "cat": "Style", "name": "Color look (preset grade)", "flag": "--look",
     "args": [{"name": "look", "label": "Look", "type": "select", "options": LOOK_NAMES}]},
    {"key": "vignette", "cat": "Style", "name": "Vignette", "flag": "--vignette", "optional_value": True,
     "args": [{"name": "strength", "label": "Strength 0-1 (blank = default 0.5)", "type": "text",
               "placeholder": "0.5", "optional": True}]},
    {"key": "grain", "cat": "Style", "name": "Film grain", "flag": "--grain", "optional_value": True,
     "args": [{"name": "strength", "label": "Strength (blank = default 15)", "type": "text",
               "placeholder": "15", "optional": True}]},
    # --- Text & Watermark ---
    {"key": "text", "cat": "Text & Watermark", "name": "Text overlay", "flag": "--text",
     "args": [{"name": "text", "label": "Text to show", "type": "text", "placeholder": "Hello!"}],
     "extras": [
        {"name": "pos", "label": "Position", "type": "select", "flag": "--text-position",
         "options": ["", "top", "center", "bottom"]},
        {"name": "size", "label": "Font size (default 48)", "type": "text", "flag": "--text-size", "placeholder": "48"},
        {"name": "color", "label": "Color (default white)", "type": "text", "flag": "--text-color", "placeholder": "white"},
        {"name": "tstart", "label": "Show from time (optional)", "type": "text", "flag": "--text-start", "placeholder": "0:05"},
        {"name": "tend", "label": "Hide at time (optional)", "type": "text", "flag": "--text-end", "placeholder": "0:15"}]},
    {"key": "watermark_text", "cat": "Text & Watermark", "name": "Watermark (text)", "flag": "--watermark-text",
     "args": [{"name": "text", "label": "Watermark text", "type": "text", "placeholder": "My Channel"}],
     "extras": [
        {"name": "pos", "label": "Corner", "type": "select", "flag": "--watermark-position",
         "options": ["", "top-right", "top-left", "bottom-right", "bottom-left", "center"]},
        {"name": "opacity", "label": "Opacity 0-1 (default 0.65)", "type": "text", "flag": "--watermark-opacity", "placeholder": "0.65"},
        {"name": "fsize", "label": "Font size (default 36)", "type": "text", "flag": "--watermark-font-size", "placeholder": "36"}]},
    {"key": "watermark_image", "cat": "Text & Watermark", "name": "Watermark (image/logo)", "flag": "--watermark-image",
     "args": [{"name": "image", "label": "Logo image (PNG/JPG)", "type": "file", "kind": "image"}],
     "extras": [
        {"name": "pos", "label": "Corner", "type": "select", "flag": "--watermark-position",
         "options": ["", "top-right", "top-left", "bottom-right", "bottom-left", "center"]},
        {"name": "opacity", "label": "Opacity 0-1 (default 0.65)", "type": "text", "flag": "--watermark-opacity", "placeholder": "0.65"},
        {"name": "scale", "label": "Scale as fraction of width (default 0.15)", "type": "text", "flag": "--watermark-scale", "placeholder": "0.15"}]},
    # --- Combine (two videos) ---
    {"key": "concat", "cat": "Combine two videos", "name": "Join (play one after another)", "flag": "--concat",
     "args": [{"name": "second", "label": "Second video", "type": "file", "kind": "video"}]},
    {"key": "side_by_side", "cat": "Combine two videos", "name": "Side by side", "flag": "--side-by-side",
     "args": [{"name": "second", "label": "Second video", "type": "file", "kind": "video"}]},
    {"key": "stack", "cat": "Combine two videos", "name": "Stack (top / bottom)", "flag": "--stack",
     "args": [{"name": "second", "label": "Second video (bottom)", "type": "file", "kind": "video"}]},
    {"key": "crossfade", "cat": "Combine two videos", "name": "Crossfade into another video", "flag": "--crossfade",
     "args": [{"name": "second", "label": "Second video", "type": "file", "kind": "video"}],
     "extras": [{"name": "dur", "label": "Crossfade seconds (default 1.0)", "type": "text",
                 "flag": "--crossfade-duration", "placeholder": "1.0"}]},
    {"key": "overlay", "cat": "Combine two videos", "name": "Overlay a video on top", "flag": "--overlay",
     "args": [{"name": "second", "label": "Video to overlay", "type": "file", "kind": "video"}],
     "extras": [
        {"name": "x", "label": "X position (default 0)", "type": "text", "flag": "--overlay-x", "placeholder": "0"},
        {"name": "y", "label": "Y position (default 0)", "type": "text", "flag": "--overlay-y", "placeholder": "0"},
        {"name": "scale", "label": "Overlay width px (optional)", "type": "text", "flag": "--overlay-scale", "placeholder": "320"}]},
    {"key": "pip", "cat": "Combine two videos", "name": "Picture-in-picture", "flag": "--pip",
     "args": [{"name": "second", "label": "Small (PiP) video", "type": "file", "kind": "video"}],
     "extras": [
        {"name": "pos", "label": "Corner", "type": "select", "flag": "--pip-position",
         "options": ["", "top-right", "top-left", "bottom-right", "bottom-left"]},
        {"name": "width", "label": "PiP width px (default 320)", "type": "text", "flag": "--pip-width", "placeholder": "320"}]},
    # --- Convert ---
    {"key": "convert", "cat": "Convert", "name": "Convert format", "flag": "--convert",
     "args": [],
     "extras": [{"name": "format", "label": "Target format", "type": "select", "flag": "--format",
                 "options": ["mp4", "mov", "mkv", "webm"], "required": True}]},
]
OPS_BY_KEY = {op["key"]: op for op in OPERATIONS}
CATEGORIES: list[str] = []
for _op in OPERATIONS:
    if _op["cat"] not in CATEGORIES:
        CATEGORIES.append(_op["cat"])

KIND_EXTS = {"video": VIDEO_EXTENSIONS, "audio": AUDIO_EXTENSIONS, "image": IMAGE_EXTENSIONS}
KIND_ACCEPT = {"video": "video/*", "audio": "audio/*", "image": "image/*"}

# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def start_job(op_name: str, cmd: list[str], output: Path) -> str:
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "op": op_name, "output": output,
                        "started": time.time(), "error": ""}

    def worker() -> None:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=EDIT_TIMEOUT_SECS)
            ok = proc.returncode == 0 and output.exists()
            err = "" if ok else (proc.stderr or proc.stdout or "").strip()[-2000:]
        except subprocess.TimeoutExpired:
            ok, err = False, f"Timed out after {EDIT_TIMEOUT_SECS // 60} minutes."
        except Exception as exc:  # noqa: BLE001
            ok, err = False, str(exc)
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "done" if ok else "failed"
            JOBS[job_id]["error"] = err

    threading.Thread(target=worker, daemon=True).start()
    return job_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def safe_name(name: str) -> str:
    name = os.path.basename(name.replace("\\", "/"))
    return re.sub(r"[^\w\-.]", "_", name) or "upload"


def list_input_videos() -> list[Path]:
    if not INPUT_DIR.exists():
        return []
    return sorted(p for p in INPUT_DIR.iterdir()
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)


def list_outputs() -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    files = [p for p in OUTPUT_DIR.iterdir()
             if p.is_file() and not p.name.startswith(".")
             and p.suffix.lower() in VIDEO_EXTENSIONS]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def parse_multipart(body: bytes, content_type: str) -> dict[str, dict]:
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    if not m:
        raise ValueError("no multipart boundary")
    boundary = m.group(1).encode()
    fields: dict[str, dict] = {}
    for part in body.split(b"--" + boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        raw_headers, data = part.split(b"\r\n\r\n", 1)
        headers = raw_headers.decode("utf-8", "replace")
        dm = re.search(r'name="([^"]*)"', headers)
        if not dm:
            continue
        name = dm.group(1)
        fm = re.search(r'filename="([^"]*)"', headers)
        if fm:
            fields[name] = {"filename": fm.group(1), "data": data}
        else:
            fields[name] = {"value": data.decode("utf-8", "replace").strip()}
    return fields


def save_upload(field: dict, kind: str) -> Path | None:
    """Save an uploaded file field to input/; returns path or None."""
    if not (field and field.get("filename") and field.get("data")):
        return None
    fname = safe_name(field["filename"])
    if not fname.lower().endswith(KIND_EXTS[kind]):
        raise ValueError(
            f"'{fname}' is not a supported {kind} file "
            f"({', '.join(KIND_EXTS[kind])})")
    INPUT_DIR.mkdir(exist_ok=True)
    path = INPUT_DIR / fname
    path.write_bytes(field["data"])
    return path


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
STYLE = """
  :root { --accent:#2563eb; --ok:#16a34a; --err:#dc2626; --muted:#64748b; }
  * { box-sizing:border-box; }
  body { font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
         margin:0; background:#f1f5f9; color:#0f172a; }
  .wrap { max-width:640px; margin:0 auto; padding:16px; }
  header { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin:10px 0 14px; }
  h1 { font-size:1.35rem; margin:0; }
  .sub { color:var(--muted); font-size:.85rem; }
  nav a { margin-right:14px; color:var(--accent); font-weight:600; text-decoration:none; }
  nav a.active { color:#0f172a; }
  .card { background:#fff; border-radius:12px; padding:16px;
          box-shadow:0 1px 3px rgba(0,0,0,.08); margin-bottom:16px; }
  label { display:block; font-weight:600; margin:12px 0 4px; font-size:.95rem; }
  select,input[type=text],input[type=file] { width:100%; padding:11px;
      border:1px solid #cbd5e1; border-radius:8px; font-size:1rem; background:#fff; }
  .btn { display:block; width:100%; padding:14px; margin-top:18px; border:0;
         border-radius:8px; background:var(--accent); color:#fff;
         font-size:1.1rem; font-weight:700; cursor:pointer; }
  .btn:active { opacity:.85; }
  .ok { border-left:5px solid var(--ok); }
  .err { border-left:5px solid var(--err); }
  .run { border-left:5px solid var(--accent); }
  .dl { display:block; text-align:center; padding:13px; margin-top:12px;
        border-radius:8px; background:var(--ok); color:#fff;
        font-weight:700; text-decoration:none; font-size:1.05rem; }
  a.link { color:var(--accent); }
  pre { white-space:pre-wrap; word-break:break-word; background:#f8fafc;
        padding:10px; border-radius:8px; font-size:.78rem; max-height:260px; overflow:auto; }
  .hint { color:var(--muted); font-size:.82rem; margin-top:4px; }
  video { width:100%; border-radius:8px; background:#000; margin-top:10px; }
  table { width:100%; border-collapse:collapse; font-size:.9rem; }
  td,th { padding:8px 6px; border-bottom:1px solid #e2e8f0; text-align:left;
          word-break:break-all; }
  .meta { color:var(--muted); font-size:.8rem; white-space:nowrap; }
  details summary { cursor:pointer; font-weight:600; margin:10px 0 4px; color:var(--muted); }
  .spinner { display:inline-block; width:16px; height:16px; border:3px solid #cbd5e1;
             border-top-color:var(--accent); border-radius:50%;
             animation:sp 1s linear infinite; vertical-align:-3px; margin-right:8px; }
  @keyframes sp { to { transform:rotate(360deg); } }
"""


def page(title: str, body: str, active: str = "edit", refresh: int = 0) -> str:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{meta}<title>{html.escape(title)}</title><style>{STYLE}</style></head>
<body><div class="wrap">
<header><h1>&#127916; Video Editor</h1>
<span class="sub">web_edit {VERSION}</span></header>
<nav>
<a href="/" class="{'active' if active == 'edit' else ''}">Edit</a>
<a href="/library" class="{'active' if active == 'library' else ''}">Library</a>
</nav>
{body}
</div></body></html>"""


def field_html_spec(op: dict) -> dict:
    """JSON-safe spec of an op's form fields for the client-side renderer."""
    fields = []
    for f in op.get("args", []):
        fields.append({"name": "a_" + f["name"], "label": f["label"],
                       "type": f["type"], "options": f.get("options"),
                       "placeholder": f.get("placeholder", ""),
                       "accept": KIND_ACCEPT.get(f.get("kind", ""), ""),
                       "optional": bool(f.get("optional"))})
    for f in op.get("extras", []):
        fields.append({"name": "x_" + f["name"], "label": f["label"],
                       "type": f["type"], "options": f.get("options"),
                       "placeholder": f.get("placeholder", ""),
                       "accept": KIND_ACCEPT.get(f.get("kind", ""), ""),
                       "optional": not f.get("required", False)})
    return {"key": op["key"], "fields": fields}


def render_form() -> str:
    videos = list_input_videos()
    vid_opts = ""
    for v in videos:
        info = probe_video(v) or ""
        label = v.name + (f" ({info})" if info else "")
        vid_opts += f'<option value="{html.escape(v.name)}">{html.escape(label)}</option>'
    existing_block = ""
    if videos:
        existing_block = f"""
  <label>&hellip;or pick a video already in input/</label>
  <select name="existing"><option value="">-- none, use upload above --</option>{vid_opts}</select>"""

    op_opts = ""
    for cat in CATEGORIES:
        op_opts += f'<optgroup label="{html.escape(cat)}">'
        for op in OPERATIONS:
            if op["cat"] == cat:
                op_opts += f'<option value="{op["key"]}">{html.escape(op["name"])}</option>'
        op_opts += "</optgroup>"

    specs = json.dumps([field_html_spec(op) for op in OPERATIONS])

    body = f"""
<form class="card" method="post" action="/edit" enctype="multipart/form-data">
  <label>1. Upload a video</label>
  <input type="file" name="video" accept="video/*">
  {existing_block}
  <label>2. Choose an operation</label>
  <select name="op" id="op" onchange="renderFields()">{op_opts}</select>
  <div id="dynfields"></div>
  <details><summary>Advanced: output quality</summary>
    <label>Quality</label>
    <select name="quality">
      <option value="">default (standard)</option>
      <option value="preview">preview (fast, small)</option>
      <option value="high">high</option>
      <option value="master">master (biggest)</option>
    </select>
  </details>
  <button class="btn" type="submit">&#9654; Run Edit</button>
  <div class="hint">Edits run in the background &mdash; you'll land on a status
  page that refreshes itself. Output is saved to <code>output/</code>.</div>
</form>
<script>
const OPS = {specs};
function esc(s) {{ const d = document.createElement('span'); d.textContent = s || ''; return d.innerHTML; }}
function renderFields() {{
  const key = document.getElementById('op').value;
  const op = OPS.find(o => o.key === key);
  let out = '';
  for (const f of op.fields) {{
    out += `<label>${{esc(f.label)}}${{f.optional ? ' <span class="hint">(optional)</span>' : ''}}</label>`;
    if (f.type === 'select') {{
      out += `<select name="${{f.name}}">` + f.options.map(o =>
        `<option value="${{esc(o)}}">${{o === '' ? '(default)' : esc(o)}}</option>`).join('') + `</select>`;
    }} else if (f.type === 'file') {{
      out += `<input type="file" name="${{f.name}}" accept="${{f.accept}}">`;
    }} else {{
      out += `<input type="text" name="${{f.name}}" placeholder="${{esc(f.placeholder)}}">`;
    }}
  }}
  document.getElementById('dynfields').innerHTML = out;
}}
renderFields();
</script>"""
    return page("Video Editor", body, "edit")


def render_job(job_id: str) -> tuple[int, str, str]:
    """Returns (http_code, html, '') for the job status page."""
    with JOBS_LOCK:
        job = dict(JOBS.get(job_id) or {})
    if not job:
        return 404, page("Job not found",
                         '<div class="card err"><h2>Job not found</h2>'
                         '<p><a class="link" href="/">&larr; Back</a></p></div>'), ""
    elapsed = fmt_duration(time.time() - job["started"])
    output: Path = job["output"]
    if job["status"] == "running":
        body = (f'<div class="card run"><h2><span class="spinner"></span>'
                f'Working&hellip;</h2>'
                f'<p><b>{html.escape(job["op"])}</b> &mdash; running for {elapsed}. '
                f'This page refreshes itself every 3 seconds; big videos can '
                f'take a while.</p></div>')
        return 200, page("Working...", body, "edit", refresh=3), ""
    if job["status"] == "failed":
        body = (f'<div class="card err"><h2>Edit failed</h2>'
                f'<p><b>{html.escape(job["op"])}</b> hit a problem:</p>'
                f'<pre>{html.escape(job["error"] or "no error output")}</pre>'
                f'<p><a class="link" href="/">&larr; Try again</a></p></div>')
        return 200, page("Edit failed", body, "edit"), ""
    size = fmt_file_size(output.stat().st_size) if output.exists() else "?"
    link = f"/files/{urllib.parse.quote(output.name)}"
    preview = ""
    if output.suffix.lower() in PREVIEWABLE:
        preview = f'<video controls preload="metadata" src="{link}"></video>'
    body = (f'<div class="card ok"><h2>Done!</h2>'
            f'<p><b>{html.escape(job["op"])}</b> finished in {elapsed}. '
            f'Output: <code>{html.escape(output.name)}</code> ({size})</p>'
            f'{preview}'
            f'<a class="dl" href="{link}" download>&#11015; Download result</a>'
            f'<p><a class="link" href="/">&larr; Edit another video</a> &middot; '
            f'<a class="link" href="/library">Open library</a></p></div>')
    return 200, page("Done", body, "edit"), ""


def render_library() -> str:
    files = list_outputs()
    if not files:
        body = ('<div class="card"><h2>Library is empty</h2>'
                '<p>Finished edits land here. '
                '<a class="link" href="/">Run your first edit</a>.</p></div>')
        return page("Library", body, "library")
    rows = ""
    for f in files:
        link = f"/files/{urllib.parse.quote(f.name)}"
        when = time.strftime("%b %d %H:%M", time.localtime(f.stat().st_mtime))
        prev = (f' &middot; <a class="link" href="/view/{urllib.parse.quote(f.name)}">Preview</a>'
                if f.suffix.lower() in PREVIEWABLE else "")
        rows += (f'<tr><td>{html.escape(f.name)}<div class="meta">{when} &middot; '
                 f'{fmt_file_size(f.stat().st_size)}</div></td>'
                 f'<td class="meta"><a class="link" href="{link}" download>Download</a>{prev}</td></tr>')
    body = (f'<div class="card"><h2>Output library ({len(files)})</h2>'
            f'<table>{rows}</table></div>')
    return page("Library", body, "library")


def render_view(name: str) -> tuple[int, str]:
    target = (OUTPUT_DIR / safe_name(name)).resolve()
    if not str(target).startswith(str(OUTPUT_DIR.resolve())) or not target.is_file():
        return 404, page("Not found", '<div class="card err"><h2>File not found</h2></div>', "library")
    link = f"/files/{urllib.parse.quote(target.name)}"
    body = (f'<div class="card"><h2>{html.escape(target.name)}</h2>'
            f'<video controls preload="metadata" src="{link}"></video>'
            f'<a class="dl" href="{link}" download>&#11015; Download</a>'
            f'<p><a class="link" href="/library">&larr; Back to library</a></p></div>')
    return 200, page(target.name, body, "library")


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = f"web_edit/{VERSION}"
    protocol_version = "HTTP/1.1"

    def _send_html(self, code: int, page_html: str) -> None:
        data = page_html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _error_card(self, code: int, title: str, detail: str = "") -> None:
        body = (f'<div class="card err"><h2>{html.escape(title)}</h2>'
                f'{detail}<p><a class="link" href="/">&larr; Back</a></p></div>')
        self._send_html(code, page(title, body))

    # ---- GET ----
    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in ("", "/"):
            self._send_html(200, render_form())
        elif path == "/library":
            self._send_html(200, render_library())
        elif path.startswith("/job/"):
            code, html_page, _ = render_job(path[len("/job/"):])
            self._send_html(code, html_page)
        elif path.startswith("/view/"):
            code, html_page = render_view(urllib.parse.unquote(path[len("/view/"):]))
            self._send_html(code, html_page)
        elif path.startswith("/files/"):
            self._serve_file(path)
        else:
            self._error_card(404, "Page not found")

    def _serve_file(self, path: str) -> None:
        name = safe_name(urllib.parse.unquote(path[len("/files/"):]))
        target = (OUTPUT_DIR / name).resolve()
        if not str(target).startswith(str(OUTPUT_DIR.resolve())) or not target.is_file():
            self._error_card(404, "File not found")
            return
        size = target.stat().st_size
        ctype = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
                 "mkv": "video/x-matroska"}.get(target.suffix.lower().lstrip("."),
                                                "application/octet-stream")
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
                elif not m.group(1):
                    start = 0
        if rng and start <= end < size:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            start, end = 0, size - 1
            self.send_response(200)
        length = end - start + 1
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with target.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    # ---- POST ----
    def do_POST(self) -> None:
        if urllib.parse.urlparse(self.path).path != "/edit":
            self._error_card(404, "Page not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self._error_card(400, "Upload problem",
                             f"<p>Upload size must be between 1 byte and "
                             f"{fmt_file_size(MAX_UPLOAD_BYTES)}.</p>")
            return
        try:
            fields = parse_multipart(self.rfile.read(length),
                                     self.headers.get("Content-Type", ""))
        except Exception as exc:  # noqa: BLE001
            self._error_card(400, "Could not read the form",
                             f"<pre>{html.escape(str(exc))}</pre>")
            return
        try:
            job_id = self._start_edit(fields)
        except ValueError as exc:
            self._error_card(400, "Can't run that edit", f"<p>{html.escape(str(exc))}</p>")
            return
        self._redirect(f"/job/{job_id}")

    def _start_edit(self, fields: dict) -> str:
        op = OPS_BY_KEY.get(fields.get("op", {}).get("value", ""))
        if not op:
            raise ValueError("Unknown operation.")

        # Input video: upload wins, else existing pick.
        INPUT_DIR.mkdir(exist_ok=True)
        video = save_upload(fields.get("video"), "video")
        if video is None:
            existing = fields.get("existing", {}).get("value", "")
            if existing:
                cand = (INPUT_DIR / safe_name(existing)).resolve()
                if str(cand).startswith(str(INPUT_DIR.resolve())) and cand.is_file():
                    video = cand
        if video is None:
            raise ValueError("Upload a video or pick one from the input/ list.")

        cmd = [sys.executable, str(EDIT_SCRIPT), str(video), op["flag"]]

        # Main args
        for f in op.get("args", []):
            fname = "a_" + f["name"]
            if f["type"] == "file":
                path = save_upload(fields.get(fname), f["kind"])
                if path is None:
                    raise ValueError(f"{f['label']} is required.")
                cmd.append(str(path))
            else:
                val = fields.get(fname, {}).get("value", "")
                if not val and f.get("optional") and op.get("optional_value"):
                    continue  # flag-only (e.g. --vignette with default strength)
                if not val:
                    raise ValueError(f"{f['label']} is required.")
                cmd.append(val)

        # Extra flags
        for f in op.get("extras", []):
            fname = "x_" + f["name"]
            if f["type"] == "file":
                path = save_upload(fields.get(fname), f["kind"])
                if path is not None:
                    cmd += [f["flag"], str(path)]
                elif f.get("required"):
                    raise ValueError(f"{f['label']} is required.")
                continue
            val = fields.get(fname, {}).get("value", "")
            if val:
                cmd += [f["flag"], val]
            elif f.get("required"):
                raise ValueError(f"{f['label']} is required.")

        quality = fields.get("quality", {}).get("value", "")
        if quality:
            cmd += ["--quality", quality]

        # Output path (convert changes the extension)
        if op["key"] == "convert":
            suffix = "." + fields.get("x_format", {}).get("value", "mp4")
        else:
            suffix = choose_output_suffix(video)
        OUTPUT_DIR.mkdir(exist_ok=True)
        output = build_output_path(video.stem, op["key"], suffix)
        cmd += ["--output", str(output)]

        return start_job(op["name"], cmd, output)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[web_edit] %s\n" % (fmt % args))


def main() -> None:
    if not shutil.which("ffmpeg"):
        print("WARNING: ffmpeg not found on PATH -- edits will fail. "
              "Install it first (Windows: winget install ffmpeg).")
    port = int(os.environ.get("PORT", "5000"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"web_edit {VERSION} running -- {len(OPERATIONS)} operations.")
    print(f"  Open:  http://localhost:{port}")
    print("  Stop:  Ctrl+C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

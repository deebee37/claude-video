#!/usr/bin/env python3
"""
web_edit.py -- browser-based UI for scripts/edit.py.

Zero dependencies: Python standard library only (no Flask, no pip installs).
Works on Windows, macOS, Linux, and Replit.

Usage:
    python3 scripts/web_edit.py           # then open http://localhost:5000
    PORT=8080 python3 scripts/web_edit.py # custom port

Upload a video (or pick one already in input/), choose an operation,
fill in the fields, hit Run Edit. Output lands in output/ with a
download link.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import (EDIT_SCRIPT, OUTPUT_DIR, REPO_ROOT, build_output_path,
                   choose_output_suffix, fmt_file_size, probe_video)

VERSION = "1.0.0"
INPUT_DIR = REPO_ROOT / "input"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
EDIT_TIMEOUT_SECS = 1800  # 30 min

# Mirrors easy_edit.py OPERATIONS -- same flags, same order.
OPERATIONS: list[dict] = [
    {"key": "trim", "name": "Trim (keep a section)", "flag": "--trim",
     "fields": [{"name": "start", "label": "Start time (SS, MM:SS, or HH:MM:SS)", "placeholder": "0:05"},
                {"name": "end", "label": "End time (or 'end')", "placeholder": "0:30"}]},
    {"key": "cut", "name": "Cut (remove a section)", "flag": "--cut",
     "fields": [{"name": "start", "label": "Start of section to remove", "placeholder": "0:10"},
                {"name": "end", "label": "End of section to remove", "placeholder": "0:20"}]},
    {"key": "resize", "name": "Resize", "flag": "--resize",
     "fields": [{"name": "size", "label": "New size as WxH", "placeholder": "1280x720"}]},
    {"key": "rotate", "name": "Rotate", "flag": "--rotate",
     "fields": [{"name": "degrees", "label": "Degrees (90, 180, or 270)", "placeholder": "90"}]},
    {"key": "speed", "name": "Speed", "flag": "--speed",
     "fields": [{"name": "factor", "label": "Speed factor (2.0 = double, 0.5 = half)", "placeholder": "2.0"}]},
    {"key": "fps", "name": "FPS (change frame rate)", "flag": "--fps",
     "fields": [{"name": "fps", "label": "Target frame rate", "placeholder": "30"}]},
    {"key": "normalize_audio", "name": "Normalize audio", "flag": "--normalize-audio", "fields": []},
    {"key": "sharpen", "name": "Sharpen", "flag": "--sharpen", "fields": []},
    {"key": "denoise", "name": "Denoise", "flag": "--denoise", "fields": []},
    {"key": "watermark_text", "name": "Watermark (text)", "flag": "--watermark-text",
     "fields": [{"name": "text", "label": "Watermark text", "placeholder": "My Channel"}]},
    {"key": "watermark_image", "name": "Watermark (image/logo)", "flag": "--watermark-image",
     "fields": [], "needs_image": True},
]
OPS_BY_KEY = {op["key"]: op for op in OPERATIONS}


def safe_name(name: str) -> str:
    name = os.path.basename(name.replace("\\", "/"))
    return re.sub(r"[^\w\-.]", "_", name) or "upload"


def list_input_videos() -> list[Path]:
    if not INPUT_DIR.exists():
        return []
    return sorted(p for p in INPUT_DIR.iterdir()
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)


def parse_multipart(body: bytes, content_type: str) -> dict[str, dict]:
    """Minimal multipart/form-data parser. Returns {name: {value|filename+data}}."""
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


PAGE_TOP = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Video Editor</title>
<style>
  :root {{ --accent:#2563eb; --ok:#16a34a; --err:#dc2626; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
         margin:0; background:#f1f5f9; color:#0f172a; }}
  .wrap {{ max-width:560px; margin:0 auto; padding:16px; }}
  h1 {{ font-size:1.4rem; margin:12px 0 2px; }}
  .sub {{ color:#64748b; font-size:.9rem; margin-bottom:16px; }}
  .card {{ background:#fff; border-radius:12px; padding:16px;
           box-shadow:0 1px 3px rgba(0,0,0,.08); margin-bottom:16px; }}
  label {{ display:block; font-weight:600; margin:12px 0 4px; font-size:.95rem; }}
  select,input[type=text],input[type=file] {{ width:100%; padding:12px;
      border:1px solid #cbd5e1; border-radius:8px; font-size:1rem; background:#fff; }}
  .btn {{ display:block; width:100%; padding:14px; margin-top:18px; border:0;
         border-radius:8px; background:var(--accent); color:#fff;
         font-size:1.1rem; font-weight:700; cursor:pointer; }}
  .btn:active {{ opacity:.85; }}
  .ok {{ border-left:5px solid var(--ok); }}
  .err {{ border-left:5px solid var(--err); }}
  .dl {{ display:block; text-align:center; padding:14px; margin-top:12px;
        border-radius:8px; background:var(--ok); color:#fff;
        font-weight:700; text-decoration:none; font-size:1.05rem; }}
  a.back {{ color:var(--accent); }}
  pre {{ white-space:pre-wrap; word-break:break-word; background:#f8fafc;
        padding:10px; border-radius:8px; font-size:.8rem; }}
  .hint {{ color:#64748b; font-size:.82rem; margin-top:4px; }}
  .spin {{ display:none; text-align:center; color:#64748b; margin-top:14px; }}
</style></head><body><div class="wrap">
<h1>&#127916; Video Editor</h1>
<div class="sub">web_edit {version} &middot; 11 operations &middot; output saved to <code>output/</code></div>
"""

PAGE_BOTTOM = "</div></body></html>"


def render_form() -> str:
    videos = list_input_videos()
    vid_opts = ""
    for v in videos:
        info = probe_video(v) or ""
        label = f"{v.name}" + (f" ({info})" if info else "")
        vid_opts += f'<option value="{html.escape(v.name)}">{html.escape(label)}</option>'
    op_opts = "".join(
        f'<option value="{op["key"]}">{html.escape(op["name"])}</option>'
        for op in OPERATIONS)
    fields_json_rows = []
    for op in OPERATIONS:
        row = {"key": op["key"], "fields": op["fields"],
               "needs_image": bool(op.get("needs_image"))}
        fields_json_rows.append(row)
    import json as _json
    fields_json = _json.dumps(fields_json_rows)

    existing_block = ""
    if videos:
        existing_block = f"""
  <label>&hellip;or pick a video already in input/</label>
  <select name="existing"><option value="">-- none, use upload above --</option>{vid_opts}</select>"""

    return PAGE_TOP.format(version=VERSION) + f"""
<form class="card" method="post" action="/edit" enctype="multipart/form-data"
      onsubmit="document.getElementById('spin').style.display='block'">
  <label>1. Upload a video</label>
  <input type="file" name="video" accept="video/*">
  {existing_block}
  <label>2. Choose an operation</label>
  <select name="op" id="op" onchange="renderFields()">{op_opts}</select>
  <div id="dynfields"></div>
  <button class="btn" type="submit">&#9654; Run Edit</button>
  <div class="spin" id="spin">Working&hellip; this can take a while for big videos. Leave this page open.</div>
</form>
<script>
const OPS = {fields_json};
function renderFields() {{
  const key = document.getElementById('op').value;
  const op = OPS.find(o => o.key === key);
  let out = '';
  for (const f of op.fields) {{
    out += `<label>${{f.label}}</label>` +
           `<input type="text" name="f_${{f.name}}" placeholder="${{f.placeholder}}" >`;
  }}
  if (op.needs_image) {{
    out += `<label>Watermark image (PNG/JPG)</label>` +
           `<input type="file" name="wm_image" accept="image/*">`;
  }}
  document.getElementById('dynfields').innerHTML = out;
}}
renderFields();
</script>
""" + PAGE_BOTTOM


def render_result(ok: bool, title: str, body_html: str) -> str:
    cls = "ok" if ok else "err"
    return (PAGE_TOP.format(version=VERSION)
            + f'<div class="card {cls}"><h2>{html.escape(title)}</h2>{body_html}'
              '<p><a class="back" href="/">&larr; Edit another video</a></p></div>'
            + PAGE_BOTTOM)


class Handler(BaseHTTPRequestHandler):
    server_version = f"web_edit/{VERSION}"

    def _send_html(self, code: int, page: str) -> None:
        data = page.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/" or path == "":
            self._send_html(200, render_form())
        elif path.startswith("/files/"):
            self._serve_output(path)
        else:
            self._send_html(404, render_result(False, "Not found", ""))

    def _serve_output(self, path: str) -> None:
        name = safe_name(urllib.parse.unquote(path[len("/files/"):]))
        target = (OUTPUT_DIR / name).resolve()
        if not str(target).startswith(str(OUTPUT_DIR.resolve())) or not target.is_file():
            self._send_html(404, render_result(False, "File not found", ""))
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        with target.open("rb") as fh:
            shutil.copyfileobj(fh, self.wfile)

    def do_POST(self) -> None:
        if urllib.parse.urlparse(self.path).path != "/edit":
            self._send_html(404, render_result(False, "Not found", ""))
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self._send_html(400, render_result(
                False, "Upload problem",
                f"<p>Upload size must be between 1 byte and {fmt_file_size(MAX_UPLOAD_BYTES)}.</p>"))
            return
        try:
            fields = parse_multipart(self.rfile.read(length),
                                     self.headers.get("Content-Type", ""))
        except Exception as exc:  # noqa: BLE001
            self._send_html(400, render_result(
                False, "Could not read the form", f"<pre>{html.escape(str(exc))}</pre>"))
            return
        self._run_edit(fields)

    def _run_edit(self, fields: dict) -> None:
        op_key = fields.get("op", {}).get("value", "")
        op = OPS_BY_KEY.get(op_key)
        if not op:
            self._send_html(400, render_result(False, "Unknown operation", ""))
            return

        # Resolve the input video: upload wins, else existing pick.
        INPUT_DIR.mkdir(exist_ok=True)
        video: Path | None = None
        up = fields.get("video")
        if up and up.get("filename") and up.get("data"):
            fname = safe_name(up["filename"])
            if not fname.lower().endswith(VIDEO_EXTENSIONS):
                self._send_html(400, render_result(
                    False, "Unsupported file type",
                    f"<p>Use one of: {', '.join(VIDEO_EXTENSIONS)}</p>"))
                return
            video = INPUT_DIR / fname
            video.write_bytes(up["data"])
        else:
            existing = fields.get("existing", {}).get("value", "")
            if existing:
                cand = (INPUT_DIR / safe_name(existing)).resolve()
                if str(cand).startswith(str(INPUT_DIR.resolve())) and cand.is_file():
                    video = cand
        if video is None:
            self._send_html(400, render_result(
                False, "No video selected",
                "<p>Upload a video or pick one from the input/ list.</p>"))
            return

        # Build the edit.py command exactly like easy_edit does.
        cmd = [sys.executable, str(EDIT_SCRIPT), str(video), op["flag"]]
        for f in op["fields"]:
            val = fields.get(f"f_{f['name']}", {}).get("value", "")
            if not val:
                self._send_html(400, render_result(
                    False, "Missing field", f"<p>{html.escape(f['label'])} is required.</p>"))
                return
            cmd.append(val)
        if op.get("needs_image"):
            wm = fields.get("wm_image")
            if not (wm and wm.get("filename") and wm.get("data")):
                self._send_html(400, render_result(
                    False, "Missing watermark image",
                    "<p>Pick a PNG/JPG image for the watermark.</p>"))
                return
            wm_name = safe_name(wm["filename"])
            if not wm_name.lower().endswith(IMAGE_EXTENSIONS):
                self._send_html(400, render_result(
                    False, "Watermark must be an image",
                    f"<p>Use one of: {', '.join(IMAGE_EXTENSIONS)}</p>"))
                return
            wm_path = INPUT_DIR / wm_name
            wm_path.write_bytes(wm["data"])
            cmd.append(str(wm_path))

        OUTPUT_DIR.mkdir(exist_ok=True)
        output = build_output_path(video.stem, op["key"], choose_output_suffix(video))
        cmd += ["--output", str(output)]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=EDIT_TIMEOUT_SECS)
        except subprocess.TimeoutExpired:
            self._send_html(500, render_result(
                False, "Edit timed out",
                f"<p>Gave up after {EDIT_TIMEOUT_SECS // 60} minutes.</p>"))
            return

        if proc.returncode != 0 or not output.exists():
            tail = (proc.stderr or proc.stdout or "").strip()[-1500:]
            self._send_html(500, render_result(
                False, "Edit failed",
                "<p>ffmpeg/edit.py reported a problem:</p>"
                f"<pre>{html.escape(tail) or 'no error output'}</pre>"))
            return

        size = fmt_file_size(output.stat().st_size)
        link = f"/files/{urllib.parse.quote(output.name)}"
        self._send_html(200, render_result(
            True, "Done!",
            f"<p><b>{html.escape(op['name'])}</b> finished. "
            f"Output: <code>{html.escape(output.name)}</code> ({size})</p>"
            f'<a class="dl" href="{link}">&#11015; Download result</a>'))

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        sys.stderr.write("[web_edit] %s\n" % (fmt % args))


def main() -> None:
    if not shutil.which("ffmpeg"):
        print("WARNING: ffmpeg not found on PATH -- edits will fail. "
              "Install it first (Windows: winget install ffmpeg).")
    port = int(os.environ.get("PORT", "5000"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"web_edit {VERSION} running.")
    print(f"  Open:  http://localhost:{port}")
    print("  Stop:  Ctrl+C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

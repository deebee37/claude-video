"""Shared helpers for easy_edit.py and future GUI."""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "output"
EDIT_SCRIPT = Path(__file__).resolve().parent / "edit.py"


def choose_output_suffix(video: Path) -> str:
    src = video.suffix.lower()
    if src == ".webm":
        return ".mkv"
    if src in (".mp4", ".mov", ".mkv"):
        return video.suffix
    return ".mp4"


def build_output_path(input_stem: str, op_key: str, suffix: str = ".mp4") -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_stem = re.sub(r"[^\w\-.]", "_", input_stem)
    candidate = OUTPUT_DIR / f"{safe_stem}_{op_key}_{ts}{suffix}"
    n = 1
    while candidate.exists():
        n += 1
        candidate = OUTPUT_DIR / f"{safe_stem}_{op_key}_{ts}_{n}{suffix}"
    return candidate


def fmt_file_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"


def parse_fps(value: str | None) -> int | None:
    if not value or "/" not in value:
        return None
    try:
        num_s, den_s = value.split("/", 1)
        num, den = int(num_s), int(den_s)
        if num <= 0 or den <= 0:
            return None
        fps = round(num / den)
        return fps if fps > 0 else None
    except ValueError:
        return None


def probe_video(path: Path) -> str | None:
    """Return a short info string like '00:02:35, 1920x1080, 30fps, has audio'."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration:stream=width,height,avg_frame_rate,r_frame_rate,codec_type",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
    except Exception:
        return None

    parts: list[str] = []

    dur = data.get("format", {}).get("duration")
    if dur:
        try:
            parts.append(fmt_duration(float(dur)))
        except ValueError:
            pass

    has_audio = False
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not any("x" in p for p in parts):
            w, h = s.get("width"), s.get("height")
            if w and h:
                parts.append(f"{w}x{h}")
            fps = parse_fps(s.get("avg_frame_rate")) or parse_fps(s.get("r_frame_rate"))
            if fps:
                parts.append(f"{fps}fps")
        if s.get("codec_type") == "audio":
            has_audio = True

    parts.append("has audio" if has_audio else "no audio")
    return ", ".join(parts) if len(parts) > 1 else None

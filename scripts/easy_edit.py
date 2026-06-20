#!/usr/bin/env python3
"""
easy_edit.py -- beginner-friendly menu runner for scripts/edit.py.

Lists videos from input/, offers a numbered menu of common edits,
builds the correct edit.py command, and saves output to output/.

Usage:
    python3 scripts/easy_edit.py              # interactive menu
    python3 scripts/easy_edit.py --list-ops   # print operations and exit
    python3 scripts/easy_edit.py --version    # print version and exit
"""
from __future__ import annotations

import json
import platform
import re
import shlex
import shutil
import subprocess

from utils import (build_output_path, choose_output_suffix, fmt_duration,
                   fmt_file_size, parse_fps, probe_video, EDIT_SCRIPT,
                   OUTPUT_DIR, REPO_ROOT)
import sys
import tempfile
from datetime import datetime
from pathlib import Path

VERSION = "1.0.0"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm")

INPUT_DIR = REPO_ROOT / "input"

OPERATIONS: list[dict] = [
    {
        "name": "Trim (keep a section)",
        "key": "trim",
        "flag": "--trim",
        "prompts": [
            {"label": "Start time (SS, MM:SS, or HH:MM:SS)", "validate": "time_no_end"},
            {"label": "End time (SS, MM:SS, HH:MM:SS, or 'end')", "validate": "time"},
        ],
    },
    {
        "name": "Cut (remove a section)",
        "key": "cut",
        "flag": "--cut",
        "prompts": [
            {"label": "Start of section to remove (SS, MM:SS, or HH:MM:SS)", "validate": "time_no_end"},
            {"label": "End of section to remove (SS, MM:SS, or HH:MM:SS)", "validate": "time_no_end"},
        ],
    },
    {
        "name": "Resize",
        "key": "resize",
        "flag": "--resize",
        "prompts": [
            {"label": "New size as WxH (e.g. 1920x1080, 1280x720)", "validate": "size"},
        ],
    },
    {
        "name": "Rotate",
        "key": "rotate",
        "flag": "--rotate",
        "prompts": [
            {"label": "Degrees to rotate (90, 180, or 270)", "validate": "rotation"},
        ],
    },
    {
        "name": "Speed",
        "key": "speed",
        "flag": "--speed",
        "prompts": [
            {"label": "Speed factor (e.g. 2.0 = double speed, 0.5 = half speed)", "validate": "positive_float"},
        ],
    },
    {
        "name": "FPS (change frame rate)",
        "key": "fps",
        "flag": "--fps",
        "prompts": [
            {"label": "Target frame rate (e.g. 24, 30, 60)", "validate": "positive_float"},
        ],
    },
    {
        "name": "Normalize audio",
        "key": "normalize-audio",
        "flag": "--normalize-audio",
        "prompts": [],
    },
    {
        "name": "Sharpen",
        "key": "sharpen",
        "flag": "--sharpen",
        "prompts": [],
    },
    {
        "name": "Denoise",
        "key": "denoise",
        "flag": "--denoise",
        "prompts": [],
    },
    {
        "name": "Watermark (text)",
        "key": "watermark-text",
        "flag": "--watermark-text",
        "prompts": [
            {"label": "Watermark text (e.g. your name or date)", "validate": "nonempty"},
        ],
    },
    {
        "name": "Watermark (image/logo)",
        "key": "watermark-image",
        "flag": "--watermark-image",
        "prompts": [
            {"label": "Path to image file (.png or .jpg)", "validate": "filepath"},
        ],
    },
]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _is_clock(value: str) -> bool:
    """True for a non-negative SS / MM:SS / HH:MM:SS timestamp."""
    parts = value.strip().split(":")
    if len(parts) > 3:
        return False
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return False
    return all(n >= 0 for n in nums)


def _validate_time(value: str) -> bool:
    if value.strip().lower() == "end":
        return True
    return _is_clock(value)


def _validate_time_no_end(value: str) -> bool:
    return _is_clock(value)


def _validate_size(value: str) -> bool:
    m = re.fullmatch(r"(\d+)x(\d+)", value.strip(), re.IGNORECASE)
    if not m:
        return False
    return int(m.group(1)) > 0 and int(m.group(2)) > 0


def _validate_rotation(value: str) -> bool:
    return value.strip() in ("90", "180", "270")


def _validate_positive_float(value: str) -> bool:
    try:
        return float(value.strip()) > 0
    except ValueError:
        return False


def _validate_nonempty(value: str) -> bool:
    return bool(value.strip())


def _validate_filepath(value: str) -> bool:
    v = value.strip()
    if not v:
        return False
    p = Path(v).expanduser()
    if p.exists():
        return True
    if (REPO_ROOT / v).exists():
        return True
    if (REPO_ROOT / "assets" / v).exists():
        return True
    return False


def _resolve_filepath(value: str) -> str:
    v = value.strip()
    p = Path(v).expanduser()
    if p.exists():
        return str(p.resolve())
    if (REPO_ROOT / v).exists():
        return str((REPO_ROOT / v).resolve())
    if (REPO_ROOT / "assets" / v).exists():
        return str((REPO_ROOT / "assets" / v).resolve())
    return v


_VALIDATORS: dict[str, tuple] = {
    "time":           (_validate_time, "Enter a time like 30, 1:30, or 0:01:30 (or 'end')"),
    "time_no_end":    (_validate_time_no_end, "Enter a time like 30, 1:30, or 0:01:30"),
    "size":           (_validate_size, "Enter size as WxH, e.g. 1920x1080"),
    "rotation":       (_validate_rotation, "Enter 90, 180, or 270"),
    "positive_float": (_validate_positive_float, "Enter a positive number, e.g. 2.0"),
    "nonempty":       (_validate_nonempty, "Cannot be empty"),
    "filepath":       (_validate_filepath, "File not found. Enter a valid path"),
}


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def check_prerequisites() -> None:
    missing = []
    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg")
    if not shutil.which("ffprobe"):
        missing.append("ffprobe")
    if not missing:
        return
    print(f"\nMissing required tool(s): {', '.join(missing)}")
    s = platform.system()
    if s == "Windows":
        print("  Install with:  winget install Gyan.FFmpeg")
    elif s == "Darwin":
        print("  Install with:  brew install ffmpeg")
    else:
        print("  Install with:  sudo apt install ffmpeg")
    sys.exit(1)


def ensure_dirs() -> None:
    for d in (INPUT_DIR, OUTPUT_DIR):
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            print(f"  Created folder: {d}")


def find_videos() -> list[Path]:
    if not INPUT_DIR.exists():
        return []
    videos = [
        p for p in sorted(INPUT_DIR.iterdir())
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    return videos


def pick_from_list(items: list[str], prompt: str) -> int:
    print()
    for i, item in enumerate(items, 1):
        print(f"  {i}. {item}")
    while True:
        print()
        raw = input(f"{prompt} (1-{len(items)}): ").strip()
        try:
            choice = int(raw)
            if 1 <= choice <= len(items):
                return choice - 1
        except ValueError:
            pass
        print(f"  Please enter a number between 1 and {len(items)}.")


def prompt_value(label: str, validate_type: str) -> str:
    fn, hint = _VALIDATORS[validate_type]
    while True:
        raw = input(f"  {label}: ").strip()
        if fn(raw):
            if validate_type == "filepath":
                return _resolve_filepath(raw)
            return raw
        print(f"    Invalid. {hint}")


def build_command(video: Path, op: dict, values: list[str],
                  output: Path) -> list[str]:
    cmd = [sys.executable, str(EDIT_SCRIPT), str(video), op["flag"]]
    cmd.extend(values)
    cmd.extend(["--output", str(output)])
    return cmd


_ERROR_HINTS = ("error", "invalid", "not supported", "only ", "could not",
                "no such", "permission", "unable", "cannot", "failed")

# edit.py status lines we never want to surface as the error summary.
_STATUS_PREFIXES = ("working dir:", "input:")


def run_edit(cmd: list[str]) -> tuple[bool, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        return True, r.stdout
    stderr = r.stderr or ""
    # edit.py reports the actual error as the last non-status "[edit]" line
    # (e.g. "[edit] normalize-audio requires an input with an audio stream."),
    # while raw ffmpeg/ffprobe output appears on lines without the tag.
    edit_msgs: list[str] = []
    detail_lines: list[str] = []
    for ln in stderr.splitlines():
        if not ln.strip():
            continue
        if "[edit]" in ln:
            msg = ln.split("[edit]", 1)[-1].strip()
            if not msg.lower().startswith(_STATUS_PREFIXES):
                edit_msgs.append(msg)
        else:
            detail_lines.append(ln.strip())

    parts: list[str] = []
    if edit_msgs:
        parts.append(edit_msgs[-1])
    # Add the underlying ffmpeg/ffprobe reason for extra context.
    reason = ""
    for ln in detail_lines:
        if any(k in ln.lower() for k in _ERROR_HINTS):
            reason = ln
    if not reason and detail_lines:
        reason = detail_lines[-1]
    if reason and reason not in " ".join(parts):
        parts.append(reason)
    if not parts:
        parts.append(f"exit code {r.returncode}")
    return False, " ".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if "--version" in sys.argv:
        print(f"easy_edit {VERSION}")
        return 0

    if "--list-ops" in sys.argv:
        print(f"\neasy_edit {VERSION} -- available operations:\n")
        for i, op in enumerate(OPERATIONS, 1):
            prompts = ", ".join(p["label"].split("(")[0].strip() for p in op["prompts"])
            extra = f" (asks for: {prompts})" if prompts else ""
            print(f"  {i:2d}. {op['name']}{extra}  [{op['flag']}]")
        print()
        return 0

    print(f"\n=== Easy Video Editor v{VERSION} ===\n")
    print("  Videos in:   input/")
    print("  Results in:  output/")
    print("  Help:        QUICKSTART.md\n")

    check_prerequisites()
    ensure_dirs()

    last_failed = False
    try:
        while True:
            videos = find_videos()
            if not videos:
                print(f"No video files found in:\n  {INPUT_DIR}\n")
                print("Drop a .mp4, .mov, .mkv, or .webm file into that folder and try again.")
                return 1

            idx = pick_from_list(
                [f"{v.name}  ({v.stat().st_size / 1_048_576:.1f} MB)" for v in videos],
                "Pick a video",
            )
            video = videos[idx]

            info = probe_video(video)
            if info:
                print(f"\n  {video.name} — {info}")

            op_idx = pick_from_list(
                [op["name"] for op in OPERATIONS],
                "Pick an operation",
            )
            op = OPERATIONS[op_idx]

            values: list[str] = []
            if op["prompts"]:
                print(f"\n  {op['name']}:")
            for p in op["prompts"]:
                values.append(prompt_value(p["label"], p["validate"]))

            suffix = choose_output_suffix(video)
            if video.suffix.lower() == ".webm" and suffix != ".webm":
                print(f"\nWebM input detected. Saving as {suffix} to preserve audio compatibility.")
            output = build_output_path(video.stem, op["key"], suffix)

            uses_intermediates = op["key"] in ("cut", "concat")
            if uses_intermediates:
                tmp_dir = Path(tempfile.mkdtemp(prefix="easy-edit-"))
                tmp_output = tmp_dir / output.name
                cmd = build_command(video, op, values, tmp_output)
            else:
                tmp_dir = None
                tmp_output = None
                cmd = build_command(video, op, values, output)

            print(f"\nCommand:\n  {shlex.join(cmd)}\n")
            confirm = input("Run this? (y/n): ").strip().lower()
            if confirm != "y":
                print("Cancelled.\n")
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                continue

            output_existed = output.exists()
            print("Running...")
            success, message = run_edit(cmd)

            # For temp-dir operations, promote the finished file into output/.
            if success and uses_intermediates:
                if tmp_output.exists() and tmp_output.stat().st_size > 0:
                    shutil.move(str(tmp_output), str(output))
                else:
                    success = False
                    message = "the edit finished but produced no output file"
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

            # A zero-byte or missing result is a failure, even on exit code 0.
            if success and (not output.exists() or output.stat().st_size == 0):
                success = False
                message = "the edit finished but produced no output file"

            if success:
                last_failed = False
                try:
                    size_text = fmt_file_size(output.stat().st_size)
                except OSError:
                    size_text = None
                print(f"\nDone! Output saved to:\n  {output}")
                if size_text:
                    print(f"  Size: {size_text}")
                print()
            else:
                if output.exists() and not output_existed:
                    output.unlink()
                last_failed = True
                print(f"\nSomething went wrong:\n  {message}\n")

            again = input("Edit another? (y/n): ").strip().lower()
            if again != "y":
                break
    except EOFError:
        # End of input (Ctrl+D or a piped run); stop cleanly but keep the
        # failure status from the last edit so callers see a non-zero exit.
        print()

    print("Bye!")
    return 1 if last_failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nCancelled. Bye!")
        sys.exit(0)

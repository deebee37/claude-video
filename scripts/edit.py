#!/usr/bin/env python3
"""
edit.py — AI-assisted video editor for claude-video.

Wraps ffmpeg to perform edits on real video files. Accepts any format
ffmpeg supports (mp4, mov, mkv, avi, webm, HEVC, VP9, AV1, etc.).
No content filtering — works on any video regardless of rating.

Prints a markdown report to stdout with the output path and 3 preview
frame paths. Status messages go to stderr.

Usage examples:
  python3 edit.py input.mp4 --trim 0 30 --output out.mp4
  python3 edit.py input.mp4 --cut 0:10 0:20
  python3 edit.py a.mp4 --concat b.mp4 c.mp4
  python3 edit.py input.mp4 --speed 2.0
  python3 edit.py input.mp4 --text "My Title" --text-position top
  python3 edit.py input.mp4 --mute
  python3 edit.py input.mp4 --volume 1.5
  python3 edit.py input.mp4 --replace-audio music.mp3
  python3 edit.py input.mp4 --fade-in 1 --fade-out 2
  python3 edit.py input.mp4 --resize 1920x1080
  python3 edit.py input.mp4 --rotate 90
  python3 edit.py input.mp4 --crop 1280:720:0:0
  python3 edit.py base.mp4 --overlay logo.mp4 --overlay-x 10 --overlay-y 10
  python3 edit.py left.mp4 --side-by-side right.mp4
  python3 edit.py top.mp4 --stack bottom.mp4
  python3 edit.py clip1.mp4 --crossfade clip2.mp4 --crossfade-duration 1.5
  python3 edit.py main.mp4 --pip reaction.mp4 --pip-position top-right
  python3 edit.py input.mkv --format mp4 --trim 0 60
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Reuse utilities from sibling modules
_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))
from frames import get_metadata, parse_time, format_time, extract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status(msg: str) -> None:
    print(f"[edit] {msg}", file=sys.stderr)


def _require(cmd: str) -> None:
    if not shutil.which(cmd):
        raise SystemExit(f"[edit] ERROR: '{cmd}' not found on PATH. Install it first.")


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=check)


def _auto_output(work_dir: Path, suffix: str = ".mp4") -> Path:
    return work_dir / f"output{suffix}"


def _parse_size(size_str: str) -> tuple[int, int]:
    """Parse '1920x1080' or '1920:1080' → (1920, 1080)."""
    sep = "x" if "x" in size_str else ":"
    parts = size_str.split(sep)
    if len(parts) != 2:
        raise SystemExit(f"[edit] Invalid size '{size_str}'. Use WxH e.g. 1920x1080.")
    return int(parts[0]), int(parts[1])


# ---------------------------------------------------------------------------
# Individual edit operations
# ---------------------------------------------------------------------------

def op_trim(input_path: Path, output_path: Path, start: float, end: float | None) -> None:
    """Cut video to [start, end]. Tries stream copy first; falls back to re-encode."""
    _status(f"trimming {format_time(start)} → {format_time(end) if end else 'end'}")
    cmd_base = ["ffmpeg", "-y", "-ss", str(start)]
    if end is not None:
        cmd_base += ["-to", str(end)]
    cmd_base += ["-i", str(input_path)]

    # Fast path: stream copy (lossless, instant)
    r = _run(cmd_base + ["-c", "copy", str(output_path)], check=False)
    if r.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        return

    # Fallback: re-encode (handles HEVC, VFR, and other copy-incompatible formats)
    _status("stream copy failed, re-encoding (HEVC/VFR source?)")
    r = _run(cmd_base + [
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        str(output_path),
    ], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] trim failed:\n{r.stderr}")


def op_cut(input_path: Path, output_path: Path, cut_start: float, cut_end: float, duration: float) -> None:
    """Remove [cut_start, cut_end] from the video, keeping everything outside that range."""
    _status(f"cutting out {format_time(cut_start)} → {format_time(cut_end)}")
    work = output_path.parent
    seg_a = work / "seg_a.mp4"
    seg_b = work / "seg_b.mp4"

    # Segment A: 0 → cut_start
    if cut_start > 0:
        _run(["ffmpeg", "-y", "-i", str(input_path), "-to", str(cut_start),
              "-c", "copy", str(seg_a)], check=False)
    # Segment B: cut_end → end
    if cut_end < duration:
        _run(["ffmpeg", "-y", "-ss", str(cut_end), "-i", str(input_path),
              "-c", "copy", str(seg_b)], check=False)

    parts = [p for p in [seg_a, seg_b] if p.exists() and p.stat().st_size > 0]
    if not parts:
        raise SystemExit("[edit] cut produced no output segments.")

    if len(parts) == 1:
        shutil.copy(parts[0], output_path)
    else:
        _concat_files(parts, output_path)


def _concat_files(parts: list[Path], output_path: Path) -> None:
    """Concatenate a list of video files using the concat filter (re-encodes)."""
    work = output_path.parent
    list_file = work / "concat_list.txt"
    list_file.write_text("\n".join(f"file '{p}'" for p in parts))
    r = _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
              "-i", str(list_file),
              "-c:v", "libx264", "-preset", "fast", "-crf", "18",
              "-c:a", "aac", "-b:a", "192k",
              str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] concat failed:\n{r.stderr}")


def op_concat(inputs: list[Path], output_path: Path) -> None:
    """Join multiple video files together."""
    _status(f"concatenating {len(inputs)} clips")
    _concat_files(inputs, output_path)


def op_speed(input_path: Path, output_path: Path, factor: float) -> None:
    """Change playback speed. Factor > 1 = faster, < 1 = slower."""
    _status(f"changing speed to {factor}x")
    pts = 1.0 / factor

    # atempo only accepts 0.5–2.0; chain filters for extreme values
    def _atempo_chain(f: float) -> str:
        filters = []
        while f > 2.0:
            filters.append("atempo=2.0")
            f /= 2.0
        while f < 0.5:
            filters.append("atempo=0.5")
            f *= 2.0
        filters.append(f"atempo={f:.4f}")
        return ",".join(filters)

    vf = f"setpts={pts:.4f}*PTS"
    af = _atempo_chain(factor)

    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", vf, "-af", af,
              "-c:v", "libx264", "-preset", "fast", "-crf", "18",
              "-c:a", "aac", "-b:a", "192k",
              str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] speed change failed:\n{r.stderr}")


def op_text(input_path: Path, output_path: Path,
            text: str, position: str, size: int, color: str,
            start: float | None, end: float | None) -> None:
    """Burn a text overlay onto the video."""
    _status(f"adding text overlay: '{text}'")

    # Position presets
    pos_map = {
        "top":    ("(w-text_w)/2", "h*0.08"),
        "center": ("(w-text_w)/2", "(h-text_h)/2"),
        "bottom": ("(w-text_w)/2", "h*0.88"),
    }
    x, y = pos_map.get(position, pos_map["bottom"])

    time_filter = ""
    if start is not None or end is not None:
        t_start = start or 0
        t_end = end if end is not None else 999999
        time_filter = f":enable='between(t,{t_start},{t_end})'"

    # Escape special chars for ffmpeg drawtext
    safe_text = text.replace("'", "\\'").replace(":", "\\:")

    vf = (f"drawtext=text='{safe_text}'"
          f":fontsize={size}:fontcolor={color}"
          f":x={x}:y={y}"
          f":shadowcolor=black:shadowx=2:shadowy=2"
          f"{time_filter}")

    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", vf,
              "-c:v", "libx264", "-preset", "fast", "-crf", "18",
              "-c:a", "copy",
              str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] text overlay failed:\n{r.stderr}")


def op_mute(input_path: Path, output_path: Path) -> None:
    """Remove audio track."""
    _status("removing audio")
    r = _run(["ffmpeg", "-y", "-i", str(input_path), "-an", "-c:v", "copy", str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] mute failed:\n{r.stderr}")


def op_volume(input_path: Path, output_path: Path, level: float) -> None:
    """Adjust audio volume."""
    _status(f"adjusting volume to {level}x")
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-af", f"volume={level}",
              "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
              str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] volume adjustment failed:\n{r.stderr}")


def op_replace_audio(input_path: Path, audio_path: Path, output_path: Path) -> None:
    """Replace video's audio with a different audio file."""
    _status(f"replacing audio with {audio_path.name}")
    r = _run(["ffmpeg", "-y",
              "-i", str(input_path),
              "-i", str(audio_path),
              "-map", "0:v", "-map", "1:a",
              "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
              "-shortest",
              str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] audio replace failed:\n{r.stderr}")


def op_fade(input_path: Path, output_path: Path,
            fade_in: float | None, fade_out: float | None, duration: float) -> None:
    """Add fade-in and/or fade-out effects."""
    _status(f"adding fades (in={fade_in}s, out={fade_out}s)")
    vf_parts = []
    af_parts = []
    if fade_in:
        vf_parts.append(f"fade=in:st=0:d={fade_in}")
        af_parts.append(f"afade=in:st=0:d={fade_in}")
    if fade_out:
        fade_start = max(0, duration - fade_out)
        vf_parts.append(f"fade=out:st={fade_start:.3f}:d={fade_out}")
        af_parts.append(f"afade=out:st={fade_start:.3f}:d={fade_out}")

    cmd = ["ffmpeg", "-y", "-i", str(input_path)]
    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]
    if af_parts:
        cmd += ["-af", ",".join(af_parts)]
    cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", str(output_path)]
    r = _run(cmd, check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] fade failed:\n{r.stderr}")


def op_resize(input_path: Path, output_path: Path, width: int, height: int) -> None:
    """Resize video to target resolution."""
    _status(f"resizing to {width}x{height}")
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", f"scale={width}:{height}",
              "-c:v", "libx264", "-preset", "fast", "-crf", "18",
              "-c:a", "copy", str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] resize failed:\n{r.stderr}")


def op_rotate(input_path: Path, output_path: Path, degrees: int) -> None:
    """Rotate video by 90, 180, or 270 degrees."""
    _status(f"rotating {degrees}°")
    rotate_map = {
        90:  "transpose=1",
        180: "transpose=1,transpose=1",
        270: "transpose=2",
    }
    vf = rotate_map.get(degrees)
    if not vf:
        raise SystemExit(f"[edit] Only 90, 180, 270 degree rotations supported. Got {degrees}.")
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", vf,
              "-c:v", "libx264", "-preset", "fast", "-crf", "18",
              "-c:a", "copy", str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] rotate failed:\n{r.stderr}")


def op_crop(input_path: Path, output_path: Path, crop: str) -> None:
    """Crop video. Format: W:H:X:Y"""
    _status(f"cropping to {crop}")
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", f"crop={crop}",
              "-c:v", "libx264", "-preset", "fast", "-crf", "18",
              "-c:a", "copy", str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] crop failed:\n{r.stderr}")


# ---------------------------------------------------------------------------
# Blending / compositing operations
# ---------------------------------------------------------------------------

def op_overlay(input_path: Path, overlay_path: Path, output_path: Path,
               x: int, y: int, scale: int | None) -> None:
    """Overlay a second video on top of the base video."""
    _status(f"overlaying {overlay_path.name} at ({x},{y})")
    scale_filter = f"[1]scale={scale}:-2[ov];" if scale else "[1]copy[ov];"
    vf = f"{scale_filter}[0][ov]overlay={x}:{y}"
    r = _run(["ffmpeg", "-y",
              "-i", str(input_path),
              "-i", str(overlay_path),
              "-filter_complex", vf,
              "-c:v", "libx264", "-preset", "fast", "-crf", "18",
              "-c:a", "copy",
              str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] overlay failed:\n{r.stderr}")


def op_side_by_side(left_path: Path, right_path: Path, output_path: Path) -> None:
    """Place two videos side by side (horizontal stack)."""
    _status(f"side-by-side: {left_path.name} | {right_path.name}")
    r = _run(["ffmpeg", "-y",
              "-i", str(left_path),
              "-i", str(right_path),
              "-filter_complex", "[0][1]hstack=inputs=2",
              "-c:v", "libx264", "-preset", "fast", "-crf", "18",
              "-c:a", "copy",
              str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] side-by-side failed:\n{r.stderr}")


def op_stack(top_path: Path, bottom_path: Path, output_path: Path) -> None:
    """Stack two videos vertically."""
    _status(f"stacking: {top_path.name} / {bottom_path.name}")
    r = _run(["ffmpeg", "-y",
              "-i", str(top_path),
              "-i", str(bottom_path),
              "-filter_complex", "[0][1]vstack=inputs=2",
              "-c:v", "libx264", "-preset", "fast", "-crf", "18",
              "-c:a", "copy",
              str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] stack failed:\n{r.stderr}")


def op_crossfade(clip1: Path, clip2: Path, output_path: Path,
                 fade_duration: float, clip1_duration: float) -> None:
    """Crossfade transition between two clips."""
    _status(f"crossfade ({fade_duration}s) between {clip1.name} and {clip2.name}")
    offset = max(0.0, clip1_duration - fade_duration)
    fc = (f"[0][1]xfade=transition=fade:duration={fade_duration:.3f}:offset={offset:.3f}[vout];"
          f"[0:a][1:a]acrossfade=d={fade_duration:.3f}[aout]")
    r = _run(["ffmpeg", "-y",
              "-i", str(clip1),
              "-i", str(clip2),
              "-filter_complex", fc,
              "-map", "[vout]", "-map", "[aout]",
              "-c:v", "libx264", "-preset", "fast", "-crf", "18",
              "-c:a", "aac", "-b:a", "192k",
              str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] crossfade failed:\n{r.stderr}")


def op_pip(main_path: Path, pip_path: Path, output_path: Path,
           position: str, pip_width: int) -> None:
    """Picture-in-picture: embed a smaller video in a corner."""
    _status(f"picture-in-picture ({position}): {pip_path.name}")
    # pip is scaled to pip_width, positioned in a corner with 20px margin
    pos_map = {
        "top-right":    f"main_w-overlay_w-20:20",
        "top-left":     "20:20",
        "bottom-right": f"main_w-overlay_w-20:main_h-overlay_h-20",
        "bottom-left":  f"20:main_h-overlay_h-20",
    }
    xy = pos_map.get(position, pos_map["top-right"])
    fc = f"[1]scale={pip_width}:-2[pip];[0][pip]overlay={xy}"
    r = _run(["ffmpeg", "-y",
              "-i", str(main_path),
              "-i", str(pip_path),
              "-filter_complex", fc,
              "-c:v", "libx264", "-preset", "fast", "-crf", "18",
              "-c:a", "copy",
              str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] pip failed:\n{r.stderr}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        prog="edit",
        description="AI-assisted video editor — ffmpeg wrapper for claude-video.",
    )
    ap.add_argument("input", help="Path to the input video file")
    ap.add_argument("--output", help="Output file path (default: auto in temp dir)")
    ap.add_argument("--out-dir", help="Directory for output (default: auto temp dir)")

    # Operations
    ap.add_argument("--trim", nargs=2, metavar=("START", "END"),
                    help="Trim to time range. Times: SS, MM:SS, HH:MM:SS, or 'end'")
    ap.add_argument("--cut", nargs=2, metavar=("START", "END"),
                    help="Remove section between START and END")
    ap.add_argument("--concat", nargs="+", metavar="FILE",
                    help="Concatenate additional video files after input")
    ap.add_argument("--speed", type=float, metavar="FACTOR",
                    help="Change speed (2.0=double speed, 0.5=half speed)")
    ap.add_argument("--text", metavar="TEXT",
                    help="Add a text overlay")
    ap.add_argument("--text-position", choices=["top", "center", "bottom"], default="bottom",
                    help="Position of text overlay (default: bottom)")
    ap.add_argument("--text-size", type=int, default=48,
                    help="Font size for text overlay (default: 48)")
    ap.add_argument("--text-color", default="white",
                    help="Font color for text overlay (default: white)")
    ap.add_argument("--text-start", help="When to start showing text (time format)")
    ap.add_argument("--text-end", help="When to stop showing text (time format)")
    ap.add_argument("--mute", action="store_true",
                    help="Remove audio track")
    ap.add_argument("--volume", type=float, metavar="LEVEL",
                    help="Adjust volume (1.0=normal, 2.0=double, 0.5=half)")
    ap.add_argument("--replace-audio", metavar="AUDIO_FILE",
                    help="Replace audio track with a different audio file")
    ap.add_argument("--fade-in", type=float, metavar="SECS",
                    help="Fade in duration in seconds")
    ap.add_argument("--fade-out", type=float, metavar="SECS",
                    help="Fade out duration in seconds")
    ap.add_argument("--resize", metavar="WxH",
                    help="Resize to resolution e.g. 1920x1080")
    ap.add_argument("--rotate", type=int, choices=[90, 180, 270],
                    help="Rotate 90, 180, or 270 degrees")
    ap.add_argument("--crop", metavar="W:H:X:Y",
                    help="Crop: width:height:x_offset:y_offset")

    # Blending / compositing
    ap.add_argument("--overlay", metavar="FILE",
                    help="Overlay a second video on top of the input")
    ap.add_argument("--overlay-x", type=int, default=0,
                    help="X position of overlay (default: 0)")
    ap.add_argument("--overlay-y", type=int, default=0,
                    help="Y position of overlay (default: 0)")
    ap.add_argument("--overlay-scale", type=int, default=None,
                    help="Scale overlay to this width in pixels before placing")
    ap.add_argument("--side-by-side", metavar="FILE",
                    help="Place input and FILE side by side (horizontal)")
    ap.add_argument("--stack", metavar="FILE",
                    help="Stack input on top and FILE on bottom (vertical)")
    ap.add_argument("--crossfade", metavar="FILE",
                    help="Crossfade transition from input into FILE")
    ap.add_argument("--crossfade-duration", type=float, default=1.0,
                    help="Crossfade duration in seconds (default: 1.0)")
    ap.add_argument("--pip", metavar="FILE",
                    help="Picture-in-picture: embed FILE as a smaller overlay")
    ap.add_argument("--pip-position",
                    choices=["top-right", "top-left", "bottom-right", "bottom-left"],
                    default="top-right",
                    help="Corner for the PiP window (default: top-right)")
    ap.add_argument("--pip-width", type=int, default=320,
                    help="Width of PiP window in pixels (default: 320)")

    # Output format override
    ap.add_argument("--format", choices=["mp4", "mov", "mkv", "webm"],
                    default=None,
                    help="Output container format (default: match input extension)")

    args = ap.parse_args()

    _require("ffmpeg")
    _require("ffprobe")

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"[edit] Input file not found: {input_path}")

    # Working directory
    if args.out_dir:
        work_dir = Path(args.out_dir).expanduser().resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="edit-"))

    _status(f"working dir: {work_dir}")

    # Get input metadata
    meta = get_metadata(str(input_path))
    duration = meta["duration_seconds"]
    _status(f"input: {input_path.name} ({format_time(duration)}, "
            f"{meta['width']}x{meta['height']}, {meta['codec']})")

    # Determine output path
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        if args.format:
            suffix = f".{args.format}"
        else:
            suffix = input_path.suffix or ".mp4"
        output_path = work_dir / f"output{suffix}"

    # -----------------------------------------------------------------------
    # Execute the requested operation
    # -----------------------------------------------------------------------
    op_count = sum([
        args.trim is not None,
        args.cut is not None,
        args.concat is not None,
        args.speed is not None,
        args.text is not None,
        args.mute,
        args.volume is not None,
        args.replace_audio is not None,
        args.fade_in is not None or args.fade_out is not None,
        args.resize is not None,
        args.rotate is not None,
        args.crop is not None,
        args.overlay is not None,
        args.side_by_side is not None,
        args.stack is not None,
        args.crossfade is not None,
        args.pip is not None,
    ])

    if op_count == 0:
        raise SystemExit("[edit] No operation specified. Use --trim, --cut, --concat, --speed, "
                         "--text, --mute, --volume, --replace-audio, --fade-in/out, "
                         "--resize, --rotate, --crop, --overlay, --side-by-side, "
                         "--stack, --crossfade, or --pip.")
    if op_count > 1:
        raise SystemExit("[edit] Specify one operation per call. Chain calls for multi-step edits "
                         "(output of one → input of next).")

    if args.trim:
        raw_start, raw_end = args.trim
        t_start = parse_time(raw_start) or 0.0
        t_end = None if raw_end.lower() == "end" else parse_time(raw_end)
        op_trim(input_path, output_path, t_start, t_end)

    elif args.cut:
        t_start = parse_time(args.cut[0]) or 0.0
        t_end = parse_time(args.cut[1]) or duration
        op_cut(input_path, output_path, t_start, t_end, duration)

    elif args.concat:
        extra = [Path(f).expanduser().resolve() for f in args.concat]
        for p in extra:
            if not p.exists():
                raise SystemExit(f"[edit] File not found: {p}")
        op_concat([input_path] + extra, output_path)

    elif args.speed is not None:
        op_speed(input_path, output_path, args.speed)

    elif args.text is not None:
        t_start = parse_time(args.text_start) if args.text_start else None
        t_end = parse_time(args.text_end) if args.text_end else None
        op_text(input_path, output_path, args.text,
                args.text_position, args.text_size, args.text_color,
                t_start, t_end)

    elif args.mute:
        op_mute(input_path, output_path)

    elif args.volume is not None:
        op_volume(input_path, output_path, args.volume)

    elif args.replace_audio:
        audio_path = Path(args.replace_audio).expanduser().resolve()
        if not audio_path.exists():
            raise SystemExit(f"[edit] Audio file not found: {audio_path}")
        op_replace_audio(input_path, audio_path, output_path)

    elif args.fade_in is not None or args.fade_out is not None:
        op_fade(input_path, output_path, args.fade_in, args.fade_out, duration)

    elif args.resize:
        w, h = _parse_size(args.resize)
        op_resize(input_path, output_path, w, h)

    elif args.rotate:
        op_rotate(input_path, output_path, args.rotate)

    elif args.crop:
        op_crop(input_path, output_path, args.crop)

    elif args.overlay:
        overlay_path = Path(args.overlay).expanduser().resolve()
        if not overlay_path.exists():
            raise SystemExit(f"[edit] Overlay file not found: {overlay_path}")
        op_overlay(input_path, overlay_path, output_path,
                   args.overlay_x, args.overlay_y, args.overlay_scale)

    elif args.side_by_side:
        right_path = Path(args.side_by_side).expanduser().resolve()
        if not right_path.exists():
            raise SystemExit(f"[edit] File not found: {right_path}")
        op_side_by_side(input_path, right_path, output_path)

    elif args.stack:
        bottom_path = Path(args.stack).expanduser().resolve()
        if not bottom_path.exists():
            raise SystemExit(f"[edit] File not found: {bottom_path}")
        op_stack(input_path, bottom_path, output_path)

    elif args.crossfade:
        clip2_path = Path(args.crossfade).expanduser().resolve()
        if not clip2_path.exists():
            raise SystemExit(f"[edit] File not found: {clip2_path}")
        op_crossfade(input_path, clip2_path, output_path,
                     args.crossfade_duration, duration)

    elif args.pip:
        pip_path = Path(args.pip).expanduser().resolve()
        if not pip_path.exists():
            raise SystemExit(f"[edit] PiP file not found: {pip_path}")
        op_pip(input_path, pip_path, output_path, args.pip_position, args.pip_width)

    # -----------------------------------------------------------------------
    # Verify output and gather metadata
    # -----------------------------------------------------------------------
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise SystemExit(f"[edit] Output file missing or empty: {output_path}")

    out_meta = get_metadata(str(output_path))
    out_duration = out_meta["duration_seconds"]

    # Extract 3 preview frames (start, middle, end)
    frames_dir = work_dir / "preview_frames"
    frames_dir.mkdir(exist_ok=True)
    preview_times = [
        out_duration * 0.1,
        out_duration * 0.5,
        out_duration * 0.9,
    ]
    preview_frames = []
    for i, t in enumerate(preview_times):
        frame_path = frames_dir / f"preview_{i:02d}.jpg"
        r = _run(["ffmpeg", "-y", "-ss", str(t), "-i", str(output_path),
                  "-frames:v", "1", "-q:v", "4",
                  "-vf", "scale=512:-2",
                  str(frame_path)], check=False)
        if r.returncode == 0 and frame_path.exists():
            preview_frames.append((format_time(t), str(frame_path)))

    # -----------------------------------------------------------------------
    # Markdown report to stdout
    # -----------------------------------------------------------------------
    print(f"# Edit complete")
    print()
    print(f"**Input:** `{input_path.name}` ({format_time(duration)})")
    print(f"**Output:** `{output_path}` ({format_time(out_duration)}, "
          f"{out_meta['width']}x{out_meta['height']})")
    print(f"**Size:** {output_path.stat().st_size // 1024:,} KB")
    print()
    if preview_frames:
        print("## Preview frames")
        print()
        for ts, fpath in preview_frames:
            print(f"- `t={ts}` → `{fpath}`")
    print()
    print(f"*Working directory: {work_dir}*")

    return 0


if __name__ == "__main__":
    sys.exit(main())

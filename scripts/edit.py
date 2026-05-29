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
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Reuse utilities from sibling modules
_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))
from frames import get_metadata, parse_time, format_time
from looks import get_look_filter


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


def _parse_ratio(s: str) -> float:
    """Parse '2.35:1' or '2.35' → 2.35. Raise SystemExit on bad input."""
    try:
        if ":" in s:
            num, den = s.split(":")
            return float(num) / float(den)
        return float(s)
    except (ValueError, ZeroDivisionError):
        raise SystemExit(f"[edit] Invalid ratio '{s}'. Use e.g. 2.35:1 or 2.35.")


def _check_encoder(name: str) -> None:
    """Verify ffmpeg has the given encoder. Raise SystemExit if missing."""
    r = _run(["ffmpeg", "-hide_banner", "-encoders"], check=False)
    if r.returncode != 0 or name not in r.stdout:
        raise SystemExit(
            f"[edit] Encoder '{name}' not found in this ffmpeg build. "
            f"Use --codec h264 instead."
        )


def _check_vidstab() -> None:
    """Raise SystemExit if ffmpeg build lacks vidstab filters."""
    r = _run(["ffmpeg", "-hide_banner", "-filters"], check=False)
    missing = [f for f in ("vidstabdetect", "vidstabtransform") if f not in r.stdout]
    if missing:
        raise SystemExit(
            f"[edit] vidstab filter(s) not available in this ffmpeg build: {', '.join(missing)}\n"
            "Check: ffmpeg -filters | grep vidstab\n"
            "To install: brew install ffmpeg  (macOS) or  apt install ffmpeg  (Ubuntu/Debian)"
        )


class EncodeConfig:
    """Output encoding parameters resolved from CLI flags."""

    _PRESETS = {
        "preview":  ("libx264", 28, "ultrafast"),
        "standard": ("libx264", 18, "fast"),
        "high":     ("libx264", 15, "medium"),
        "master":   ("libx265", 16, "slow"),
    }

    def __init__(self, args):
        codec_map = {"h264": "libx264", "h265": "libx265"}
        base_codec, base_crf, base_preset = self._PRESETS[args.quality or "standard"]
        self.vcodec   = codec_map.get(args.codec or "", base_codec)
        self.crf      = args.crf if args.crf is not None else base_crf
        if not (0 <= self.crf <= 51):
            raise SystemExit(f"[edit] --crf must be 0–51. Got: {self.crf}")
        self.vpreset  = args.preset or base_preset
        self.abitrate = args.audio_bitrate or "192k"
        if self.vcodec == "libx265":
            _check_encoder("libx265")
            _status("Warning: h265/slow encode may take several minutes for long clips.")

    def video_flags(self) -> list[str]:
        return ["-c:v", self.vcodec, "-preset", self.vpreset,
                "-crf", str(self.crf), "-pix_fmt", "yuv420p"]

    def audio_flags(self) -> list[str]:
        return ["-c:a", "aac", "-b:a", self.abitrate]

    @staticmethod
    def audio_copy() -> list[str]:
        return ["-c:a", "copy"]


def _strip_flags(enabled: bool) -> list[str]:
    """Return -map_metadata -1 -map_chapters -1 when --strip-metadata is set."""
    return ["-map_metadata", "-1", "-map_chapters", "-1"] if enabled else []


# ---------------------------------------------------------------------------
# Individual edit operations
# ---------------------------------------------------------------------------

def op_trim(input_path: Path, output_path: Path, start: float, end: float | None,
            cfg: EncodeConfig, strip_meta: bool) -> None:
    """Cut video to [start, end]. Tries stream copy first; falls back to re-encode."""
    _status(f"trimming {format_time(start)} → {format_time(end) if end else 'end'}")
    cmd_base = ["ffmpeg", "-y", "-ss", str(start)]
    if end is not None:
        cmd_base += ["-to", str(end)]
    cmd_base += ["-i", str(input_path)]
    strip = _strip_flags(strip_meta)

    # Fast path: stream copy (lossless, instant)
    r = _run(cmd_base + strip + ["-c", "copy", str(output_path)], check=False)
    if r.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        return

    # Fallback: re-encode (handles HEVC, VFR, and other copy-incompatible formats)
    _status("stream copy failed, re-encoding (HEVC/VFR source?)")
    r = _run(cmd_base + strip + cfg.video_flags() + cfg.audio_flags()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] trim failed:\n{r.stderr}")


def op_trim_precise(input_path: Path, output_path: Path, start: float, end: float | None,
                    cfg: EncodeConfig, strip_meta: bool) -> None:
    """Frame-accurate trim via re-encode. No stream copy — start/end are exact."""
    _status(f"precise trim {format_time(start)} → {format_time(end) if end else 'end'}")
    cmd = ["ffmpeg", "-y", "-ss", str(start)]
    if end is not None:
        cmd += ["-to", str(end)]
    cmd += ["-i", str(input_path)]
    cmd += _strip_flags(strip_meta)
    cmd += cfg.video_flags() + cfg.audio_flags()
    cmd += [str(output_path)]
    r = _run(cmd, check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] trim-precise failed:\n{r.stderr}")


def op_cut(input_path: Path, output_path: Path, cut_start: float, cut_end: float,
           duration: float, cfg: EncodeConfig, strip_meta: bool) -> None:
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
        _concat_files(parts, output_path, cfg, strip_meta)


def _concat_files(parts: list[Path], output_path: Path,
                  cfg: EncodeConfig, strip_meta: bool) -> None:
    """Concatenate a list of video files using the concat filter (re-encodes)."""
    work = output_path.parent
    list_file = work / "concat_list.txt"
    list_file.write_text("\n".join(f"file '{p}'" for p in parts))
    r = _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
              "-i", str(list_file)]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_flags()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] concat failed:\n{r.stderr}")


def op_concat(inputs: list[Path], output_path: Path,
              cfg: EncodeConfig, strip_meta: bool) -> None:
    """Join multiple video files together."""
    _status(f"concatenating {len(inputs)} clips")
    _concat_files(inputs, output_path, cfg, strip_meta)


def op_speed(input_path: Path, output_path: Path, factor: float,
             cfg: EncodeConfig, strip_meta: bool) -> None:
    """Change playback speed. Factor > 1 = faster, < 1 = slower."""
    if factor <= 0:
        raise SystemExit("[edit] --speed must be a positive number. "
                         "Reverse playback is not supported by --speed.")
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
              "-vf", vf, "-af", af]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_flags()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] speed change failed:\n{r.stderr}")


def op_text(input_path: Path, output_path: Path,
            text: str, position: str, size: int, color: str,
            start: float | None, end: float | None,
            cfg: EncodeConfig, strip_meta: bool) -> None:
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
              "-vf", vf]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] text overlay failed:\n{r.stderr}")


def op_mute(input_path: Path, output_path: Path, strip_meta: bool) -> None:
    """Remove audio track."""
    _status("removing audio")
    r = _run(["ffmpeg", "-y", "-i", str(input_path), "-an", "-c:v", "copy"]
             + _strip_flags(strip_meta)
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] mute failed:\n{r.stderr}")


def op_volume(input_path: Path, output_path: Path, level: float,
              cfg: EncodeConfig, strip_meta: bool) -> None:
    """Adjust audio volume."""
    _status(f"adjusting volume to {level}x")
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-af", f"volume={level}",
              "-c:v", "copy"]
             + cfg.audio_flags()
             + _strip_flags(strip_meta)
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] volume adjustment failed:\n{r.stderr}")


def op_replace_audio(input_path: Path, audio_path: Path, output_path: Path,
                     cfg: EncodeConfig, strip_meta: bool) -> None:
    """Replace video's audio with a different audio file."""
    _status(f"replacing audio with {audio_path.name}")
    r = _run(["ffmpeg", "-y",
              "-i", str(input_path),
              "-i", str(audio_path),
              "-map", "0:v", "-map", "1:a",
              "-c:v", "copy"]
             + cfg.audio_flags()
             + _strip_flags(strip_meta)
             + ["-shortest", str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] audio replace failed:\n{r.stderr}")


def op_fade(input_path: Path, output_path: Path,
            fade_in: float | None, fade_out: float | None, duration: float,
            cfg: EncodeConfig, strip_meta: bool) -> None:
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
    cmd += _strip_flags(strip_meta)
    cmd += cfg.video_flags() + cfg.audio_flags() + [str(output_path)]
    r = _run(cmd, check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] fade failed:\n{r.stderr}")


def op_resize(input_path: Path, output_path: Path, width: int, height: int,
              cfg: EncodeConfig, strip_meta: bool) -> None:
    """Resize video to target resolution."""
    _status(f"resizing to {width}x{height}")
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", f"scale={width}:{height}"]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] resize failed:\n{r.stderr}")


def op_rotate(input_path: Path, output_path: Path, degrees: int,
              cfg: EncodeConfig, strip_meta: bool) -> None:
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
              "-vf", vf]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] rotate failed:\n{r.stderr}")


def op_crop(input_path: Path, output_path: Path, crop: str,
            cfg: EncodeConfig, strip_meta: bool) -> None:
    """Crop video. Format: W:H:X:Y"""
    _status(f"cropping to {crop}")
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", f"crop={crop}"]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] crop failed:\n{r.stderr}")


# ---------------------------------------------------------------------------
# Blending / compositing operations
# ---------------------------------------------------------------------------

def op_overlay(input_path: Path, overlay_path: Path, output_path: Path,
               x: int, y: int, scale: int | None,
               cfg: EncodeConfig, strip_meta: bool) -> None:
    """Overlay a second video on top of the base video."""
    _status(f"overlaying {overlay_path.name} at ({x},{y})")
    if scale:
        vf = f"[1:v]scale={scale}:-2[ov];[0:v][ov]overlay=x={x}:y={y}:format=auto:eof_action=pass:repeatlast=0[v]"
    else:
        vf = f"[0:v][1:v]overlay=x={x}:y={y}:format=auto:eof_action=pass:repeatlast=0[v]"
    r = _run(["ffmpeg", "-y",
              "-i", str(input_path),
              "-i", str(overlay_path),
              "-filter_complex", vf,
              "-map", "[v]", "-map", "0:a?"]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] overlay failed:\n{r.stderr}")


def op_side_by_side(left_path: Path, right_path: Path, output_path: Path,
                    cfg: EncodeConfig, strip_meta: bool) -> None:
    """Place two videos side by side (horizontal stack). Auto-matches heights."""
    _status(f"side-by-side: {left_path.name} | {right_path.name}")
    h = get_metadata(str(left_path))["height"]
    fc = (f"[0:v]scale=-2:{h}[l];"
          f"[1:v]scale=-2:{h}[r];"
          f"[l][r]hstack=inputs=2[vout]")
    r = _run(["ffmpeg", "-y",
              "-i", str(left_path),
              "-i", str(right_path),
              "-filter_complex", fc,
              "-map", "[vout]", "-map", "0:a?"]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_flags()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] side-by-side failed:\n{r.stderr}")


def op_stack(top_path: Path, bottom_path: Path, output_path: Path,
             cfg: EncodeConfig, strip_meta: bool) -> None:
    """Stack two videos vertically. Auto-matches widths."""
    _status(f"stacking: {top_path.name} / {bottom_path.name}")
    w = get_metadata(str(top_path))["width"]
    fc = (f"[0:v]scale={w}:-2[t];"
          f"[1:v]scale={w}:-2[b];"
          f"[t][b]vstack=inputs=2[vout]")
    r = _run(["ffmpeg", "-y",
              "-i", str(top_path),
              "-i", str(bottom_path),
              "-filter_complex", fc,
              "-map", "[vout]", "-map", "0:a?"]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_flags()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] stack failed:\n{r.stderr}")


def op_crossfade(clip1: Path, clip2: Path, output_path: Path,
                 fade_duration: float, clip1_duration: float,
                 cfg: EncodeConfig, strip_meta: bool) -> None:
    """Crossfade transition between two clips. Handles silent inputs and mismatched sizes."""
    _status(f"crossfade ({fade_duration}s) between {clip1.name} and {clip2.name}")
    offset = max(0.0, clip1_duration - fade_duration)

    meta1 = get_metadata(str(clip1))
    meta2 = get_metadata(str(clip2))
    both_have_audio = meta1.get("has_audio") and meta2.get("has_audio")
    w, h = meta1["width"], meta1["height"]

    if (meta2["width"], meta2["height"]) != (w, h):
        _status(f"scaling clip 2 ({meta2['width']}x{meta2['height']}) to match clip 1 ({w}x{h})")

    # Always scale clip 2 to match clip 1 (xfade requires identical sizes)
    video_fc = (
        f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1[c2v];"
        f"[0:v]setsar=1[c1v];"
        f"[c1v][c2v]xfade=transition=fade:duration={fade_duration:.3f}:offset={offset:.3f}[vout]"
    )

    cmd = ["ffmpeg", "-y", "-i", str(clip1), "-i", str(clip2), "-filter_complex"]
    if both_have_audio:
        cmd.append(video_fc + f";[0:a][1:a]acrossfade=d={fade_duration:.3f}[aout]")
        cmd += ["-map", "[vout]", "-map", "[aout]"]
        cmd += _strip_flags(strip_meta)
        cmd += cfg.video_flags() + cfg.audio_flags()
    else:
        _status("one or both clips have no audio — video-only crossfade")
        cmd.append(video_fc)
        cmd += ["-map", "[vout]"]
        cmd += _strip_flags(strip_meta)
        cmd += cfg.video_flags()
    cmd.append(str(output_path))

    r = _run(cmd, check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] crossfade failed:\n{r.stderr}")


def op_convert(input_path: Path, output_path: Path,
               cfg: EncodeConfig, strip_meta: bool) -> None:
    """Re-encode the video into a different container (format conversion)."""
    _status(f"converting to {output_path.suffix.lstrip('.')}")
    r = _run(["ffmpeg", "-y", "-i", str(input_path)]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_flags()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] convert failed:\n{r.stderr}")


def op_pip(main_path: Path, pip_path: Path, output_path: Path,
           position: str, pip_width: int,
           cfg: EncodeConfig, strip_meta: bool) -> None:
    """Picture-in-picture: embed a smaller video in a corner."""
    _status(f"picture-in-picture ({position}): {pip_path.name}")
    # pip is scaled to pip_width, positioned in a corner with 20px margin
    pos_map = {
        "top-right":    "main_w-overlay_w-20:20",
        "top-left":     "20:20",
        "bottom-right": "main_w-overlay_w-20:main_h-overlay_h-20",
        "bottom-left":  "20:main_h-overlay_h-20",
    }
    xy = pos_map.get(position, pos_map["top-right"])
    fc = f"[1]scale={pip_width}:-2[pip];[0][pip]overlay={xy}"
    r = _run(["ffmpeg", "-y",
              "-i", str(main_path),
              "-i", str(pip_path),
              "-filter_complex", fc]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] pip failed:\n{r.stderr}")


# ---------------------------------------------------------------------------
# Cinematic operations (Phase 1)
# ---------------------------------------------------------------------------

def op_look(input_path: Path, output_path: Path, look_name: str,
            cfg: EncodeConfig, strip_meta: bool) -> None:
    """Apply a named color preset (cinematic, moody, warm, etc.)."""
    try:
        vf = get_look_filter(look_name)
    except ValueError as e:
        raise SystemExit(f"[edit] {e}")
    _status(f"applying look: {look_name}")
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", vf]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] look '{look_name}' failed:\n{r.stderr}")


def op_lut(input_path: Path, output_path: Path, lut_path_str: str,
           cfg: EncodeConfig, strip_meta: bool) -> None:
    """Apply a .cube 3D LUT for cinematic color grading."""
    lut = Path(lut_path_str).expanduser().resolve()
    if not lut.exists():
        raise SystemExit(f"[edit] LUT file not found: {lut}")
    if lut.suffix.lower() != ".cube":
        raise SystemExit(f"[edit] LUT must be a .cube file. Got: {lut.suffix}")
    # Escape for ffmpeg filter syntax: normalize separators, escape special chars, quote
    safe_path = str(lut).replace("\\", "/")
    safe_path = safe_path.replace("'", r"\'")
    safe_path = safe_path.replace(":", r"\:")
    vf = f"lut3d=file='{safe_path}'"
    _status(f"applying LUT: {lut.name}")
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", vf]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        err = r.stderr.lower()
        if "lut3d" in err and ("not found" in err or "no such filter" in err):
            raise SystemExit(
                "[edit] lut3d filter unavailable — this ffmpeg build lacks lut3d. "
                "Try: ffmpeg -filters | grep lut3d"
            )
        raise SystemExit(f"[edit] LUT apply failed:\n{r.stderr}")


def op_letterbox(input_path: Path, output_path: Path, ratio_str: str,
                 cfg: EncodeConfig, strip_meta: bool) -> None:
    """Extend canvas with black bars to match a target aspect ratio using `pad`."""
    ratio = _parse_ratio(ratio_str)
    if ratio <= 0:
        raise SystemExit(f"[edit] Ratio must be positive. Got: {ratio}")
    meta = get_metadata(str(input_path))
    w, h = meta["width"], meta["height"]
    src_aspect = w / h

    if abs(src_aspect - ratio) < 0.001:
        raise SystemExit(
            f"[edit] Source ({w}x{h}) already matches target ratio {ratio_str} — "
            "no bars to add."
        )

    if src_aspect < ratio:
        # Source narrower than target → extend horizontally (side bars)
        target_w = int(h * ratio) & ~1
        target_h = h
        vf = f"pad={target_w}:{target_h}:(ow-iw)/2:0:color=black"
    else:
        # Source wider than target → extend vertically (true letterbox bars)
        target_h = int(w / ratio) & ~1
        target_w = w
        vf = f"pad={target_w}:{target_h}:0:(oh-ih)/2:color=black"

    _status(f"letterbox to {ratio_str}: {w}x{h} → {target_w}x{target_h}")
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", vf]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] letterbox failed:\n{r.stderr}")


def op_fps(input_path: Path, output_path: Path, fps: float,
           cfg: EncodeConfig, strip_meta: bool) -> None:
    """Convert to a constant target frame rate (forces CFR)."""
    if fps <= 0:
        raise SystemExit(f"[edit] --fps must be a positive number. Got: {fps}")
    _status(f"converting to {fps} fps (CFR)")
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", f"fps={fps}",
              "-vsync", "cfr"]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] fps conversion failed:\n{r.stderr}")


# ---------------------------------------------------------------------------
# Texture and motion operations (Phase 2)
# ---------------------------------------------------------------------------

def op_vignette(input_path: Path, output_path: Path, strength: float,
                cfg: EncodeConfig, strip_meta: bool) -> None:
    """Apply a soft vignette (edge darkening) effect."""
    if not (0.1 <= strength <= 1.0):
        raise SystemExit("[edit] --vignette strength must be between 0.1 and 1.0.")
    _status(f"applying vignette (strength={strength})")
    angle = strength * math.pi / 2
    vf = f"vignette=angle={angle:.6f}"
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", vf]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] vignette failed:\n{r.stderr}")


def op_grain(input_path: Path, output_path: Path, strength: float,
             cfg: EncodeConfig, strip_meta: bool) -> None:
    """Add analog film grain noise."""
    if not (1 <= strength <= 50):
        raise SystemExit("[edit] --grain strength must be between 1 and 50.")
    grain = int(round(strength))
    _status(f"adding grain (strength={strength})")
    vf = f"noise=alls={grain}:allf=t+u"
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", vf]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] grain failed:\n{r.stderr}")


def op_reverse(input_path: Path, output_path: Path, meta: dict,
               cfg: EncodeConfig, strip_meta: bool) -> None:
    """Play video (and audio) in reverse."""
    if meta["duration_seconds"] > 600:
        _status("Warning: reverse loads full video into RAM — may be slow for long clips.")
    _status("reversing video")
    has_audio = meta.get("has_audio", False)
    cmd = ["ffmpeg", "-y", "-i", str(input_path), "-vf", "reverse"]
    if has_audio:
        cmd += ["-af", "areverse"]
    cmd += _strip_flags(strip_meta)
    if has_audio:
        cmd += cfg.video_flags() + cfg.audio_flags()
    else:
        cmd += cfg.video_flags()
    cmd.append(str(output_path))
    r = _run(cmd, check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] reverse failed:\n{r.stderr}")


def op_loop(input_path: Path, output_path: Path, n: int,
            cfg: EncodeConfig, strip_meta: bool, work_dir: Path) -> None:
    """Repeat the clip N times back to back."""
    if n < 2:
        raise SystemExit("[edit] --loop N must be 2 or greater.")
    _status(f"looping {n} times")
    _concat_files([input_path] * n, output_path, cfg, strip_meta)


def op_boomerang(input_path: Path, output_path: Path, meta: dict,
                 cfg: EncodeConfig, strip_meta: bool, work_dir: Path) -> None:
    """Concat clip + reversed clip (forward then backward)."""
    if meta["duration_seconds"] > 300:
        _status("Warning: boomerang reverses the full clip — may be slow for long clips.")
    _status("creating boomerang (forward + reverse)")
    has_audio = meta.get("has_audio", False)
    reversed_path = work_dir / "boomerang_rev.mp4"
    cmd = ["ffmpeg", "-y", "-i", str(input_path), "-vf", "reverse"]
    if has_audio:
        cmd += ["-af", "areverse"]
    cmd += cfg.video_flags()
    if has_audio:
        cmd += cfg.audio_flags()
    cmd.append(str(reversed_path))
    r = _run(cmd, check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] boomerang reverse step failed:\n{r.stderr}")
    _concat_files([input_path, reversed_path], output_path, cfg, strip_meta)


def op_stabilize(input_path: Path, output_path: Path,
                 cfg: EncodeConfig, strip_meta: bool, work_dir: Path) -> None:
    """Two-pass video stabilization via vidstab."""
    _check_vidstab()
    trf_path = work_dir / "transforms.trf"
    safe_trf = str(trf_path).replace("\\", "/").replace("'", r"\'").replace(":", r"\:")

    _status("stabilize pass 1/2: analyzing motion")
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", f"vidstabdetect=shakiness=5:accuracy=15:result='{safe_trf}'",
              "-f", "null", "-"], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] stabilize pass 1 failed:\n{r.stderr}")
    if not trf_path.exists():
        raise SystemExit("[edit] stabilize pass 1 produced no transform file.")

    _status("stabilize pass 2/2: applying transforms")
    vf = f"vidstabtransform=input='{safe_trf}':smoothing=10,unsharp=5:5:0.8:3:3:0.4"
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", vf]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] stabilize pass 2 failed:\n{r.stderr}")


def op_blur(input_path: Path, output_path: Path, region: str,
            meta: dict, cfg: EncodeConfig, strip_meta: bool) -> None:
    """Blur a rectangular region for privacy (faces, plates, etc.)."""
    parts = region.split(":")
    if len(parts) != 4:
        raise SystemExit(f"[edit] --blur requires W:H:X:Y format. Got: {region!r}")
    try:
        w, h, x, y = (int(p) for p in parts)
    except ValueError:
        raise SystemExit(f"[edit] --blur values must be integers. Got: {region!r}")
    if w <= 0 or h <= 0:
        raise SystemExit(f"[edit] --blur W and H must be positive. Got: W={w}, H={h}")
    if x < 0 or y < 0:
        raise SystemExit(f"[edit] --blur X and Y must be non-negative. Got: X={x}, Y={y}")
    vid_w, vid_h = meta["width"], meta["height"]
    if x + w > vid_w or y + h > vid_h:
        raise SystemExit(
            f"[edit] --blur region {w}x{h} at ({x},{y}) extends outside "
            f"video bounds {vid_w}x{vid_h}."
        )
    w = w & ~1
    h = h & ~1
    if w < 2 or h < 2:
        raise SystemExit("[edit] --blur W and H must be at least 2 after rounding to even.")
    _status(f"blurring region {w}x{h} at ({x},{y})")
    fc = f"[0]crop={w}:{h}:{x}:{y},boxblur=20:5[b];[0][b]overlay={x}:{y}"
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-filter_complex", fc]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] blur failed:\n{r.stderr}")


def op_normalize_audio(input_path: Path, output_path: Path,
                       cfg: EncodeConfig, strip_meta: bool, meta: dict) -> None:
    """Normalize audio loudness to EBU R128 (-16 LUFS). Video is stream-copied."""
    if not meta.get("has_audio"):
        raise SystemExit("[edit] normalize-audio requires an input with an audio stream.")
    _status("normalizing audio loudness (EBU R128, -16 LUFS)")
    cmd = ["ffmpeg", "-y", "-i", str(input_path),
           "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
           "-c:v", "copy"]
    cmd += _strip_flags(strip_meta)
    cmd += cfg.audio_flags()
    cmd += [str(output_path)]
    r = _run(cmd, check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] normalize-audio failed:\n{r.stderr}")


def op_sharpen(input_path: Path, output_path: Path,
               cfg: EncodeConfig, strip_meta: bool) -> None:
    """Sharpen video using unsharp mask."""
    _status("sharpening (unsharp mask)")
    r = _run(["ffmpeg", "-y", "-i", str(input_path),
              "-vf", "unsharp=5:5:0.8:3:3:0.4"]
             + _strip_flags(strip_meta)
             + cfg.video_flags() + cfg.audio_copy()
             + [str(output_path)], check=False)
    if r.returncode != 0:
        raise SystemExit(f"[edit] sharpen failed:\n{r.stderr}")


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
    ap.add_argument("--trim-precise", nargs=2, metavar=("START", "END"),
                    help="Frame-accurate trim (always re-encodes). Use when --trim's "
                         "keyframe snapping causes visible extra frames at cut points.")
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
    ap.add_argument("--convert", action="store_true",
                    help="Convert format only (no other edits). Pair with --format.")

    # Cinematic operations (Phase 1)
    ap.add_argument("--look", metavar="NAME",
                    help="Apply a named color preset: cinematic, moody, warm, cool, "
                         "bw, vintage, teal-orange, film")
    ap.add_argument("--lut", metavar="PATH",
                    help="Apply a .cube 3D LUT file for color grading")
    ap.add_argument("--letterbox", metavar="RATIO",
                    help="Add letterbox bars to target ratio, e.g. 2.35:1, 1.85:1")
    ap.add_argument("--fps", type=float, metavar="N",
                    help="Convert to constant frame rate N (e.g. 24)")

    # Texture and motion (Phase 2)
    ap.add_argument("--vignette", type=float, nargs="?", const=0.5, metavar="STRENGTH",
                    help="Soft edge darkening, strength 0.1–1.0 (default 0.5)")
    ap.add_argument("--grain", type=float, nargs="?", const=15.0, metavar="STRENGTH",
                    help="Analog film grain noise, strength 1–50 (default 15)")
    ap.add_argument("--reverse", action="store_true",
                    help="Play video (and audio) in reverse")
    ap.add_argument("--loop", type=int, metavar="N",
                    help="Repeat clip N times (N >= 2)")
    ap.add_argument("--boomerang", action="store_true",
                    help="Concat clip + reversed clip (forward then backward)")
    ap.add_argument("--stabilize", action="store_true",
                    help="Two-pass video stabilization (vidstabdetect + vidstabtransform). "
                         "Requires ffmpeg built with libvidstab. "
                         "Check: ffmpeg -filters | grep vidstab")
    ap.add_argument("--blur", metavar="W:H:X:Y",
                    help="Blur a rectangular region for privacy (faces, plates, etc.). "
                         "Format: width:height:x:y (pixels, top-left origin).")
    ap.add_argument("--normalize-audio", action="store_true",
                    help="Normalize audio loudness to EBU R128 (-16 LUFS, -1.5 dBTP, LRA 11). "
                         "Video is stream-copied unchanged. One-pass loudnorm.")
    ap.add_argument("--sharpen", action="store_true",
                    help="Sharpen video using unsharp mask (luma 5x5 +0.8, chroma 3x3 +0.4).")

    # Output quality controls (global modifiers, not operations)
    ap.add_argument("--strip-metadata", action="store_true",
                    help="Remove GPS, device serial, timestamps, chapters from output")
    ap.add_argument("--quality", choices=["preview", "standard", "high", "master"],
                    default=None,
                    help="Export quality preset (default: standard = libx264 CRF 18 fast)")
    ap.add_argument("--crf", type=int, default=None,
                    help="CRF value 0–51 (lower = better quality). Overrides --quality.")
    ap.add_argument("--codec", choices=["h264", "h265"], default=None,
                    help="Video codec. h265 needs libx265 in ffmpeg build.")
    ap.add_argument("--preset", choices=["ultrafast", "fast", "medium", "slow"],
                    default=None,
                    help="Encoder speed/compression tradeoff. Overrides --quality.")
    ap.add_argument("--audio-bitrate", default=None,
                    help="Output audio bitrate, e.g. 192k, 320k")

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

    # Resolve output encoding parameters once for all ops
    cfg = EncodeConfig(args)
    strip_meta = bool(args.strip_metadata)

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
        args.trim_precise is not None,
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
        args.convert,
        args.look is not None,
        args.lut is not None,
        args.letterbox is not None,
        args.fps is not None,
        args.vignette is not None,
        args.grain is not None,
        args.reverse,
        args.loop is not None,
        args.boomerang,
        args.stabilize,
        args.blur is not None,
        args.normalize_audio,
        args.sharpen,
    ])

    if op_count == 0:
        raise SystemExit("[edit] No operation specified. Use --trim, --trim-precise, --cut, --concat, --speed, "
                         "--text, --mute, --volume, --replace-audio, --fade-in/out, "
                         "--resize, --rotate, --crop, --overlay, --side-by-side, "
                         "--stack, --crossfade, --pip, --convert, --look, --lut, "
                         "--letterbox, --fps, --vignette, --grain, --reverse, --loop, "
                         "or --boomerang, --stabilize, --blur, --normalize-audio, --sharpen.")
    if op_count > 1:
        raise SystemExit("[edit] Specify one operation per call. Chain calls for multi-step edits "
                         "(output of one → input of next).")

    if args.trim:
        raw_start, raw_end = args.trim
        t_start = parse_time(raw_start) or 0.0
        t_end = None if raw_end.lower() == "end" else parse_time(raw_end)
        op_trim(input_path, output_path, t_start, t_end, cfg, strip_meta)

    elif args.trim_precise:
        raw_start, raw_end = args.trim_precise
        t_start = parse_time(raw_start) or 0.0
        t_end = None if raw_end.lower() == "end" else parse_time(raw_end)
        op_trim_precise(input_path, output_path, t_start, t_end, cfg, strip_meta)

    elif args.cut:
        t_start = parse_time(args.cut[0]) or 0.0
        t_end = parse_time(args.cut[1]) or duration
        op_cut(input_path, output_path, t_start, t_end, duration, cfg, strip_meta)

    elif args.concat:
        extra = [Path(f).expanduser().resolve() for f in args.concat]
        for p in extra:
            if not p.exists():
                raise SystemExit(f"[edit] File not found: {p}")
        op_concat([input_path] + extra, output_path, cfg, strip_meta)

    elif args.speed is not None:
        op_speed(input_path, output_path, args.speed, cfg, strip_meta)

    elif args.text is not None:
        t_start = parse_time(args.text_start) if args.text_start else None
        t_end = parse_time(args.text_end) if args.text_end else None
        op_text(input_path, output_path, args.text,
                args.text_position, args.text_size, args.text_color,
                t_start, t_end, cfg, strip_meta)

    elif args.mute:
        op_mute(input_path, output_path, strip_meta)

    elif args.volume is not None:
        op_volume(input_path, output_path, args.volume, cfg, strip_meta)

    elif args.replace_audio:
        audio_path = Path(args.replace_audio).expanduser().resolve()
        if not audio_path.exists():
            raise SystemExit(f"[edit] Audio file not found: {audio_path}")
        op_replace_audio(input_path, audio_path, output_path, cfg, strip_meta)

    elif args.fade_in is not None or args.fade_out is not None:
        op_fade(input_path, output_path, args.fade_in, args.fade_out, duration,
                cfg, strip_meta)

    elif args.resize:
        w, h = _parse_size(args.resize)
        op_resize(input_path, output_path, w, h, cfg, strip_meta)

    elif args.rotate:
        op_rotate(input_path, output_path, args.rotate, cfg, strip_meta)

    elif args.crop:
        op_crop(input_path, output_path, args.crop, cfg, strip_meta)

    elif args.overlay:
        overlay_path = Path(args.overlay).expanduser().resolve()
        if not overlay_path.exists():
            raise SystemExit(f"[edit] Overlay file not found: {overlay_path}")
        op_overlay(input_path, overlay_path, output_path,
                   args.overlay_x, args.overlay_y, args.overlay_scale,
                   cfg, strip_meta)

    elif args.side_by_side:
        right_path = Path(args.side_by_side).expanduser().resolve()
        if not right_path.exists():
            raise SystemExit(f"[edit] File not found: {right_path}")
        op_side_by_side(input_path, right_path, output_path, cfg, strip_meta)

    elif args.stack:
        bottom_path = Path(args.stack).expanduser().resolve()
        if not bottom_path.exists():
            raise SystemExit(f"[edit] File not found: {bottom_path}")
        op_stack(input_path, bottom_path, output_path, cfg, strip_meta)

    elif args.crossfade:
        clip2_path = Path(args.crossfade).expanduser().resolve()
        if not clip2_path.exists():
            raise SystemExit(f"[edit] File not found: {clip2_path}")
        op_crossfade(input_path, clip2_path, output_path,
                     args.crossfade_duration, duration, cfg, strip_meta)

    elif args.pip:
        pip_path = Path(args.pip).expanduser().resolve()
        if not pip_path.exists():
            raise SystemExit(f"[edit] PiP file not found: {pip_path}")
        op_pip(input_path, pip_path, output_path, args.pip_position, args.pip_width,
               cfg, strip_meta)

    elif args.convert:
        if not args.format and not args.output:
            raise SystemExit("[edit] --convert needs either --format or --output with a "
                             "different extension than the input.")
        op_convert(input_path, output_path, cfg, strip_meta)

    elif args.look is not None:
        op_look(input_path, output_path, args.look, cfg, strip_meta)

    elif args.lut is not None:
        op_lut(input_path, output_path, args.lut, cfg, strip_meta)

    elif args.letterbox is not None:
        op_letterbox(input_path, output_path, args.letterbox, cfg, strip_meta)

    elif args.fps is not None:
        op_fps(input_path, output_path, args.fps, cfg, strip_meta)

    elif args.vignette is not None:
        op_vignette(input_path, output_path, args.vignette, cfg, strip_meta)

    elif args.grain is not None:
        op_grain(input_path, output_path, args.grain, cfg, strip_meta)

    elif args.reverse:
        op_reverse(input_path, output_path, meta, cfg, strip_meta)

    elif args.loop is not None:
        op_loop(input_path, output_path, args.loop, cfg, strip_meta, work_dir)

    elif args.boomerang:
        op_boomerang(input_path, output_path, meta, cfg, strip_meta, work_dir)

    elif args.stabilize:
        op_stabilize(input_path, output_path, cfg, strip_meta, work_dir)

    elif args.blur:
        op_blur(input_path, output_path, args.blur, meta, cfg, strip_meta)

    elif args.normalize_audio:
        op_normalize_audio(input_path, output_path, cfg, strip_meta, meta)

    elif args.sharpen:
        op_sharpen(input_path, output_path, cfg, strip_meta)

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

#!/usr/bin/env python3
"""
smoke_edit.py -- smoke and regression tests for scripts/edit.py.

Generates tiny synthetic media with ffmpeg, then runs each CLI operation
and verifies outputs. Takes ~30-90 seconds depending on hardware.

Usage:
    python3 scripts/smoke_edit.py
    python3 scripts/smoke_edit.py --keep          # retain temp dir
    python3 scripts/smoke_edit.py --only trim crop # run a subset
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
EDIT      = [sys.executable, str(REPO_ROOT / "scripts" / "edit.py")]
QUALITY   = ["--quality", "preview"]  # libx264 CRF28 ultrafast


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

_results: list[tuple[str, bool, str]] = []  # (name, passed, note)


def _pass(name: str, note: str = "") -> None:
    _results.append((name, True, note))
    tag = f"  SKIP  {name}  -- {note}" if note.startswith("SKIP") else f"  PASS  {name}"
    if note and not note.startswith("SKIP"):
        tag += f"  ({note})"
    print(tag)


def _fail(name: str, reason: str) -> None:
    _results.append((name, False, reason))
    print(f"  FAIL  {name}  -- {reason}")


def _skip(name: str, reason: str) -> None:
    _results.append((name, True, f"SKIP: {reason}"))
    print(f"  SKIP  {name}  -- {reason}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _require_tool(name: str) -> None:
    if not shutil.which(name):
        raise SystemExit(f"[smoke] '{name}' not found on PATH. Install ffmpeg and retry.")


def _ffprobe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise ValueError(f"ffprobe failed: {r.stderr.strip()}")
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    fmt   = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "duration":  float(fmt.get("duration") or video.get("duration") or 0),
        "width":     video.get("width"),
        "height":    video.get("height"),
        "has_audio": audio is not None,
    }


def _check(path: Path,
           min_dur: float | None = None,
           max_dur: float | None = None,
           w: int | None = None,
           h: int | None = None) -> str | None:
    """Return an error string, or None if all checks pass."""
    if not path.exists():
        return "output file missing"
    if path.stat().st_size == 0:
        return "output file is empty"
    try:
        m = _ffprobe(path)
    except ValueError as e:
        return str(e)
    if m["width"] is None:
        return "output has no video stream"
    if min_dur is not None and m["duration"] < min_dur:
        return f"duration {m['duration']:.2f}s < expected min {min_dur:.2f}s"
    if max_dur is not None and m["duration"] > max_dur:
        return f"duration {m['duration']:.2f}s > expected max {max_dur:.2f}s"
    if w is not None and m["width"] != w:
        return f"width {m['width']} != expected {w}"
    if h is not None and m["height"] != h:
        return f"height {m['height']} != expected {h}"
    return None


def run_ok(name: str, cmd: list[str], out: Path,
           min_dur: float | None = None,
           max_dur: float | None = None,
           w: int | None = None,
           h: int | None = None) -> bool:
    """Run cmd, expect exit 0, then verify the output file."""
    r = _run(cmd)
    if r.returncode != 0:
        _fail(name, f"exit {r.returncode}: {r.stderr.strip()[-300:]}")
        return False
    err = _check(out, min_dur, max_dur, w, h)
    if err:
        _fail(name, err)
        return False
    _pass(name)
    return True


def run_fail(name: str, cmd: list[str], out: Path | None = None) -> bool:
    """Run cmd, expect a non-zero exit code. If out is given, assert no artifact was left."""
    r = _run(cmd)
    if r.returncode == 0:
        _fail(name, "expected failure but got exit 0")
        return False
    if out is not None and out.exists() and out.stat().st_size > 0:
        _fail(name, f"exited {r.returncode} as expected but left a non-empty output: {out.name}")
        return False
    _pass(name, f"correctly exited {r.returncode}")
    return True


# ---------------------------------------------------------------------------
# Test-media generation
# ---------------------------------------------------------------------------

def _make_media(d: Path) -> tuple[Path, Path, Path]:
    """Create and return paths for clip_audio.mp4, clip_silent.mp4, logo.png."""
    clip_audio  = d / "clip_audio.mp4"
    clip_silent = d / "clip_silent.mp4"
    logo        = d / "logo.png"

    r = _run(["ffmpeg", "-y",
              "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=10",
              "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
              "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
              "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "96k",
              str(clip_audio)])
    if r.returncode != 0:
        raise SystemExit(f"[smoke] failed to create clip_audio.mp4:\n{r.stderr}")

    r = _run(["ffmpeg", "-y",
              "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=10",
              "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
              "-pix_fmt", "yuv420p", "-an",
              str(clip_silent)])
    if r.returncode != 0:
        raise SystemExit(f"[smoke] failed to create clip_silent.mp4:\n{r.stderr}")

    r = _run(["ffmpeg", "-y",
              "-f", "lavfi", "-i", "color=c=red:s=64x64:r=1",
              "-frames:v", "1",
              str(logo)])
    if r.returncode != 0:
        raise SystemExit(f"[smoke] failed to create logo.png:\n{r.stderr}")

    return clip_audio, clip_silent, logo


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

KNOWN_TESTS: frozenset[str] = frozenset({
    "trim", "cut", "crop", "resize", "rotate", "concat", "speed",
    "overlay", "pip", "side-by-side", "stabilize", "look",
    "normalize-audio", "sharpen", "denoise", "watermark-text", "watermark-image",
    "speed-zero", "speed-negative", "normalize-audio-no-audio", "watermark-both",
})


def run_all(d: Path, only: set[str] | None) -> None:
    clip_a, clip_s, logo = _make_media(d)
    print(f"  media: {clip_a.name}, {clip_s.name}, {logo.name}\n")

    def want(name: str) -> bool:
        return only is None or name in only

    # -- trim -----------------------------------------------------------------
    if want("trim"):
        out = d / "trim.mp4"
        run_ok("trim",
               EDIT + [str(clip_a), "--trim", "2", "7", "--output", str(out)] + QUALITY,
               out, min_dur=3.5, max_dur=6.5)

    # -- cut ------------------------------------------------------------------
    if want("cut"):
        out = d / "cut.mp4"
        run_ok("cut",
               EDIT + [str(clip_a), "--cut", "2", "7", "--output", str(out)] + QUALITY,
               out, min_dur=3.5, max_dur=6.5)

    # -- crop -----------------------------------------------------------------
    if want("crop"):
        out = d / "crop.mp4"
        run_ok("crop",
               EDIT + [str(clip_a), "--crop", "160:120:80:60", "--output", str(out)] + QUALITY,
               out, min_dur=8.0, w=160, h=120)

    # -- resize ---------------------------------------------------------------
    if want("resize"):
        out = d / "resize.mp4"
        run_ok("resize",
               EDIT + [str(clip_a), "--resize", "160x120", "--output", str(out)] + QUALITY,
               out, min_dur=8.0, w=160, h=120)

    # -- rotate ---------------------------------------------------------------
    if want("rotate"):
        out = d / "rotate.mp4"
        # 320x240 rotated 90 deg -> 240x320
        run_ok("rotate",
               EDIT + [str(clip_a), "--rotate", "90", "--output", str(out)] + QUALITY,
               out, min_dur=8.0, w=240, h=320)

    # -- concat ---------------------------------------------------------------
    if want("concat"):
        out = d / "concat.mp4"
        run_ok("concat",
               EDIT + [str(clip_a), "--concat", str(clip_a), "--output", str(out)] + QUALITY,
               out, min_dur=18.0, max_dur=22.0)

    # -- speed ----------------------------------------------------------------
    if want("speed"):
        out = d / "speed.mp4"
        run_ok("speed",
               EDIT + [str(clip_a), "--speed", "2.0", "--output", str(out)] + QUALITY,
               out, min_dur=3.5, max_dur=6.5)

    # -- overlay --------------------------------------------------------------
    if want("overlay"):
        out = d / "overlay.mp4"
        run_ok("overlay",
               EDIT + [str(clip_a), "--overlay", str(clip_s),
                       "--overlay-x", "10", "--overlay-y", "10",
                       "--output", str(out)] + QUALITY,
               out, min_dur=8.0)

    # -- pip ------------------------------------------------------------------
    if want("pip"):
        out = d / "pip.mp4"
        run_ok("pip",
               EDIT + [str(clip_a), "--pip", str(clip_s),
                       "--pip-position", "top-right",
                       "--output", str(out)] + QUALITY,
               out, min_dur=8.0, w=320, h=240)

    # -- side-by-side ---------------------------------------------------------
    if want("side-by-side"):
        out = d / "sbs.mp4"
        # two 320x240 clips -> 640x240
        run_ok("side-by-side",
               EDIT + [str(clip_a), "--side-by-side", str(clip_a),
                       "--output", str(out)] + QUALITY,
               out, w=640, h=240)

    # -- stabilize (skip if this ffmpeg build lacks vidstab) ------------------
    if want("stabilize"):
        r = _run(["ffmpeg", "-hide_banner", "-filters"])
        if "vidstabdetect" not in r.stdout:
            _skip("stabilize", "ffmpeg build lacks vidstab")
        else:
            out = d / "stabilize.mp4"
            run_ok("stabilize",
                   EDIT + [str(clip_a), "--stabilize", "--output", str(out)] + QUALITY,
                   out, min_dur=8.0)

    # -- look (color preset) --------------------------------------------------
    if want("look"):
        out = d / "look.mp4"
        run_ok("look",
               EDIT + [str(clip_a), "--look", "cinematic", "--output", str(out)] + QUALITY,
               out, min_dur=8.0)

    # -- normalize-audio ------------------------------------------------------
    if want("normalize-audio"):
        out = d / "normalize.mp4"
        run_ok("normalize-audio",
               EDIT + [str(clip_a), "--normalize-audio", "--output", str(out)] + QUALITY,
               out, min_dur=8.0)

    # -- sharpen --------------------------------------------------------------
    if want("sharpen"):
        out = d / "sharpen.mp4"
        run_ok("sharpen",
               EDIT + [str(clip_a), "--sharpen", "--output", str(out)] + QUALITY,
               out, min_dur=8.0)

    # -- denoise ----------------------------------------------------------------
    if want("denoise"):
        out = d / "denoise.mp4"
        run_ok("denoise",
               EDIT + [str(clip_a), "--denoise", "--output", str(out)] + QUALITY,
               out, min_dur=8.0)

    # -- watermark-text -------------------------------------------------------
    if want("watermark-text"):
        out = d / "wm_text.mp4"
        run_ok("watermark-text",
               EDIT + [str(clip_a), "--watermark-text", "Smoke Test",
                       "--watermark-position", "bottom-right",
                       "--output", str(out)] + QUALITY,
               out, min_dur=8.0)

    # -- watermark-image ------------------------------------------------------
    if want("watermark-image"):
        out = d / "wm_image.mp4"
        run_ok("watermark-image",
               EDIT + [str(clip_a), "--watermark-image", str(logo),
                       "--watermark-position", "bottom-right",
                       "--output", str(out)] + QUALITY,
               out, min_dur=8.0)

    # -------------------------------------------------------------------------
    # Negative / guard tests
    # -------------------------------------------------------------------------

    if want("speed-zero"):
        _out = d / "_no_speed_zero.mp4"
        run_fail("speed-zero",
                 EDIT + [str(clip_a), "--speed", "0", "--output", str(_out)] + QUALITY,
                 out=_out)

    if want("speed-negative"):
        _out = d / "_no_speed_neg.mp4"
        run_fail("speed-negative",
                 EDIT + [str(clip_a), "--speed", "-1", "--output", str(_out)] + QUALITY,
                 out=_out)

    if want("normalize-audio-no-audio"):
        _out = d / "_no_norm_silent.mp4"
        run_fail("normalize-audio-no-audio",
                 EDIT + [str(clip_s), "--normalize-audio", "--output", str(_out)] + QUALITY,
                 out=_out)

    if want("watermark-both"):
        _out = d / "_no_wm_both.mp4"
        run_fail("watermark-both",
                 EDIT + [str(clip_a),
                         "--watermark-text", "Test",
                         "--watermark-image", str(logo),
                         "--output", str(_out)] + QUALITY,
                 out=_out)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke tests for scripts/edit.py.")
    ap.add_argument("--keep", action="store_true",
                    help="Keep temp directory after the run (useful for debugging failures).")
    ap.add_argument("--only", nargs="+", metavar="TEST",
                    help="Run only the named tests, e.g. --only trim crop resize.")
    args = ap.parse_args()

    _require_tool("ffmpeg")
    _require_tool("ffprobe")

    only = set(args.only) if args.only else None
    if only:
        unknown = only - KNOWN_TESTS
        if unknown:
            raise SystemExit(
                f"[smoke] Unknown test name(s): {', '.join(sorted(unknown))}\n"
                f"Valid names: {', '.join(sorted(KNOWN_TESTS))}"
            )

    tmp = Path(tempfile.mkdtemp(prefix="smoke-edit-"))
    print(f"\n[smoke] temp dir: {tmp}")
    print("[smoke] generating test media and running tests...\n")

    try:
        run_all(tmp, only)
    except SystemExit as e:
        print(f"\n[smoke] ABORTED: {e}", file=sys.stderr)
        return 1
    finally:
        if args.keep:
            print(f"\n[smoke] temp dir kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    passed  = sum(1 for _, ok, note in _results if ok and not note.startswith("SKIP"))
    skipped = sum(1 for _, ok, note in _results if ok and note.startswith("SKIP"))
    failed  = sum(1 for _, ok, _ in _results if not ok)
    total   = len(_results)

    print(f"\n[smoke] {total} tests -- {passed} passed, {skipped} skipped, {failed} failed")
    if failed:
        failing = [name for name, ok, _ in _results if not ok]
        print(f"[smoke] FAILED: {', '.join(failing)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
looks.py — named cinematic color presets for /edit.

Each entry is a single ffmpeg filter string (chained with commas) that can
be plugged into `-vf` directly. Presets are static literals — no values
interpolated from user input — so results are repeatable for a given input
and ffmpeg build.

Filter order within each preset:
  - eq adjusts global contrast/brightness/saturation first
  - curves / colorbalance shape the tone response second
  - vignette / final saturation pass last
"""

from __future__ import annotations


LOOKS: dict[str, str] = {
    "cinematic": (
        "eq=contrast=1.2:brightness=-0.03:saturation=0.9,"
        "curves=r='0/0 0.1/0.06 0.8/0.75 1/1'"
        ":g='0/0 0.1/0.10 0.9/0.85 1/1'"
        ":b='0/0 0.1/0.14 0.8/0.72 1/0.95'"
    ),
    "moody": (
        "eq=contrast=1.3:brightness=-0.08:saturation=0.7,"
        "colorbalance=ss=-0.1:ms=0.05:hs=-0.05"
    ),
    "warm": "colorbalance=rs=0.15:gs=0.05:bs=-0.1:rm=0.1:gm=0.05:bm=-0.05",
    "cool": "colorbalance=rs=-0.1:gs=0:bs=0.15:rm=-0.05:gm=0:bm=0.1",
    "bw":   "hue=s=0,eq=contrast=1.15",
    "vintage": (
        "curves=r='0/0.05 0.5/0.5 1/0.95'"
        ":g='0/0.02 0.5/0.45 1/0.88'"
        ":b='0/0.08 0.5/0.4 1/0.75',"
        "vignette"
    ),
    "teal-orange": (
        "colorbalance=rs=0.2:rm=0.15:gs=0.05:gm=0:bs=-0.15:bm=-0.1,"
        "eq=saturation=1.2"
    ),
    "film": (
        "curves=r='0/0.03 0.1/0.1 0.9/0.88 1/0.97'"
        ":g='0/0.01 0.1/0.09 0.9/0.86 1/0.96'"
        ":b='0/0.04 0.5/0.45 1/0.92',"
        "eq=contrast=1.1:saturation=0.95"
    ),
}


def get_look_filter(name: str) -> str:
    if name not in LOOKS:
        choices = ", ".join(sorted(LOOKS))
        raise ValueError(f"Unknown look '{name}'. Available: {choices}")
    return LOOKS[name]

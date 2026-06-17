# Quick Start: Edit Videos on Your PC

A simple menu that lets you trim, resize, rotate, speed up, and more — no command-line knowledge needed.

## What you need

- **Python 3.8 or newer.** Check with: `python3 --version`
- **ffmpeg and ffprobe.** The editor checks for these on startup and tells you how to install them if they are missing:
  - Windows: `winget install Gyan.FFmpeg`
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`

No other installs or packages are required.

## Setup

Download or clone this repo to your computer:

```
git clone https://github.com/deebee37/claude-video.git
```

That's it. No pip install needed.

## Step 1: Put your video in the input folder

Copy or move a video file into the `input/` folder inside the repo. Supported formats: `.mp4`, `.mov`, `.mkv`, `.webm`.

## Step 2: Run the editor

**Windows:** Double-click `run_easy_edit.bat`.

**macOS or Linux:** Open a terminal in the repo folder and run:

```
./run_easy_edit.sh
```

**Any platform:** You can also run directly with:

```
python3 scripts/easy_edit.py
```

## Step 3: Follow the menu

1. The editor lists your video files. Pick one by number.
2. It shows 11 editing operations. Pick one by number.
3. If the operation needs extra info (like a start time or size), it asks you.
4. It shows the exact command it will run and asks you to confirm with `y`.
5. The edit runs. When it finishes, your new file is saved in the `output/` folder.
6. It asks if you want to edit another video.

## Available operations

| # | Operation | What it does |
|---|-----------|-------------|
| 1 | Trim | Keep only a section of the video (set start and end times) |
| 2 | Cut | Remove a section from the middle (set start and end times) |
| 3 | Resize | Change the video dimensions (e.g. 1920x1080, 1280x720) |
| 4 | Rotate | Rotate the video 90, 180, or 270 degrees |
| 5 | Speed | Speed up or slow down (2.0 = double speed, 0.5 = half speed) |
| 6 | FPS | Change the frame rate (e.g. 24, 30, 60) |
| 7 | Normalize audio | Even out loud and quiet parts |
| 8 | Sharpen | Make the image crisper |
| 9 | Denoise | Reduce video grain and noise |
| 10 | Watermark (text) | Add text over the video (e.g. your name or a date) |
| 11 | Watermark (image) | Add a logo or image overlay |

## Where to find your edited video

Finished videos are saved in the `output/` folder. The filename includes the operation and a timestamp so nothing gets overwritten:

```
output/myvideo_trim_20260617_143022.mp4
```

## Quick checks

See the version:

```
python3 scripts/easy_edit.py --version
```

List all available operations:

```
python3 scripts/easy_edit.py --list-ops
```

## Troubleshooting

**"Missing required tool(s): ffmpeg"** — Install ffmpeg using the command shown on screen for your platform (see "What you need" above).

**"No video files found"** — Make sure your video is inside the `input/` folder and has one of these extensions: `.mp4`, `.mov`, `.mkv`, `.webm`.

**"Something went wrong"** — The error message explains what happened. Common causes: the input file is corrupted, or you entered an invalid time/size. Try again with a different value.

**WebM files** — WebM inputs are automatically saved as `.mkv` to preserve audio compatibility. This is normal.

**Cancel anytime** — Press `Ctrl+C` to stop the editor at any point.

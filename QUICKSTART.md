# Quick Start: Edit Videos on Your PC

Three ways to edit, easiest first. All of them use the same engine and save
results to the `output/` folder. **No pip installs needed — everything runs on
plain Python.**

| Editor | Best for | Start it with |
|---|---|---|
| **Web editor** (recommended) | Everyone — works in your browser, even from your phone | `run_web.bat` / `./run_web.sh` |
| Desktop GUI | A native window on your PC | `run_gui.bat` / `./run_gui.sh` |
| Menu (terminal) | Keyboard-only / SSH sessions | `run_easy_edit.bat` / `./run_easy_edit.sh` |

## What you need

- **Python 3.8 or newer.** Check with: `python3 --version`
- **ffmpeg and ffprobe.** The editors check for these on startup and tell you
  how to install them if they are missing:
  - Windows: `winget install Gyan.FFmpeg`
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`

## Setup

Download or clone this repo to your computer:

```
git clone https://github.com/deebee37/claude-video.git
```

That's it.

---

## The web editor (recommended)

**Windows:** Double-click `run_web.bat`.

**macOS or Linux:**

```
./run_web.sh
```

**Any platform / Replit:**

```
python3 scripts/web_edit.py
```

Then open **http://localhost:5000** in your browser. (On Replit the app
appears in the Preview pane automatically — it binds to the `PORT` Replit
provides.)

### Using it

1. **Upload a video** with the file picker — or drop the file into `input/`
   first and pick it from the dropdown.
2. **Choose an operation** — all 34 operations, grouped by category. The form
   shows only the fields that operation needs.
3. **Run Edit.** The edit runs in the background; a status page refreshes
   itself until it's done, so big videos won't freeze your browser.
4. **Preview and download** the result right on the page.

The **Library** tab lists every finished video in `output/` with preview and
download links.

### Phone bonus

If your phone is on the same wifi as your PC, open
`http://<your-PC's-IP>:5000` on the phone and edit from the couch.

### All 34 operations

- **Trim & Cut** — trim (keep a section), cut (remove a section)
- **Size & Framing** — resize, rotate, crop, letterbox
- **Speed & Time** — speed, FPS, reverse, loop, boomerang
- **Audio** — mute, volume, normalize, fade in, fade out, replace audio track
- **Enhance** — sharpen, denoise, stabilize, blur a region
- **Style** — 8 color looks (cinematic, moody, warm, cool, b&w, vintage,
  teal-orange, film), vignette, film grain
- **Text & Watermark** — text overlay (position/size/color/timing),
  text watermark, image/logo watermark
- **Combine two videos** — join, side-by-side, stack, crossfade,
  overlay, picture-in-picture
- **Convert** — mp4 / mov / mkv / webm

There's also an **Advanced: output quality** option on every edit
(preview / standard / high / master).

---

## The desktop GUI

**Windows:** Double-click `run_gui.bat`.
**macOS or Linux:** `./run_gui.sh`
**Any platform:** `python3 scripts/gui_edit.py`

A window opens: choose a video, pick one of 11 common operations, fill in the
fields, hit **Run Edit**.

---

## The terminal menu

**Windows:** Double-click `run_easy_edit.bat`.
**macOS or Linux:** `./run_easy_edit.sh`
**Any platform:** `python3 scripts/easy_edit.py`

1. The editor lists your video files. Pick one by number.
2. It shows 11 editing operations. Pick one by number.
3. If the operation needs extra info (like a start time or size), it asks you.
4. It shows the exact command it will run and asks you to confirm with `y`.
5. The edit runs. When it finishes, your new file is saved in `output/`.

Quick checks:

```
python3 scripts/easy_edit.py --version
python3 scripts/easy_edit.py --list-ops
```

---

## Where to find your edited video

Finished videos are saved in the `output/` folder. The filename includes the
operation and a timestamp so nothing gets overwritten:

```
output/myvideo_trim_20260617_143022.mp4
```

## Troubleshooting

**"Missing required tool(s): ffmpeg"** — Install ffmpeg using the command
shown on screen for your platform (see "What you need" above).

**Web editor: "This site can't be reached"** — Make sure the black
console window that `run_web.bat` opened is still running; it must stay open
while you edit. The address is `http://localhost:5000`.

**Port 5000 already in use** — Start it on another port:
`PORT=8080 python3 scripts/web_edit.py` (then open http://localhost:8080).

**"No video files found"** — Make sure your video is inside the `input/`
folder and has one of these extensions: `.mp4`, `.mov`, `.mkv`, `.webm`.

**WebM files** — WebM inputs are automatically saved as `.mkv` to preserve
audio compatibility. This is normal. (Converting *to* WebM via the Convert
operation produces a proper VP9/Opus `.webm`.)

**Cancel anytime** — Press `Ctrl+C` in the console to stop any of the
editors.

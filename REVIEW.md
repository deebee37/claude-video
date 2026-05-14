# claude-video — Project Review Brief

Please read this and give me your honest opinions, critique, and suggestions. I want specific, direct feedback — not just validation.

---

## What this is

`claude-video` is a Claude Code skill that gives Claude two new slash commands:

**`/watch`** — Watch and understand any video.
Paste a YouTube URL (or any URL yt-dlp supports) or a local file path. Claude downloads it with `yt-dlp`, extracts frames with `ffmpeg` at an auto-scaled rate (up to 100 frames, 2 fps max), pulls a timestamped transcript from native captions (free) or Whisper API (fallback), reads every frame as an image, and answers questions grounded in what it actually saw and heard.

**`/edit`** — Edit personal videos with natural language.
Point Claude at a video file on your phone or laptop. Tell it what you want in plain English. Claude translates that into `ffmpeg` commands, executes the edit, reads back 3 preview frames to verify the result, and tells you where the output file is. No AI generation, no subscriptions — just smart ffmpeg automation. Works on any video, any length, any format, any content rating.

---

## How it works — key architecture decisions

### Skill installation
The repo clones once to `~/.claude/skills/watch` (symlink). Claude Code sets `CLAUDE_SKILL_DIR` to that path. Slash commands live in `~/.claude/commands/`. No package manager, no daemon, no config files beyond a `.env` for optional Whisper keys.

### Python stdlib-only
All scripts (`watch.py`, `edit.py`, `download.py`, `frames.py`, `transcribe.py`, `whisper.py`, `setup.py`) use only Python stdlib + subprocess calls to `ffmpeg`/`ffprobe`/`yt-dlp`. No pip dependencies to install beyond yt-dlp itself.

### `/watch` frame budget
Claude's context window limits how many image tokens it can hold. The script uses a duration-aware budget:
- ≤30s → ~30 frames
- 1–3min → ~60 frames
- >10min → 100 frames (sparse scan, with warning)
Hard cap: 2 fps, 100 frames.

### `/edit` — one operation per call, chained
`edit.py` accepts exactly one operation flag per invocation. Multi-step edits are chained by Claude: the output of step 1 becomes the input of step 2, etc. Claude reasons about the sequence before starting.

Supported operations: `--trim`, `--cut`, `--concat`, `--speed`, `--text` (overlay), `--mute`, `--volume`, `--replace-audio`, `--fade-in`/`--fade-out`, `--resize`, `--rotate`, `--crop`, `--overlay`, `--side-by-side`, `--stack`, `--crossfade`, `--pip`.

### Trim uses stream copy, everything else re-encodes
`--trim` uses `-c copy` (fast, lossless, no quality loss). All other operations re-encode to `libx264 CRF 18` (high quality, universal compatibility). Stream copy may fail on some formats (HEVC, variable frame rate) — the script falls back to re-encode when that happens.

### 3 preview frames for verification
After every edit, Claude reads 3 JPEG frames (at 10%, 50%, 90% of the output duration) to visually confirm the result looks right. This costs ~3k image tokens vs. re-watching the full video.

### Free by default
Works with yt-dlp + ffmpeg (both free, open source). Whisper API key is only needed for videos with no native captions. Native captions cover ~95% of YouTube videos.

---

## Questions I want your opinion on

1. **One-op-per-call vs. pipeline spec**: The edit script accepts one operation at a time and Claude chains calls. An alternative would be accepting a JSON array of operations in one call. Which is better and why?

2. **Skill directory coupling**: Both `/watch` and `/edit` share the same `CLAUDE_SKILL_DIR` (the whole repo is one skill). Should `/edit` be a separate skill with its own directory, or is sharing fine?

3. **Trim stream copy asymmetry**: `--trim` is lossless/fast via stream copy; everything else re-encodes. Is this asymmetry worth it, or should all ops re-encode for consistency?

4. **3 preview frames**: Is this enough to catch bad edits? Too many? Should it be configurable?

5. **Missing operations**: What common video editing operations are obviously missing from this list that users will immediately want?

6. **Security**: User-supplied file paths are passed to ffmpeg via Python `subprocess`. The paths are resolved with `Path.expanduser().resolve()` before use. Any shell injection concerns?

7. **Format support**: ffmpeg handles virtually any input format. Is there anything about the current output strategy (inherit input extension, re-encode with libx264) that will frustrate users with unusual source formats (HEVC from iPhones, VP9, AV1)?

8. **Overall approach**: Is "Claude as a smart ffmpeg wrapper" the right design, or is there a fundamentally better architecture for this kind of tool?

---

## Repo structure

```
.
├── SKILL.md                 # /watch skill contract
├── scripts/
│   ├── watch.py             # /watch orchestrator
│   ├── edit.py              # /edit ffmpeg wrapper
│   ├── download.py          # yt-dlp wrapper
│   ├── frames.py            # ffmpeg frame extraction + fps logic
│   ├── transcribe.py        # VTT parsing + Whisper orchestration
│   ├── whisper.py           # Groq / OpenAI Whisper clients
│   ├── setup.py             # preflight checker + installer
│   └── install.sh           # one-shot Claude Code setup
├── commands/
│   ├── watch.md             # /watch slash command
│   └── edit.md              # /edit slash command
└── hooks/
    └── scripts/check-setup.sh  # SessionStart status hook
```

---

*Give me your most useful critique. What would you change, add, or remove?*

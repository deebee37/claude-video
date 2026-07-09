#!/usr/bin/env bash
# Launch the browser-based video editor (scripts/web_edit.py).
cd "$(dirname "$0")"
exec python3 scripts/web_edit.py

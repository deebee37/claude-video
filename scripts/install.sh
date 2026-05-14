#!/usr/bin/env bash
# Install the /watch and /edit skills into the current user's Claude Code environment.
# Run once after cloning: bash scripts/install.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_LINK="$HOME/.claude/skills/watch"
CMD_DIR="$HOME/.claude/commands"
SETTINGS="$HOME/.claude/settings.json"

echo "[claude-video] installing skills..."

# Symlink the skill directory
if [[ -L "$SKILL_LINK" ]]; then
  echo "  skill already linked at $SKILL_LINK"
elif [[ -e "$SKILL_LINK" ]]; then
  echo "  ERROR: $SKILL_LINK exists and is not a symlink — remove it and re-run" >&2
  exit 1
else
  mkdir -p "$(dirname "$SKILL_LINK")"
  ln -s "$REPO_DIR" "$SKILL_LINK"
  echo "  skill linked: $SKILL_LINK -> $REPO_DIR"
fi

# Install slash commands
mkdir -p "$CMD_DIR"
cp "$REPO_DIR/commands/watch.md" "$CMD_DIR/watch.md"
echo "  command installed: $CMD_DIR/watch.md"
cp "$REPO_DIR/commands/edit.md" "$CMD_DIR/edit.md"
echo "  command installed: $CMD_DIR/edit.md"

# Add SessionStart hook to settings.json if not already present
if [[ -f "$SETTINGS" ]] && grep -q "check-setup.sh" "$SETTINGS" 2>/dev/null; then
  echo "  SessionStart hook already configured"
else
  echo "  NOTE: add this hook to $SETTINGS under hooks.SessionStart:"
  echo '    {"type":"command","command":"CLAUDE_PLUGIN_ROOT='"$REPO_DIR"' bash '"$REPO_DIR"'/hooks/scripts/check-setup.sh","timeout":5}'
fi

# Run preflight
echo "[claude-video] checking dependencies..."
python3 "$REPO_DIR/scripts/setup.py"

echo "[claude-video] done. Restart Claude Code and try /watch <url> or /edit <video>."

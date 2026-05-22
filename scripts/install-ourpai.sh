#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#   PAI v5.0.0 — One-Line Bootstrap Installer
#   curl -fsSL https://ourpai.ai/install.sh | bash
#
#   What this does:
#     1. Verifies prerequisites (curl, bash, rsync, tar; offers to install bun)
#     2. Backs up existing ~/.claude/ to ~/.claude.backup-{TIMESTAMP}
#     3. Downloads the v5.0.0 release tarball from
#        github.com/danielmiessler/Personal_AI_Infrastructure (HTTPS, no auth)
#     4. Places the release content at ~/.claude/
#     5. Hands off to the bundled installer (~/.claude/install.sh)
#        which runs the wizard (DA identity, voice, Pulse setup)
#
#   Why a tarball, not git clone:
#     - HTTPS-only — works for anonymous users with no GitHub account
#     - Immune to local `git config url.<x>.insteadOf` rewrites
#     - No SSH-agent / SSH-key dependency
#     - One fewer prerequisite (git not required)
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

# ─── Pinned release ──────────────────────────────────────────────
PAI_VERSION="${PAI_VERSION:-5.0.0}"
PAI_TAG="v${PAI_VERSION}"
PAI_REPO_OWNER="danielmiessler"
PAI_REPO_NAME="Personal_AI_Infrastructure"
PAI_TARBALL_URL="https://github.com/${PAI_REPO_OWNER}/${PAI_REPO_NAME}/archive/refs/tags/${PAI_TAG}.tar.gz"
PAI_RELEASE_SUBPATH="Releases/${PAI_TAG}/.claude"
DRY_RUN="${DRY_RUN:-0}"

# ─── Colors ──────────────────────────────────────────────────────
if [ -t 1 ]; then
  BLUE='\033[38;2;59;130;246m'
  LIGHT_BLUE='\033[38;2;147;197;253m'
  NAVY='\033[38;2;30;58;138m'
  GREEN='\033[38;2;34;197;94m'
  YELLOW='\033[38;2;234;179;8m'
  RED='\033[38;2;239;68;68m'
  GRAY='\033[38;2;100;116;139m'
  STEEL='\033[38;2;51;65;85m'
  SILVER='\033[38;2;203;213;225m'
  RESET='\033[0m'
  BOLD='\033[1m'
else
  BLUE='' LIGHT_BLUE='' NAVY='' GREEN='' YELLOW='' RED='' GRAY='' STEEL='' SILVER='' RESET='' BOLD=''
fi

info()    { printf "  ${BLUE}ℹ${RESET} %b\n" "$1"; }
success() { printf "  ${GREEN}✓${RESET} %b\n" "$1"; }
warn()    { printf "  ${YELLOW}⚠${RESET} %b\n" "$1"; }
error()   { printf "  ${RED}✗${RESET} %b\n" "$1" >&2; }
step()    { printf "\n${BOLD}${LIGHT_BLUE}▸ %s${RESET}\n" "$1"; }
run()     { if [ "$DRY_RUN" = "1" ]; then echo "  [DRY-RUN] $*"; else "$@"; fi; }

# ─── Banner ──────────────────────────────────────────────────────
# Plain ASCII art — no escape sequences. `cat <<HEREDOC` does NOT interpret
# `\033` in expanded variables (only `printf` does), so any color codes here
# would print as literal "\033[..." garbage when the script is piped through
# `sh`. Keep this section ANSI-free; the helper functions (info/success/etc.)
# carry the colored output via printf below.
cat <<'BANNER'

  ============================================================

           ____    _    ___
          |  _ \  / \  |_ _|
          | |_) |/ _ \  | |
          |  __// ___ \ | |
          |_|  /_/   \_\___|

                Personal AI Infrastructure
BANNER
printf '                    v%s  bootstrap\n\n' "$PAI_VERSION"
cat <<'BANNER'
  ============================================================

BANNER

if [ "$DRY_RUN" = "1" ]; then
  warn "DRY-RUN mode — no changes will be made."
fi

# ─── Step 1: Prereqs ─────────────────────────────────────────────
step "1/5  Checking prerequisites"

OS="$(uname -s)"
case "$OS" in
  Darwin) info "Platform: macOS" ;;
  Linux)  info "Platform: Linux (partial support — Pulse menu bar is macOS-only)" ;;
  *)      error "Unsupported OS: $OS"; exit 1 ;;
esac

need() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    error "Required command not found: $cmd"
    return 1
  fi
  success "$cmd ($(command -v "$cmd"))"
}

PREREQ_FAIL=0
need curl  || PREREQ_FAIL=1
need bash  || PREREQ_FAIL=1
need rsync || PREREQ_FAIL=1
need tar   || PREREQ_FAIL=1

if [ $PREREQ_FAIL -ne 0 ]; then
  error "Install missing prerequisites and re-run."
  if [ "$OS" = "Darwin" ]; then
    info "On macOS: curl, bash, and tar ship by default; rsync ships by default."
  else
    info "On Debian/Ubuntu:  ${BOLD}sudo apt-get install curl bash rsync tar${RESET}"
  fi
  exit 1
fi

# Bun is required by the bundled installer; offer to install if absent.
if ! command -v bun >/dev/null 2>&1; then
  warn "bun not found — the bundled installer needs it."
  if [ "${PAI_AUTO_INSTALL_BUN:-1}" = "1" ] && [ -z "${CI:-}" ] && [ -t 0 ]; then
    info "Installing bun via the official install script..."
    run bash -c "curl -fsSL https://bun.sh/install | bash"
    if [ -f "$HOME/.bun/bin/bun" ]; then
      export PATH="$HOME/.bun/bin:$PATH"
      success "bun installed at $HOME/.bun/bin/bun"
    else
      error "bun install reported success but binary not found at \$HOME/.bun/bin/bun"
      exit 1
    fi
  else
    error "Install bun first:  ${BOLD}curl -fsSL https://bun.sh/install | bash${RESET}"
    exit 1
  fi
else
  success "bun ($(command -v bun))"
fi

# ─── Step 2: Backup existing ~/.claude/ ──────────────────────────
step "2/5  Backing up existing ~/.claude/ (if present)"

if [ -e "$HOME/.claude" ]; then
  TS="$(date +%Y%m%d-%H%M%S)"
  BACKUP="$HOME/.claude.backup-$TS"
  warn "Existing ~/.claude/ detected — moving to ${BOLD}$BACKUP${RESET}"
  run mv "$HOME/.claude" "$BACKUP"
  success "Backup created at $BACKUP"
  info "Restore later with:  ${BOLD}rm -rf ~/.claude && mv $BACKUP ~/.claude${RESET}"
else
  success "No existing ~/.claude/ — clean install."
fi

# ─── Step 3: Fetch v5.0.0 ────────────────────────────────────────
step "3/5  Fetching PAI ${PAI_TAG} from GitHub"

TMP_DIR="$(mktemp -d -t pai-install-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

info "Downloading ${PAI_TAG} tarball (HTTPS, no auth required)..."
info "Source: ${PAI_TARBALL_URL}"
# Stream tarball directly into tar — HTTPS-only, immune to git URL rewrites
# and SSH-agent state. No git installation required on the user's box.
if [ "$DRY_RUN" = "1" ]; then
  echo "  [DRY-RUN] curl -fsSL '$PAI_TARBALL_URL' | tar -xzf - -C '$TMP_DIR'"
else
  if ! curl -fsSL "$PAI_TARBALL_URL" | tar -xzf - -C "$TMP_DIR"; then
    error "Failed to download or extract tarball: $PAI_TARBALL_URL"
    info "Check your network connection and verify the tag exists at:"
    info "  https://github.com/${PAI_REPO_OWNER}/${PAI_REPO_NAME}/releases/tag/${PAI_TAG}"
    exit 1
  fi
fi

# GitHub tarballs extract to {repo-name}-{tag}/. Resolve the prefix dynamically
# instead of hard-coding it, so a tag-format change doesn't silently break.
if [ "$DRY_RUN" = "1" ]; then
  EXTRACTED_DIR="$TMP_DIR/${PAI_REPO_NAME}-${PAI_VERSION}"  # placeholder for dry-run
else
  EXTRACTED_DIR="$(find "$TMP_DIR" -maxdepth 1 -type d -name "${PAI_REPO_NAME}-*" | head -n 1)"
  if [ -z "$EXTRACTED_DIR" ] || [ ! -d "$EXTRACTED_DIR" ]; then
    error "Could not locate extracted tarball directory under $TMP_DIR"
    info "Expected a directory matching: ${PAI_REPO_NAME}-*"
    exit 1
  fi
fi

if [ "$DRY_RUN" != "1" ] && [ ! -d "$EXTRACTED_DIR/$PAI_RELEASE_SUBPATH" ]; then
  error "Release subpath not found in extracted tree: $PAI_RELEASE_SUBPATH"
  info "This usually means the tag $PAI_TAG was published without a Releases directory."
  exit 1
fi
success "Fetched ${PAI_TAG}"

# ─── Step 4: Place at ~/.claude/ ─────────────────────────────────
step "4/5  Installing to ~/.claude/"

run mkdir -p "$HOME/.claude"
# rsync -a preserves perms, copies hidden files, mirrors the tree exactly.
run rsync -a "$EXTRACTED_DIR/$PAI_RELEASE_SUBPATH/" "$HOME/.claude/"
success "Files placed at ~/.claude/"

if [ "$DRY_RUN" != "1" ] && [ ! -f "$HOME/.claude/install.sh" ]; then
  error "Bundled installer missing at ~/.claude/install.sh — fetch may have failed."
  exit 1
fi

# ─── Step 5: Hand off to bundled installer ───────────────────────
step "5/5  Launching the bundled installer (DA identity, voice, Pulse setup)"

if [ "$DRY_RUN" = "1" ]; then
  info "[DRY-RUN] Would now: cd ~/.claude && ./install.sh"
  info "Bootstrap complete in dry-run mode."
  exit 0
fi

cd "$HOME/.claude"
chmod +x ./install.sh
exec ./install.sh

#!/usr/bin/env bash
set -euo pipefail

REPO="clawdot/open-gateway-clawdot-skill"
BOLD='\033[1m'
RED='\033[0;31m'
GREEN='\033[0;32m'
RESET='\033[0m'

usage() {
  echo "Usage: install.sh <skill> <platform> [version]"
  echo ""
  echo "  skill     Skill name (e.g., takeout)"
  echo "  platform  Target platform (claude-code, codex, openclaw)"
  echo "  version   Release version (default: latest)"
  echo ""
  echo "Examples:"
  echo "  install.sh takeout claude-code"
  echo "  install.sh takeout claude-code v0.1.0"
  exit 1
}

die() { echo -e "${RED}Error: $1${RESET}" >&2; exit 1; }
info() { echo -e "${GREEN}$1${RESET}"; }

# --- Parse args ---
[[ $# -lt 2 ]] && usage
SKILL="$1"
PLATFORM="$2"
VERSION="${3:-latest}"

# --- Resolve release URL ---
if [[ "$VERSION" == "latest" ]]; then
  RELEASE_URL="https://github.com/${REPO}/releases/latest/download"
else
  RELEASE_URL="https://github.com/${REPO}/releases/download/${VERSION}"
fi

# --- Fetch manifest ---
info "Fetching manifest from ${VERSION} release..."
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

MANIFEST="${TMPDIR}/manifest.json"
curl -fsSL -o "$MANIFEST" "${RELEASE_URL}/manifest.json" || \
  die "Failed to fetch manifest. Is '${VERSION}' a valid release?"

# --- Parse manifest (portable, no jq dependency) ---
ASSET=$(python3 -c "
import json, sys
m = json.load(open('${MANIFEST}'))
skill = m.get('skills', {}).get('${SKILL}')
if not skill:
    avail = ', '.join(m.get('skills', {}).keys())
    print(f'SKILL_NOT_FOUND:{avail}', file=sys.stderr)
    sys.exit(1)
plat = skill.get('${PLATFORM}')
if not plat:
    avail = ', '.join(skill.keys())
    print(f'PLATFORM_NOT_FOUND:{avail}', file=sys.stderr)
    sys.exit(1)
print(plat['asset'])
print(plat['sha256'])
print(plat.get('install_dir', '.'))
" 2>"${TMPDIR}/parse_err") || {
  ERR=$(cat "${TMPDIR}/parse_err")
  if [[ "$ERR" == SKILL_NOT_FOUND:* ]]; then
    die "Unknown skill '${SKILL}'. Available: ${ERR#SKILL_NOT_FOUND:}"
  elif [[ "$ERR" == PLATFORM_NOT_FOUND:* ]]; then
    die "Skill '${SKILL}' does not support platform '${PLATFORM}'. Available: ${ERR#PLATFORM_NOT_FOUND:}"
  else
    die "Failed to parse manifest: ${ERR}"
  fi
}

# Split the three lines of output
ASSET_FILE=$(echo "$ASSET" | sed -n '1p')
EXPECTED_SHA=$(echo "$ASSET" | sed -n '2p')
INSTALL_DIR=$(echo "$ASSET" | sed -n '3p')

# --- Download archive ---
info "Downloading ${ASSET_FILE}..."
ARCHIVE="${TMPDIR}/${ASSET_FILE}"
curl -fsSL -o "$ARCHIVE" "${RELEASE_URL}/${ASSET_FILE}" || \
  die "Failed to download ${ASSET_FILE}"

# --- Verify sha256 ---
if command -v shasum >/dev/null 2>&1; then
  ACTUAL_SHA=$(shasum -a 256 "$ARCHIVE" | cut -d' ' -f1)
elif command -v sha256sum >/dev/null 2>&1; then
  ACTUAL_SHA=$(sha256sum "$ARCHIVE" | cut -d' ' -f1)
else
  die "Neither shasum nor sha256sum found. Cannot verify archive integrity."
fi

if [[ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]]; then
  die "SHA256 mismatch!\n  Expected: ${EXPECTED_SHA}\n  Got:      ${ACTUAL_SHA}\n  Archive may be corrupted or tampered with."
fi
info "SHA256 verified."

# --- Determine install directory ---
case "$PLATFORM" in
  claude-code)
    DEST="${HOME}/.claude/skills/clawdot-${SKILL}"
    ;;
  openclaw)
    DEST="${HOME}/.openclaw/skills/clawdot-${SKILL}"
    ;;
  codex)
    DEST="."
    if [[ -f "AGENTS.md" ]]; then
      echo -e "${BOLD}Warning: AGENTS.md already exists in current directory.${RESET}"
      read -p "Overwrite? [y/N] " -n 1 -r
      echo
      [[ $REPLY =~ ^[Yy]$ ]] || die "Aborted."
    fi
    ;;
  *)
    DEST="${INSTALL_DIR/#\~/$HOME}"
    ;;
esac

# Expand ~ in DEST
DEST="${DEST/#\~/$HOME}"

# --- Extract ---
mkdir -p "$DEST"
tar xzf "$ARCHIVE" -C "$DEST"
info "Installed to ${DEST}"

# --- Post-install instructions ---
echo ""
echo -e "${BOLD}Next steps:${RESET}"

ENV_EXAMPLE="${DEST}/.env.example"
if [[ -f "$ENV_EXAMPLE" ]]; then
  echo "  Configure environment variables (see ${ENV_EXAMPLE}):"
  while IFS='=' read -r key _; do
    [[ -n "$key" && ! "$key" =~ ^# && ! "$key" =~ ^DEFAULT_ ]] && echo "    export ${key}=<your-value>"
  done < "$ENV_EXAMPLE"
fi

case "$PLATFORM" in
  claude-code)
    echo "  The skill is now available in Claude Code."
    ;;
  codex)
    echo "  AGENTS.md and scripts/ have been placed in the current directory."
    echo "  Make sure to set environment variables before running Codex."
    ;;
  openclaw)
    echo "  Register the skill in your OpenClaw workspace configuration."
    ;;
esac

echo ""
info "Done!"

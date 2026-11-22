#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_SRC="$REPO_ROOT/skills"

SKILLS_DIR="${HOME}/.agents/skills"
BIN_DIR="${HOME}/.local/bin"
COPY_MODE=0
UNINSTALL=0

usage() {
  cat <<'EOF'
Usage: ./scripts/install-skills.sh [options]

Link Tentacles agent skills (tentacles-feed, tentacles-persona, tentacles-ops)
into your global agent skills directory, and optionally link a `tentacles` CLI
wrapper that defaults TENTACLES_CONFIG to this repository.

Options:
  --target DIR     Skills destination directory (default: ~/.agents/skills)
  --bin-dir DIR    CLI wrapper directory; pass "" to skip (default: ~/.local/bin)
  --copy           Copy skill directories instead of creating symlinks
  --uninstall      Remove installed skill links/directories and CLI wrapper
  -h, --help       Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      SKILLS_DIR="${2:?missing directory for --target}"
      shift 2
      ;;
    --bin-dir)
      BIN_DIR="${2-}"
      shift 2
      ;;
    --copy)
      COPY_MODE=1
      shift
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

SKILL_NAMES=()
for dir in "$SKILLS_SRC"/*/; do
  [[ -f "${dir}SKILL.md" ]] && SKILL_NAMES+=("$(basename "$dir")")
done

if [[ "$UNINSTALL" -eq 1 ]]; then
  for name in "${SKILL_NAMES[@]}"; do
    dest="$SKILLS_DIR/$name"
    if [[ -L "$dest" || -d "$dest" ]]; then
      rm -rf "$dest"
      echo "Removed skill: $dest"
    fi
  done
  if [[ -n "$BIN_DIR" && -f "$BIN_DIR/tentacles" ]] && grep -q "tentacles-cli-wrapper" "$BIN_DIR/tentacles" 2>/dev/null; then
    rm -f "$BIN_DIR/tentacles"
    echo "Removed CLI wrapper: $BIN_DIR/tentacles"
  fi
  exit 0
fi

mkdir -p "$SKILLS_DIR"
for name in "${SKILL_NAMES[@]}"; do
  src="$SKILLS_SRC/$name"
  dest="$SKILLS_DIR/$name"
  rm -rf "$dest"
  if [[ "$COPY_MODE" -eq 1 ]]; then
    cp -R "$src" "$dest"
    echo "Copied skill: $dest"
  else
    ln -s "$src" "$dest"
    echo "Linked skill: $dest -> $src"
  fi
done

if [[ -n "$BIN_DIR" && -x "$REPO_ROOT/.venv/bin/tentacles" ]]; then
  mkdir -p "$BIN_DIR"
  wrapper="$BIN_DIR/tentacles"
  if [[ -e "$wrapper" && ! -L "$wrapper" ]] && ! grep -q "tentacles-cli-wrapper" "$wrapper" 2>/dev/null; then
    echo "Skipped CLI wrapper: $wrapper already exists and is not managed by this script" >&2
  else
    rm -f "$wrapper"
    cat >"$wrapper" <<EOF
#!/usr/bin/env bash
# tentacles-cli-wrapper
export TENTACLES_CONFIG="\${TENTACLES_CONFIG:-$REPO_ROOT/config/tentacles.yaml}"
exec "$REPO_ROOT/.venv/bin/tentacles" "\$@"
EOF
    chmod +x "$wrapper"
    echo "Installed CLI wrapper: $wrapper (default TENTACLES_CONFIG=$REPO_ROOT/config/tentacles.yaml)"
    case ":${PATH}:" in
      *":$BIN_DIR:"*) ;;
      *) echo "Note: add $BIN_DIR to your PATH so agents can invoke \`tentacles\` from any directory." ;;
    esac
  fi
fi

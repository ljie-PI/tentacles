#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG_DIR="$REPO_ROOT/src/tentacles"
WEB_DST="$PKG_DIR/_web"
BUNDLED_DST="$PKG_DIR/_bundled"
OUT_DIR="$REPO_ROOT/dist"
SKIP_FRONTEND=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir)
      OUT_DIR="${2:?missing directory for --out-dir}"
      shift 2
      ;;
    --skip-frontend)
      SKIP_FRONTEND=1
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./scripts/build-wheel.sh [--out-dir DIR] [--skip-frontend]

Build frontend static assets, stage bundled templates/skills into src/tentacles/,
and build a self-contained wheel in dist/ (cleaning staged directories on exit).
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

cleanup() {
  rm -rf "$WEB_DST" "$BUNDLED_DST"
}
trap cleanup EXIT
cleanup

if [[ "$SKIP_FRONTEND" -eq 0 ]]; then
  (cd "$REPO_ROOT/frontend" && pnpm install --frozen-lockfile && pnpm build)
fi

if [[ ! -f "$REPO_ROOT/frontend/dist/index.html" ]]; then
  echo "error: frontend/dist/index.html is missing; run without --skip-frontend first" >&2
  exit 1
fi

CONFIG_SRC="$REPO_ROOT/config/tentacles.example.yaml"
if [[ ! -f "$CONFIG_SRC" ]]; then
  CONFIG_SRC="$REPO_ROOT/config/tentacles.yaml"
fi

mkdir -p "$WEB_DST" "$BUNDLED_DST/config/prompts" "$BUNDLED_DST/scripts/browser_sources" "$BUNDLED_DST/skills"
cp -R "$REPO_ROOT/frontend/dist/." "$WEB_DST/"
cp "$CONFIG_SRC" "$BUNDLED_DST/config/tentacles.yaml"
cp "$REPO_ROOT/config/prompts/"*.md "$BUNDLED_DST/config/prompts/"
cp "$REPO_ROOT/.env.example" "$BUNDLED_DST/.env.example"
cp "$REPO_ROOT/scripts/browser_sources/"*.py "$BUNDLED_DST/scripts/browser_sources/"
cp -R "$REPO_ROOT/skills/." "$BUNDLED_DST/skills/"

PYTHON_BIN="${TENTACLES_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

mkdir -p "$OUT_DIR"
if command -v uv >/dev/null 2>&1; then
  uv build --wheel --out-dir "$OUT_DIR" "$REPO_ROOT"
elif "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip wheel "$REPO_ROOT" --no-deps --wheel-dir "$OUT_DIR"
else
  "$PYTHON_BIN" -m build --wheel --outdir "$OUT_DIR" "$REPO_ROOT"
fi

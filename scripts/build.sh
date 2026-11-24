#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${TENTACLES_CONFIG:-$REPO_ROOT/config/tentacles.yaml}"
CONFIG_EXAMPLE="$REPO_ROOT/config/tentacles.example.yaml"
VENV_DIR="$REPO_ROOT/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
TENTACLES_BIN="$VENV_DIR/bin/tentacles"

SKIP_FRONTEND=0
DEV_DEPS=0
BUILD_WHEEL=0
WHEEL_ARGS=()

usage() {
  cat <<'EOF'
Usage: ./scripts/build.sh [options]

Initialize/sync .venv, install tentacles (editable), build frontend static assets
into frontend/dist, and validate config/tentacles.yaml.

Options:
  --skip-frontend   Skip `pnpm install && pnpm build`
  --dev             Install Python dev extras (`-e ".[dev]"`) and run `mypy` + `pnpm check:api`
  --wheel           Also build a self-contained release wheel in dist/ via build-wheel.sh
  --out-dir DIR     Wheel output directory when --wheel is passed (default: dist/)
  -h, --help        Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-frontend)
      SKIP_FRONTEND=1
      shift
      ;;
    --dev)
      DEV_DEPS=1
      shift
      ;;
    --wheel)
      BUILD_WHEEL=1
      shift
      ;;
    --out-dir)
      WHEEL_ARGS+=("--out-dir" "${2:?missing directory for --out-dir}")
      shift 2
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

ensure_python() {
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "==> Creating .venv..."
    if command -v uv >/dev/null 2>&1; then
      (cd "$REPO_ROOT" && uv venv .venv)
    else
      python3 -m venv "$VENV_DIR"
    fi
  fi

  local pkg_spec="."
  if [[ "$DEV_DEPS" -eq 1 ]]; then
    pkg_spec=".[dev]"
  fi

  echo "==> Installing tentacles ($pkg_spec, editable) into .venv..."
  if command -v uv >/dev/null 2>&1; then
    (cd "$REPO_ROOT" && uv pip install --python "$PYTHON_BIN" -e "$pkg_spec")
  else
    "$PYTHON_BIN" -m pip install -e "$REPO_ROOT/$pkg_spec"
  fi
}

ensure_config() {
  if [[ ! -f "$CONFIG_FILE" && "$CONFIG_FILE" == "$REPO_ROOT/config/tentacles.yaml" && -f "$CONFIG_EXAMPLE" ]]; then
    echo "==> Creating local config/tentacles.yaml from config/tentacles.example.yaml..."
    cp "$CONFIG_EXAMPLE" "$CONFIG_FILE"
  fi
}

ensure_python
ensure_config

if [[ "$SKIP_FRONTEND" -eq 0 ]]; then
  echo "==> Building frontend (frontend/dist)..."
  (
    cd "$REPO_ROOT/frontend"
    pnpm install --frozen-lockfile
    if [[ "$DEV_DEPS" -eq 1 ]]; then
      TENTACLES_PYTHON="$PYTHON_BIN" pnpm check:api
    fi
    pnpm build
  )
elif [[ ! -f "$REPO_ROOT/frontend/dist/index.html" ]]; then
  echo "warning: frontend/dist/index.html is missing; Web UI will not be served until built" >&2
fi

if [[ "$DEV_DEPS" -eq 1 ]]; then
  echo "==> Running mypy..."
  (cd "$REPO_ROOT" && "$VENV_DIR/bin/mypy" src/tentacles)
fi

if [[ -f "$CONFIG_FILE" ]]; then
  echo "==> Checking config ($CONFIG_FILE)..."
  "$TENTACLES_BIN" --config "$CONFIG_FILE" config check
fi

if [[ "$BUILD_WHEEL" -eq 1 ]]; then
  echo "==> Building self-contained wheel..."
  TENTACLES_PYTHON="$PYTHON_BIN" "$REPO_ROOT/scripts/build-wheel.sh" --skip-frontend "${WHEEL_ARGS[@]}"
fi

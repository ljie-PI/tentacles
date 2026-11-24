#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${TENTACLES_CONFIG:-$REPO_ROOT/config/tentacles.yaml}"
CONFIG_EXAMPLE="$REPO_ROOT/config/tentacles.example.yaml"
VENV_DIR="$REPO_ROOT/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
TENTACLES_BIN="$VENV_DIR/bin/tentacles"
PID_FILE="$REPO_ROOT/data/serve.pid"
LOG_FILE="$REPO_ROOT/data/serve.log"

usage() {
  cat <<'EOF'
Usage: ./scripts/serve.sh [command] [options]

Commands:
  up          Build (via scripts/build.sh) + start/restart tentacles serve (default)
  start       Start tentacles serve in background (writes data/serve.pid & data/serve.log)
  stop        Gracefully stop background tentacles serve
  restart     Restart background tentacles serve (without rebuilding)
  status      Show service PID, listen port, and /api/health status
  logs        Tail data/serve.log (Ctrl+C to exit)

Options (for up / start / restart):
  --skip-frontend   Skip `pnpm install && pnpm build` during `up`
  --foreground      Run `tentacles serve` in foreground instead of background
  --no-scheduler    Pass `--no-scheduler` to `tentacles serve`
  -h, --help        Show this help message
EOF
}

ensure_ready() {
  if [[ ! -x "$TENTACLES_BIN" ]]; then
    echo "error: $TENTACLES_BIN not found; run './scripts/build.sh' or './scripts/serve.sh up' first" >&2
    exit 1
  fi
  if [[ ! -f "$CONFIG_FILE" && "$CONFIG_FILE" == "$REPO_ROOT/config/tentacles.yaml" && -f "$CONFIG_EXAMPLE" ]]; then
    echo "==> Creating local config/tentacles.yaml from config/tentacles.example.yaml..."
    cp "$CONFIG_EXAMPLE" "$CONFIG_FILE"
  fi
}

server_url() {
  "$PYTHON_BIN" - "$CONFIG_FILE" <<'PY'
import sys
from tentacles.config import load_config
cfg = load_config(sys.argv[1])
host = "127.0.0.1" if cfg.server.host in {"0.0.0.0", "::"} else cfg.server.host
print(f"http://{host}:{cfg.server.port}")
PY
}

running_pid() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(tr -d '[:space:]' < "$PID_FILE")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      local cmd
      cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      if [[ "$cmd" == *"tentacles"* && "$cmd" == *"serve"* ]]; then
        echo "$pid"
        return 0
      fi
    fi
  fi
  return 1
}

do_stop() {
  local pid
  if ! pid="$(running_pid)"; then
    rm -f "$PID_FILE"
    echo "tentacles serve is not running."
    return 0
  fi

  echo "==> Stopping tentacles serve (PID $pid)..."
  kill -TERM "$pid" 2>/dev/null || true
  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "Stopped tentacles serve (PID $pid)."
      return 0
    fi
    sleep 1
  done

  echo "==> Process still alive after 20s; sending SIGKILL..."
  kill -KILL "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "Force-stopped tentacles serve (PID $pid)."
}

do_start() {
  local foreground="$1"
  local no_scheduler="$2"
  ensure_ready
  "$TENTACLES_BIN" --config "$CONFIG_FILE" config check

  local serve_args=(--config "$CONFIG_FILE" serve)
  if [[ "$no_scheduler" -eq 1 ]]; then
    serve_args+=(--no-scheduler)
  fi

  if [[ "$foreground" -eq 1 ]]; then
    if pid="$(running_pid)"; then
      echo "error: tentacles serve is already running in background (PID $pid). Run './scripts/serve.sh stop' first." >&2
      exit 1
    fi
    echo "==> Starting tentacles serve in foreground..."
    exec "$TENTACLES_BIN" "${serve_args[@]}"
  fi

  if pid="$(running_pid)"; then
    echo "tentacles serve is already running (PID $pid)."
    do_status
    return 0
  fi

  mkdir -p "$(dirname "$PID_FILE")" "$(dirname "$LOG_FILE")"
  echo "==> Starting tentacles serve in background..."
  nohup "$TENTACLES_BIN" "${serve_args[@]}" >> "$LOG_FILE" 2>&1 < /dev/null &
  local new_pid=$!
  echo "$new_pid" > "$PID_FILE"

  local url
  url="$(server_url)"
  for _ in {1..20}; do
    if ! kill -0 "$new_pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "error: tentacles serve exited unexpectedly. Last log lines:" >&2
      tail -n 30 "$LOG_FILE" >&2 || true
      exit 1
    fi
    if curl --noproxy '*' -fsS "$url/api/health" >/dev/null 2>&1; then
      echo "Started tentacles serve (PID $new_pid) at $url"
      echo "Logs: $LOG_FILE"
      return 0
    fi
    sleep 0.5
  done

  echo "Started tentacles serve (PID $new_pid), still warming up. Check '$LOG_FILE'."
}

do_status() {
  ensure_ready
  local url
  url="$(server_url)"
  if pid="$(running_pid)"; then
    echo "Status: running (PID $pid)"
    echo "URL:    $url"
    echo "Health: $(curl --noproxy '*' -fsS "$url/api/health" 2>/dev/null || echo 'unreachable')"
  else
    echo "Status: stopped"
    return 1
  fi
}

COMMAND="up"
if [[ $# -gt 0 && "${1#-}" == "$1" ]]; then
  COMMAND="$1"
  shift
fi

SKIP_FRONTEND=0
FOREGROUND=0
NO_SCHEDULER=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-frontend)
      SKIP_FRONTEND=1
      shift
      ;;
    --foreground)
      FOREGROUND=1
      shift
      ;;
    --no-scheduler)
      NO_SCHEDULER=1
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

case "$COMMAND" in
  up)
    BUILD_FLAGS=()
    if [[ "$SKIP_FRONTEND" -eq 1 ]]; then
      BUILD_FLAGS+=(--skip-frontend)
    fi
    "$REPO_ROOT/scripts/build.sh" "${BUILD_FLAGS[@]}"
    if [[ "$FOREGROUND" -eq 0 ]] && running_pid >/dev/null; then
      do_stop
    fi
    do_start "$FOREGROUND" "$NO_SCHEDULER"
    ;;
  start)
    do_start "$FOREGROUND" "$NO_SCHEDULER"
    ;;
  stop)
    do_stop
    ;;
  restart)
    do_stop
    do_start "$FOREGROUND" "$NO_SCHEDULER"
    ;;
  status)
    do_status
    ;;
  logs)
    exec tail -n 50 -f "$LOG_FILE"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "Unknown command: $COMMAND" >&2
    usage >&2
    exit 2
    ;;
esac

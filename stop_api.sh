#!/usr/bin/env bash
# =============================================================================
# 停止主服务（业务 API :8000 + LiveKit Docker）
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

kill_port() {
  local port="$1"
  local pids
  pids=$(ss -ltnp 2>/dev/null | grep -E ":${port}\\s" | grep -oP 'pid=\K[0-9]+' | sort -u || true)
  if [[ -z "${pids:-}" ]]; then
    pids=$(fuser "${port}/tcp" 2>/dev/null || true)
  fi
  if [[ -n "${pids:-}" ]]; then
    echo "kill :$port -> $pids"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 0.5
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  else
    echo "  :$port idle"
  fi
}

echo "== API :8000 =="
kill_port 8000
pkill -f "apps/api/main.py" 2>/dev/null || true

echo "== LiveKit =="
if [[ -x "$ROOT/deploy/livekit/stop.sh" ]]; then
  bash "$ROOT/deploy/livekit/stop.sh" || true
else
  echo "  skip (no deploy/livekit/stop.sh)"
fi

echo "API + LiveKit stopped"

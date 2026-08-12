#!/usr/bin/env bash
# =============================================================================
# 停止 ASR（:8100 / :8101）
# =============================================================================
set -euo pipefail

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
    sleep 0.3
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  else
    echo "  :$port idle"
  fi
}

for port in 8100 8101; do kill_port "$port"; done
pkill -f "asr/main.py" 2>/dev/null || true
echo "ASR stopped"

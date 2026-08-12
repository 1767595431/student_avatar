#!/usr/bin/env bash
set -euo pipefail
# scripts → api → apps → repo root
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
PORT="${PORT:-8000}"

free_port() {
  local port="$1"
  local pids
  pids=$(ss -ltnp 2>/dev/null | grep -E ":${port}\\s" | grep -oP 'pid=\K[0-9]+' | sort -u || true)
  if [[ -n "${pids:-}" ]]; then
    echo "port :$port busy → kill $pids"
    for pid in $pids; do kill "$pid" 2>/dev/null || true; done
    sleep 1
    pids=$(ss -ltnp 2>/dev/null | grep -E ":${port}\\s" | grep -oP 'pid=\K[0-9]+' | sort -u || true)
    if [[ -n "${pids:-}" ]]; then
      echo "port :$port still busy → kill -9 $pids"
      for pid in $pids; do kill -9 "$pid" 2>/dev/null || true; done
      sleep 1
    fi
  fi
}

free_port "$PORT"

source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
conda activate student_api
# 清掉交互 shell 残留的旧 TTS_*，否则会盖过代码默认 8300/8301
unset TTS_HTTP_URLS TTS_WS_URLS TTS_WS_URL MAX_TTS_ACTIVE_JOBS || true
set -a
# shellcheck disable=SC1091
[[ -f "$ROOT/.env" ]] && source "$ROOT/.env"
set +a
# Qwen 适配层默认；.env 若显式写了 TTS_HTTP_URLS 则保留
export TTS_HTTP_URLS="${TTS_HTTP_URLS:-http://127.0.0.1:8300,http://127.0.0.1:8301}"
# 生产首版：双卡 4+4=8（FLASH 已验证；可再上探 16）
export MAX_TTS_ACTIVE_JOBS="${MAX_TTS_ACTIVE_JOBS:-8}"
cd "$ROOT/apps/api"
export PYTHONPATH="$ROOT/apps/api:$ROOT/apps/publisher:${PYTHONPATH:-}"
echo "Starting API on :${PORT} tts=$TTS_HTTP_URLS max_tts=$MAX_TTS_ACTIVE_JOBS"
exec python main.py

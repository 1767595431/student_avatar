#!/usr/bin/env bash
set -euo pipefail

kill_port() {
  local port="$1"
  local pids
  pids=$(ss -lptn "sport = :$port" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u || true)
  if [[ -z "${pids:-}" ]]; then
    pids=$(fuser "${port}/tcp" 2>/dev/null || true)
  fi
  if [[ -n "${pids:-}" ]]; then
    echo "kill :$port -> $pids"
    kill $pids 2>/dev/null || true
    sleep 0.2
    kill -9 $pids 2>/dev/null || true
  fi
}

for port in $(seq 8300 8315); do kill_port "$port"; done
for port in 8091 8092; do kill_port "$port"; done

pkill -f "Qwen3-TTS-12Hz-0.6B-Base" 2>/dev/null || true
pkill -f "tts_qwen/service/main.py" 2>/dev/null || true

echo "Qwen/vLLM TTS stopped"

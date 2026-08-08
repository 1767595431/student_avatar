#!/usr/bin/env bash
# Start TTS workers across 2 GPUs, ONE BY ONE (avoid CUDA OOM during parallel warmup).
# Default: 2 per GPU → 4 concurrent jobs (ports 8200-8203).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
LOGDIR="$ROOT/data"
mkdir -p "$LOGDIR"

PER_GPU="${TTS_WORKERS_PER_GPU:-2}"
BASE_PORT="${TTS_BASE_PORT:-8200}"

echo "Launching TTS workers sequentially: ${PER_GPU}/GPU, base_port=${BASE_PORT}"

idx=0
for gpu in 0 1; do
  for ((i=0; i<PER_GPU; i++)); do
    port=$((BASE_PORT + idx))
    idx=$((idx + 1))
    log="$LOGDIR/tts_gpu${gpu}_${port}.log"
    if [[ "$gpu" == "0" ]]; then
      nohup env -u CUDA_VISIBLE_DEVICES TTS_PORT="$port" bash "$SCRIPTS/start_gpu0.sh" >"$log" 2>&1 &
    else
      nohup env -u CUDA_VISIBLE_DEVICES TTS_PORT="$port" bash "$SCRIPTS/start_gpu1.sh" >"$log" 2>&1 &
    fi
    echo "  started gpu=$gpu port=$port pid=$! log=$log"
    ok=0
    for t in $(seq 1 150); do
      if curl -s -m 2 "http://127.0.0.1:${port}/health" | grep -q '"status":"ok"'; then
        echo "  :$port ready"
        ok=1
        break
      fi
      sleep 2
    done
    [[ $ok -eq 1 ]] || { echo "  :$port FAILED"; exit 1; }
  done
done
echo "All TTS workers ready ($idx)"

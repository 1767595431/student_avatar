#!/usr/bin/env bash
# 只起双卡 vLLM-Omni（8091/8092），用于官方路径压测；不启适配层
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
LOGDIR="$ROOT/data"
mkdir -p "$LOGDIR"

echo "Stopping any existing TTS …"
bash "$SCRIPTS/stop_workers.sh" || true
unset HF_HOME HUGGINGFACE_HUB_CACHE || true

for spec in "0:8091:start_vllm_gpu0.sh" "1:8092:start_vllm_gpu1.sh"; do
  IFS=: read -r gpu port script <<<"$spec"
  log="$LOGDIR/vllm_gpu${gpu}_${port}.log"
  nohup env -u CUDA_VISIBLE_DEVICES -u HF_HOME -u HUGGINGFACE_HUB_CACHE \
    TTS_VLLM_PORT="$port" bash "$SCRIPTS/$script" >"$log" 2>&1 &
  echo "  vllm gpu=$gpu :$port pid=$! log=$log"
  ok=0
  for t in $(seq 1 360); do
    if curl -s -m 2 "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1; then
      echo "  :$port ready (${t}×2s)"
      ok=1
      break
    fi
    sleep 2
  done
  [[ $ok -eq 1 ]] || { echo "  :$port FAILED — see $log"; tail -40 "$log"; exit 1; }
done

echo "vLLM-Omni ready: :8091 + :8092 (no adapter)"
nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv

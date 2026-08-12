#!/usr/bin/env bash
# Qwen3-TTS-0.6B-Base + vLLM-Omni：GPU0/1 :8091/:8092 + 适配层 :8300/:8301
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
    if curl -s -m 2 "http://127.0.0.1:${port}/health" >/dev/null 2>&1 \
      || curl -s -m 2 "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1; then
      echo "  :$port ready"
      ok=1
      break
    fi
    sleep 2
  done
  [[ $ok -eq 1 ]] || { echo "  :$port FAILED — see $log"; exit 1; }
done

for port in 8300 8301; do
  # 一卡一路：8300→8091(GPU0)，8301→8092(GPU1)；业务 least-inflight 才等于 4+4
  if [[ "$port" == "8300" ]]; then
    vllm_urls="http://127.0.0.1:8091"
  else
    vllm_urls="http://127.0.0.1:8092"
  fi
  log="$LOGDIR/qwen_adapter_${port}.log"
  nohup env TTS_BACKEND=vllm TTS_PORT="$port" TTS_WORKER_ID="adapter-$port" \
    TTS_VLLM_URLS="$vllm_urls" \
    bash "$SCRIPTS/start_adapter.sh" >"$log" 2>&1 &
  echo "  adapter :$port -> $vllm_urls pid=$! log=$log"
  ok=0
  for t in $(seq 1 60); do
    curl -s -m 2 "http://127.0.0.1:${port}/health" | grep -q '"status":"ok"' && { ok=1; break; }
    sleep 1
  done
  [[ $ok -eq 1 ]] || { echo "  adapter :$port FAILED $log"; exit 1; }
done

echo "Qwen TTS ready: vLLM 8091/8092 + adapters 8300→8091 / 8301→8092"

#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${ASR_PORT:-8100}"

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
conda activate student_asr
cd "$ROOT"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$ROOT/models/modelscope_cache}"
mkdir -p "$MODELSCOPE_CACHE"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export ASR_DEVICE="${ASR_DEVICE:-cuda:0}"
# FunASR ≈1.5–2GB；与 GPU0 上 Qwen TTS（FLASH）共存，需 TTS 留 ≥3GB 空闲。
# 若 OOM：先降 tts_qwen/deploy/qwen3_tts.yaml 的 gpu_memory_utilization，或 ASR_CUDA=1 改挂 GPU1。
echo "Starting ASR on :${PORT} device=$ASR_DEVICE CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
exec python main.py

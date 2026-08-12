#!/usr/bin/env bash
# 薄适配层：WS/HTTP 学生协议 → vLLM /v1/audio/speech
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$ROOT/.." && pwd)"
source /home/ubuntu/miniconda3/etc/profile.d/conda.sh

# 优先 student_tts_qwen；未装时可 TTS_ADAPTER_ENV=student_tts_moss 过渡
ENV_NAME="${TTS_ADAPTER_ENV:-}"
if [[ -z "$ENV_NAME" ]]; then
  if conda env list | awk '{print $1}' | grep -qx student_tts_qwen; then
    ENV_NAME=student_tts_qwen
  elif conda env list | awk '{print $1}' | grep -qx student_tts_moss; then
    ENV_NAME=student_tts_moss
  else
    echo "缺少适配层环境：先 bash $ROOT/scripts/install_adapter.sh" >&2
    exit 1
  fi
fi
conda activate "$ENV_NAME"
cd "$ROOT/service"

export TTS_BACKEND=vllm
export TTS_PORT="${TTS_PORT:-8300}"
export TTS_WORKER_ID="${TTS_WORKER_ID:-qwen-adapter-$TTS_PORT}"
export TTS_VLLM_URLS="${TTS_VLLM_URLS:-http://127.0.0.1:8091,http://127.0.0.1:8092}"
export TTS_VOICE_ROOT="${TTS_VOICE_ROOT:-$REPO/data/voices}"
export TTS_MODEL_ID="${TTS_MODEL_ID:-}"
export HF_HOME="${TTS_HF_HOME:-$ROOT/data/hf_cache}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-$HF_HOME/hub}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export HF_HUB_DISABLE_XET=1

echo "Starting Qwen TTS adapter env=$ENV_NAME port=$TTS_PORT -> $TTS_VLLM_URLS"
exec python main.py

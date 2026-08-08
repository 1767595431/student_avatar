#!/usr/bin/env bash
# TTS Worker on physical GPU0, port 8200 (shares GPU0 with ASR)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
conda activate student_tts
cd "$ROOT"

# force physical GPU0 (ignore ambient CUDA_VISIBLE_DEVICES)
export CUDA_VISIBLE_DEVICES=0
export TTS_DEVICE=cuda:0
export TTS_PORT="${TTS_PORT:-8200}"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$ROOT/models/modelscope_cache}"
mkdir -p "$MODELSCOPE_CACHE"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

if [[ ! -f "$ROOT/voices/default_prompt.wav" && -f "$ROOT/CosyVoice/asset/zero_shot_prompt.wav" ]]; then
  cp "$ROOT/CosyVoice/asset/zero_shot_prompt.wav" "$ROOT/voices/default_prompt.wav"
fi

echo "Starting TTS worker GPU0 port=$TTS_PORT CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
exec python main.py

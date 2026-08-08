#!/usr/bin/env bash
# TTS Worker on physical GPU1, port 8201
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
conda activate student_tts
cd "$ROOT"

# force physical GPU1 (ignore ambient CUDA_VISIBLE_DEVICES)
export CUDA_VISIBLE_DEVICES=1
export TTS_DEVICE=cuda:0
export TTS_PORT="${TTS_PORT:-8201}"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$ROOT/models/modelscope_cache}"
mkdir -p "$MODELSCOPE_CACHE"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

if [[ ! -f "$ROOT/voices/default_prompt.wav" && -f "$ROOT/CosyVoice/asset/zero_shot_prompt.wav" ]]; then
  cp "$ROOT/CosyVoice/asset/zero_shot_prompt.wav" "$ROOT/voices/default_prompt.wav"
fi

echo "Starting TTS worker GPU1 port=$TTS_PORT CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
exec python main.py

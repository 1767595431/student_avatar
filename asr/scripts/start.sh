#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
conda activate student_asr
cd "$ROOT"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$ROOT/models/modelscope_cache}"
mkdir -p "$MODELSCOPE_CACHE"
export CUDA_VISIBLE_DEVICES=0
export ASR_DEVICE=cuda:0
exec python main.py

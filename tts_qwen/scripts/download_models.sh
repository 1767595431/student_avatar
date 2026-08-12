#!/usr/bin/env bash
# 预下载 Qwen3-TTS-12Hz-0.6B-Base → tts_qwen/data/hf_cache
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
conda activate "${TTS_VLLM_ENV:-student_tts_vllm}"

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HOME="${HF_HOME:-$ROOT/data/hf_cache}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-$HF_HOME/hub}"
mkdir -p "$HUGGINGFACE_HUB_CACHE"

python - <<'PY'
from huggingface_hub import snapshot_download
repo = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
print("downloading", repo, flush=True)
path = snapshot_download(repo_id=repo)
print("ok", repo, "->", path, flush=True)
print("done")
PY

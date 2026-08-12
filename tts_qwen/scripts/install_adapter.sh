#!/usr/bin/env bash
# Qwen 适配层环境（HTTP/WS → vLLM），不占 GPU
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$ROOT/.." && pwd)"
ENV_NAME="${TTS_ADAPTER_ENV:-student_tts_qwen}"

source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
if ! conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "[tts_qwen] creating conda env $ENV_NAME"
  conda create -y -n "$ENV_NAME" python=3.12
fi
conda activate "$ENV_NAME"
pip install -U pip
pip install -r "$ROOT/requirements.txt" \
  -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host=mirrors.aliyun.com

mkdir -p "$REPO/data/voices" "$ROOT/data"
echo "[tts_qwen] env=$ENV_NAME ready"
echo "[tts_qwen] start: bash $ROOT/scripts/start_workers.sh"

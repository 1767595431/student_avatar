#!/usr/bin/env bash
# 独立环境 student_tts_vllm：vLLM + vLLM-Omni（Qwen3-TTS）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_NAME="${TTS_VLLM_ENV:-student_tts_vllm}"
mkdir -p "$ROOT/data"

source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
if ! conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  conda create -y -n "$ENV_NAME" python=3.12
fi
conda activate "$ENV_NAME"
pip install -U pip uv

uv pip install "vllm==0.26.0" --torch-backend=auto
uv pip install "vllm-omni==0.26.0"
uv pip install httpx soundfile

export LD_LIBRARY_PATH="$(python - <<'PY'
import pathlib, os
p = pathlib.Path(__import__("nvidia").__path__[0]) / "cu13" / "lib"
print(f"{p}:{os.environ.get('LD_LIBRARY_PATH','')}" if p.is_dir() else os.environ.get("LD_LIBRARY_PATH",""))
PY
)"
python -c "import vllm, vllm_omni; print('ok', vllm.__version__)"

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HOME="${HF_HOME:-$ROOT/data/hf_cache}"
echo "[tts_vllm] env=$ENV_NAME ready HF_HOME=$HF_HOME"
echo "[tts_vllm] start: bash $ROOT/scripts/start_workers.sh"

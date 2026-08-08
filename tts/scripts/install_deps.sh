#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
conda activate student_tts
cd "$ROOT"

if [[ ! -d "$ROOT/CosyVoice/.git" ]]; then
  git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git "$ROOT/CosyVoice"
  (cd "$ROOT/CosyVoice" && git submodule update --init --recursive)
fi

pip install -U pip setuptools wheel
# Torch CUDA 12.1 wheels (compatible with driver 12.8)
pip install torch==2.3.1 torchaudio==2.3.1 --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host=mirrors.aliyun.com

# onnxruntime-gpu for CUDA 12 (NOT the default CUDA11 wheel)
pip install onnxruntime-gpu==1.18.0 \
  --extra-index-url https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/onnxruntime-cuda-12/pypi/simple/ \
  -i https://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host=mirrors.aliyun.com \
  --trusted-host=aiinfra.pkgs.visualstudio.com

echo "[tts] deps installed"

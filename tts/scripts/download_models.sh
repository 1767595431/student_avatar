#!/usr/bin/env bash
# Clone CosyVoice (if needed) and download Fun-CosyVoice3-0.5B-2512
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
conda activate student_tts
cd "$ROOT"

if [[ ! -d "$ROOT/CosyVoice/.git" ]]; then
  echo "[tts] Cloning CosyVoice..."
  git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git "$ROOT/CosyVoice"
  cd "$ROOT/CosyVoice"
  git submodule update --init --recursive
  cd "$ROOT"
fi

# Ensure prompt voice exists
mkdir -p "$ROOT/voices"
if [[ ! -f "$ROOT/voices/default_prompt.wav" ]]; then
  if [[ -f "$ROOT/CosyVoice/asset/zero_shot_prompt.wav" ]]; then
    cp "$ROOT/CosyVoice/asset/zero_shot_prompt.wav" "$ROOT/voices/default_prompt.wav"
  fi
fi

python - <<'PY'
from pathlib import Path
from modelscope import snapshot_download

root = Path(__file__).resolve().parent if False else Path('.')
model_dir = Path('models/Fun-CosyVoice3-0.5B')
model_dir.parent.mkdir(parents=True, exist_ok=True)
print('Downloading Fun-CosyVoice3-0.5B-2512 ->', model_dir)
snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512', local_dir=str(model_dir))
print('OK: model downloaded')
PY

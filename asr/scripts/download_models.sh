#!/usr/bin/env bash
# Download FunASR models into asr/models (also warms ModelScope cache).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
conda activate student_asr
cd "$ROOT"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$ROOT/models/modelscope_cache}"
mkdir -p "$MODELSCOPE_CACHE"
python - <<'PY'
from funasr import AutoModel
from config import settings
print("Downloading / loading:", settings.asr_model, settings.vad_model, settings.punc_model)
model = AutoModel(
    model=settings.asr_model,
    vad_model=settings.vad_model,
    vad_kwargs={"max_single_segment_time": 60000},
    punc_model=settings.punc_model,
    device=settings.device,
    disable_update=True,
)
print("OK: FunASR models ready")
PY

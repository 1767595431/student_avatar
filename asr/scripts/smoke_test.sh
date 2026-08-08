#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
conda activate student_asr
cd "$ROOT"

# Generate a short 16k mono wav with ffmpeg sine + silence overlay text via espeak if available,
# otherwise use a spoken Chinese sample downloaded from FunASR OSS.
SAMPLE="$ROOT/data/sample_zh.wav"
if [[ ! -f "$SAMPLE" ]]; then
  curl -L -o "$SAMPLE" \
    "https://isv-data.oss-cn-hangzhou.aliyuncs.com/ics/MaaS/ASR/test_audio/asr_example_zh.wav"
fi

curl -sS -X POST "http://127.0.0.1:${ASR_PORT:-8100}/internal/asr/transcribe" \
  -F "audio=@${SAMPLE}" \
  -F "session_id=sess_smoke" \
  -F "question_id=q_smoke" | python -m json.tool

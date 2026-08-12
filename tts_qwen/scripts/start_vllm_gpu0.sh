#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
conda activate "${TTS_VLLM_ENV:-student_tts_vllm}"

export CUDA_VISIBLE_DEVICES=0
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
# 勿继承 shell 里残留的 HF_HOME（须指向 tts_qwen/data/hf_cache）
export HF_HOME="${TTS_HF_HOME:-$ROOT/data/hf_cache}"
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
# shellcheck source=/dev/null
source "$SCRIPTS/_vllm_env.sh"

PORT="${TTS_VLLM_PORT:-8091}"
MODEL="${TTS_QWEN_MODEL:-}"
if [[ -z "$MODEL" ]]; then
  MODEL="$(ls -d "$HF_HOME"/hub/models--Qwen--Qwen3-TTS-12Hz-0.6B-Base/snapshots/* 2>/dev/null | head -1 || true)"
fi
[[ -n "$MODEL" && -d "$MODEL" ]] || { echo "Qwen weights missing under $HF_HOME — run download_models.sh"; exit 1; }
DEPLOY_CFG="${TTS_DEPLOY_CONFIG:-$ROOT/deploy/qwen3_tts.yaml}"
[[ -f "$DEPLOY_CFG" ]] || { echo "missing deploy config: $DEPLOY_CFG"; exit 1; }
export HF_HUB_DISABLE_XET=1
export HF_HUB_OFFLINE=1

# 驱动 CUDA 13+ 默认 FLASH_ATTN；旧驱动可 TTS_ATTENTION_BACKEND=TRITON_ATTN
ATTN_BACKEND="${TTS_ATTENTION_BACKEND:-FLASH_ATTN}"
EXTRA=()
if [[ "${TTS_NO_ASYNC_CHUNK:-0}" == "1" ]]; then
  EXTRA+=(--no-async-chunk)
fi
if [[ "${TTS_ENFORCE_EAGER:-0}" == "1" ]]; then
  EXTRA+=(--enforce-eager)
fi

echo "vLLM-Omni Qwen3-TTS GPU0 :$PORT model=$MODEL"
echo "  deploy-config=$DEPLOY_CFG attn=$ATTN_BACKEND"
exec vllm-omni serve "$MODEL" \
  --omni \
  --deploy-config "$DEPLOY_CFG" \
  "${EXTRA[@]}" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --trust-remote-code \
  --attention-backend "$ATTN_BACKEND"

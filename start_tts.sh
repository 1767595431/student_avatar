#!/usr/bin/env bash
# =============================================================================
# TTS 启动脚本（优先第 1 步）
# 顺序：TTS → ASR → 主服务（start_api.sh）
# 双卡 vLLM-Omni :8091/:8092 + 适配层 :8300/:8301
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec env -u HF_HOME -u HUGGINGFACE_HUB_CACHE \
  bash "$ROOT/tts_qwen/scripts/start_workers.sh"

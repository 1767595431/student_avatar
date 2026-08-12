#!/usr/bin/env bash
# =============================================================================
# ASR 启动脚本（优先第 2 步，须在 TTS 之后）
# 顺序：TTS → ASR → 主服务
# 每卡一个 FunASR：GPU0→:8100、GPU1→:8101（各 2 workers）
# 依赖：TTS 已占显存后仍剩 ≥3GB/卡；先起 TTS 再起本脚本
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
bash "$ROOT/stop_asr.sh" || true
exec bash "$ROOT/asr/scripts/start_dual.sh"

#!/usr/bin/env bash
# =============================================================================
# 实时跟踪 TTS 日志（Ctrl+C 退出）
# 可选参数：vllm0 | vllm1 | adapter0 | adapter1 | all(默认)
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
D="$ROOT/tts_qwen/data"
which="${1:-all}"

case "$which" in
  vllm0)    files=("$D/vllm_gpu0_8091.log") ;;
  vllm1)    files=("$D/vllm_gpu1_8092.log") ;;
  adapter0) files=("$D/qwen_adapter_8300.log") ;;
  adapter1) files=("$D/qwen_adapter_8301.log") ;;
  all)
    files=(
      "$D/vllm_gpu0_8091.log"
      "$D/vllm_gpu1_8092.log"
      "$D/qwen_adapter_8300.log"
      "$D/qwen_adapter_8301.log"
    )
    ;;
  *)
    echo "usage: $0 [all|vllm0|vllm1|adapter0|adapter1]"
    exit 1
    ;;
esac

for f in "${files[@]}"; do
  [[ -f "$f" ]] || touch "$f"
done
echo "tail -F ${files[*]}  (Ctrl+C 退出)"
exec tail -n 40 -F "${files[@]}"

#!/usr/bin/env bash
# =============================================================================
# 实时跟踪 ASR 日志（Ctrl+C 退出）
# 可选参数：0 | 1 | all(默认)
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
D="$ROOT/asr/data"
which="${1:-all}"

case "$which" in
  0) files=("$D/asr_gpu0_8100.log") ;;
  1) files=("$D/asr_gpu1_8101.log") ;;
  all)
    files=("$D/asr_gpu0_8100.log" "$D/asr_gpu1_8101.log")
    ;;
  *)
    echo "usage: $0 [all|0|1]"
    exit 1
    ;;
esac

for f in "${files[@]}"; do
  [[ -f "$f" ]] || touch "$f"
done
echo "tail -F ${files[*]}  (Ctrl+C 退出)"
exec tail -n 40 -F "${files[@]}"

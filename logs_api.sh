#!/usr/bin/env bash
# =============================================================================
# 实时跟踪主服务日志（Ctrl+C 退出）
# 可选参数：api(默认) | livekit | all
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
API_LOG="$ROOT/apps/api/data/api.log"
which="${1:-api}"

mkdir -p "$(dirname "$API_LOG")"
[[ -f "$API_LOG" ]] || touch "$API_LOG"

case "$which" in
  api)
    echo "tail -F $API_LOG  (Ctrl+C 退出)"
    exec tail -n 80 -F "$API_LOG"
    ;;
  livekit)
    echo "docker logs -f student-livekit  (Ctrl+C 退出)"
    exec docker logs -f --tail 80 student-livekit
    ;;
  all)
    echo "API: $API_LOG | LiveKit: docker student-livekit  (Ctrl+C 退出)"
    # ponytail: 无多路合并工具时用后台 + wait；Ctrl+C 杀子进程
    trap 'kill 0 2>/dev/null || true' INT TERM
    tail -n 40 -F "$API_LOG" &
    docker logs -f --tail 40 student-livekit 2>&1 &
    wait
    ;;
  *)
    echo "usage: $0 [api|livekit|all]"
    exit 1
    ;;
esac

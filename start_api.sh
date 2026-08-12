#!/usr/bin/env bash
# =============================================================================
# 主服务启动脚本（优先第 3 步，须在 TTS + ASR 之后）
# 顺序：TTS → ASR → 主服务
# LiveKit(Docker) + 业务 API :8000（Web / Publisher / 调度）
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGDIR="$ROOT/apps/api/data"
LOG="$LOGDIR/api.log"
mkdir -p "$LOGDIR"

echo "== 0) LiveKit (Docker) =="
if curl -s -m 2 http://127.0.0.1:7880/ >/dev/null 2>&1; then
  echo "  LiveKit already up"
else
  bash "$ROOT/deploy/livekit/start.sh"
  ok=0
  for _ in $(seq 1 30); do
    curl -s -m 2 http://127.0.0.1:7880/ >/dev/null 2>&1 && { ok=1; break; }
    sleep 1
  done
  [[ $ok -eq 1 ]] || { echo "LiveKit FAILED"; exit 1; }
  echo "  LiveKit ready"
fi

echo "== 1) 检查 TTS / ASR =="
for port in 8091 8092; do
  curl -s -m 3 "http://127.0.0.1:${port}/v1/models" >/dev/null \
    || { echo "missing vLLM :$port — 先: bash $ROOT/start_tts.sh"; exit 1; }
  echo "  vLLM :$port ok"
done
for port in 8300 8301; do
  curl -s -m 2 "http://127.0.0.1:${port}/health" | grep -q '"status":"ok"' \
    || { echo "missing TTS adapter :$port — 先: bash $ROOT/start_tts.sh"; exit 1; }
  echo "  adapter :$port ok"
done
for port in 8100 8101; do
  curl -s -m 2 "http://127.0.0.1:${port}/health" | grep -q '"status":"ok"' \
    || { echo "missing ASR :$port — 先: bash $ROOT/start_asr.sh"; exit 1; }
  echo "  ASR :$port ok"
done

echo "== 2) 业务 API :8000 =="
export ASR_HTTP_URLS="${ASR_HTTP_URLS:-http://127.0.0.1:8100,http://127.0.0.1:8101}"
export TTS_HTTP_URLS="${TTS_HTTP_URLS:-http://127.0.0.1:8300,http://127.0.0.1:8301}"
export MAX_TTS_ACTIVE_JOBS="${MAX_TTS_ACTIVE_JOBS:-8}"
export MAX_ASR_JOBS="${MAX_ASR_JOBS:-4}"
: >"$LOG"
nohup env \
  ASR_HTTP_URLS="$ASR_HTTP_URLS" \
  TTS_HTTP_URLS="$TTS_HTTP_URLS" \
  MAX_TTS_ACTIVE_JOBS="$MAX_TTS_ACTIVE_JOBS" \
  MAX_ASR_JOBS="$MAX_ASR_JOBS" \
  bash "$ROOT/apps/api/scripts/start.sh" >"$LOG" 2>&1 &
echo "  api pid=$! log=$LOG"

ok=0
for _ in $(seq 1 60); do
  if curl -s -m 2 "http://127.0.0.1:8000/api/v1/health" 2>/dev/null | grep -q '"status":"ok"'; then
    ok=1
    break
  fi
  sleep 1
done
if [[ $ok -ne 1 ]]; then
  echo "API FAILED — see $LOG"
  tail -40 "$LOG" || true
  exit 1
fi
curl -sS "http://127.0.0.1:8000/api/v1/health"
echo
echo "API ready :8000  (日志: bash $ROOT/logs_api.sh)"

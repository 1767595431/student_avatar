#!/usr/bin/env bash
# 语音栈：ASR(:8100) + TTS 适配层(:8300/:8301，假定 vLLM 已起) + 业务 API(:8000)
# vLLM 双卡首次：bash tts_qwen/scripts/start_vllm_only.sh（约 3–6 分钟）
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "== check vLLM =="
for port in 8091 8092; do
  curl -s -m 3 "http://127.0.0.1:${port}/v1/models" >/dev/null \
    || { echo "missing vLLM :$port — run: bash $REPO/tts_qwen/scripts/start_vllm_only.sh"; exit 1; }
  echo "  :$port ok"
done

echo "== ASR :8100 (GPU0，与 TTS0 共用剩余显存) =="
# 后台起；已在跑则先停再起
if curl -s -m 2 http://127.0.0.1:8100/health | grep -q '"status":"ok"'; then
  echo "  ASR already up"
else
  nohup bash "$REPO/asr/scripts/start.sh" >"$REPO/asr/data/asr_start.log" 2>&1 &
  ok=0
  for t in $(seq 1 120); do
    curl -s -m 2 http://127.0.0.1:8100/health | grep -q '"status":"ok"' && { ok=1; break; }
    sleep 1
  done
  [[ $ok -eq 1 ]] || { echo "ASR FAILED — $REPO/asr/data/asr_start.log"; tail -40 "$REPO/asr/data/asr_start.log"; exit 1; }
  echo "  ASR ready"
fi

echo "== TTS adapters =="
bash "$REPO/tts_qwen/scripts/start_adapters_only.sh"

echo "== API :8000 (MAX_TTS_ACTIVE_JOBS=8) =="
nohup bash "$REPO/apps/api/scripts/start.sh" >"$REPO/apps/api/data/api_start.log" 2>&1 &
ok=0
for t in $(seq 1 60); do
  curl -s -m 2 http://127.0.0.1:8000/health | grep -q '"status":"ok\|ok"' 2>/dev/null && { ok=1; break; }
  curl -s -m 2 http://127.0.0.1:8000/health >/dev/null && { ok=1; break; }
  sleep 1
done
[[ $ok -eq 1 ]] || { echo "API FAILED — $REPO/apps/api/data/api_start.log"; tail -40 "$REPO/apps/api/data/api_start.log"; exit 1; }

echo "speech stack ready: ASR:8100 + adapters:8300/8301 + API:8000 (vLLM:8091/8092)"
curl -s http://127.0.0.1:8000/health | head -c 300; echo

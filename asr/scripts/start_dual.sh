#!/usr/bin/env bash
# 双卡 ASR：GPU0→:8100、GPU1→:8101（各 max_workers=2）
set -euo pipefail
ASR_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGDIR="$ASR_ROOT/data"
mkdir -p "$LOGDIR"

start_one() {
  local gpu="$1" port="$2"
  local log="$LOGDIR/asr_gpu${gpu}_${port}.log"
  echo "== ASR GPU${gpu} :${port} =="
  nohup env \
    ASR_PORT="$port" \
    CUDA_VISIBLE_DEVICES="$gpu" \
    ASR_DEVICE=cuda:0 \
    bash "$ASR_ROOT/scripts/start.sh" >"$log" 2>&1 &
  echo "  pid=$! log=$log"
  local ok=0
  for _ in $(seq 1 180); do
    if curl -s -m 2 "http://127.0.0.1:${port}/health" | grep -q '"status":"ok"'; then
      echo "  :$port ready"
      ok=1
      break
    fi
    sleep 1
  done
  [[ $ok -eq 1 ]] || { echo "  :$port FAILED — $log"; tail -40 "$log"; exit 1; }
}

start_one 0 8100
start_one 1 8101
echo "ASR dual ready: :8100(GPU0) + :8101(GPU1)"

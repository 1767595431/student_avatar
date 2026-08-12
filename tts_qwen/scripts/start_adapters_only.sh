#!/usr/bin/env bash
# 只起/重启适配层（假定 vLLM :8091/:8092 已就绪）。8300→8091，8301→8092。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
LOGDIR="$ROOT/data"
mkdir -p "$LOGDIR"

for port in 8091 8092; do
  curl -s -m 3 "http://127.0.0.1:${port}/v1/models" >/dev/null \
    || { echo "vLLM :$port not ready — first: bash $SCRIPTS/start_vllm_only.sh"; exit 1; }
done

kill_port() {
  local port="$1"
  local pids
  pids=$(ss -lptn "sport = :$port" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u || true)
  if [[ -n "${pids:-}" ]]; then
    echo "kill :$port -> $pids"
    kill $pids 2>/dev/null || true
    sleep 0.3
    kill -9 $pids 2>/dev/null || true
  fi
}
for port in 8300 8301; do kill_port "$port"; done

for port in 8300 8301; do
  if [[ "$port" == "8300" ]]; then
    vllm_urls="http://127.0.0.1:8091"
  else
    vllm_urls="http://127.0.0.1:8092"
  fi
  log="$LOGDIR/qwen_adapter_${port}.log"
  nohup env TTS_BACKEND=vllm TTS_PORT="$port" TTS_WORKER_ID="adapter-$port" \
    TTS_VLLM_URLS="$vllm_urls" \
    bash "$SCRIPTS/start_adapter.sh" >"$log" 2>&1 &
  echo "  adapter :$port -> $vllm_urls pid=$!"
  ok=0
  for t in $(seq 1 60); do
    curl -s -m 2 "http://127.0.0.1:${port}/health" | grep -q '"status":"ok"' && { ok=1; break; }
    sleep 1
  done
  [[ $ok -eq 1 ]] || { echo "adapter :$port FAILED — $log"; tail -30 "$log"; exit 1; }
done
echo "adapters ready: 8300→8091 / 8301→8092"

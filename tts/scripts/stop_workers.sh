#!/usr/bin/env bash
# Stop TTS workers listening on 8200-8210 (best-effort).
set -euo pipefail
for port in $(seq 8200 8210); do
  pids=$(ss -ltnp 2>/dev/null | awk -v p=":$port" '$4 ~ p {print}' | grep -oP 'pid=\K[0-9]+' || true)
  for pid in $pids; do
    echo "kill TTS port=$port pid=$pid"
    kill "$pid" 2>/dev/null || true
  done
done
sleep 2
echo done

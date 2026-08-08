#!/usr/bin/env bash
# Start LiveKit SFU via Docker Compose (required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "[livekit] ERROR: docker not found. Install docker.io first (see docs/livekit-deploy.md)."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "[livekit] ERROR: cannot talk to docker daemon. Try: sudo systemctl start docker"
  exit 1
fi

echo "[livekit] Pulling image..."
docker compose pull
echo "[livekit] Starting..."
docker compose up -d
sleep 2
docker compose ps
echo "[livekit] Health:"
curl -sS -m 3 http://127.0.0.1:7880/ || true
echo
echo "[livekit] WS endpoint: ws://<server-ip>:7880"
echo "[livekit] Done."

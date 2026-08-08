#!/usr/bin/env bash
# Compat: default single-worker entry → GPU1 worker (port 8201)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec bash "$ROOT/start_gpu1.sh"

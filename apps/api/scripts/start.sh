#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
conda activate student_api
cd "$ROOT/apps/api"
export PYTHONPATH="$ROOT/apps/api:$ROOT/apps/publisher:${PYTHONPATH:-}"
exec python main.py

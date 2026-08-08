#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
conda activate student_tts
cd "$ROOT"

python - <<'PY'
import asyncio
import json
import wave
from pathlib import Path

import websockets

OUT = Path('data/smoke_tts.wav')
OUT.parent.mkdir(parents=True, exist_ok=True)

async def main():
    uri = 'ws://127.0.0.1:8200/internal/tts/stream'
    async with websockets.connect(uri, max_size=20_000_000) as ws:
        await ws.send(json.dumps({
            'type': 'start',
            'request_id': 'tts_smoke',
            'session_id': 'sess_smoke',
            'question_id': 'q_smoke',
            'voice_id': 'avatar_voice_001',
            'sample_rate': 24000,
        }))
        print(await ws.recv())
        await ws.send(json.dumps({'type': 'text', 'text': '天空之所以呈现蓝色，主要与太阳光在大气中的散射有关。'}))
        await ws.send(json.dumps({'type': 'finish'}))

        pcm = bytearray()
        sample_rate = 24000
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                pcm.extend(msg)
                continue
            data = json.loads(msg)
            print(data)
            if data.get('type') == 'audio_start':
                continue
            if data.get('type') == 'audio_end':
                break
            if data.get('type') == 'error':
                raise SystemExit(data)

    with wave.open(str(OUT), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(pcm))
    print(f'Wrote {OUT} bytes={len(pcm)}')

asyncio.run(main())
PY

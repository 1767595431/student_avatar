#!/usr/bin/env python3
"""最小自检：适配层 WS start/text/finish → 非空 PCM。失败 exit 1。"""
from __future__ import annotations

import asyncio
import json
import sys

import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8300/internal/tts/stream"
VOICE = sys.argv[2] if len(sys.argv) > 2 else "voice_8c3b06b7"
TEXT = "同学你好，我们现在开始回答这个问题。"


async def main() -> None:
    pcm = 0
    async with websockets.connect(URL, max_size=16 * 1024 * 1024) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "start",
                    "request_id": "smoke",
                    "session_id": "smoke_s",
                    "question_id": "smoke_q",
                    "voice_id": VOICE,
                }
            )
        )
        ready = json.loads(await ws.recv())
        assert ready.get("type") == "ready", ready
        await ws.send(json.dumps({"type": "text", "text": TEXT}))
        await ws.send(json.dumps({"type": "finish"}))
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=120)
            if isinstance(msg, bytes):
                pcm += len(msg)
                continue
            data = json.loads(msg)
            t = data.get("type")
            if t == "error":
                raise RuntimeError(data)
            if t == "audio_end":
                break
    assert pcm > 0, "empty pcm"
    print(f"ok pcm_bytes={pcm} url={URL}")


if __name__ == "__main__":
    asyncio.run(main())

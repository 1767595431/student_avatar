"""学生端 TTS 独立服务：HTTP 非流式整段合成 (CosyVoice3)。"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
from collections import deque
from typing import Any, Deque, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from config import settings
from engine import engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tts.main")

app = FastAPI(title="Student TTS Service", version="1.0.0")

# question_key → cancel Event（HTTP 整段合成可被 interrupt 打断）
_active_jobs: dict[str, threading.Event] = {}
_active_lock = threading.Lock()


def _job_key(session_id: str, question_id: str) -> str:
    return f"{session_id}:{question_id}"


def _physical_device() -> str:
    """Map process cuda:0 → physical GPU via CUDA_VISIBLE_DEVICES."""
    cvd = (os.environ.get("CUDA_VISIBLE_DEVICES") or "").strip()
    local = settings.device
    if not cvd:
        return local
    parts = [p.strip() for p in cvd.split(",") if p.strip() != ""]
    try:
        idx = int(str(local).split(":")[-1])
    except ValueError:
        return f"cuda:{parts[0]}" if parts else local
    if 0 <= idx < len(parts):
        return f"cuda:{parts[idx]}"
    return f"cuda:{parts[0]}"


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Warming up CosyVoice3 on device=%s ...", settings.device)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, engine.load)
    # P3优化：冷启动首包可达 10s+；启动时跑一句短合成预热 GPU
    await loop.run_in_executor(None, engine.warmup)
    logger.info("TTS service ready on %s:%s", settings.host, settings.port)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "tts",
        "device": settings.device,
        "physical_device": _physical_device(),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES") or "",
        "model_dir": str(settings.model_dir),
        "sample_rate": engine.sample_rate,
        "default_voice_id": settings.default_voice_id,
        "voices": engine.list_voices(),
        "active_jobs": len(_active_jobs),
    }


@app.post("/internal/tts/voices/reload")
async def reload_voices():
    loop = asyncio.get_running_loop()
    loaded = await loop.run_in_executor(None, engine.reload_voices_from_disk)
    return {"ok": True, "voices": loaded}


@app.post("/internal/tts/voices/{voice_id}")
async def upsert_voice(voice_id: str, payload: dict[str, Any]):
    """Register/update a voice from prompt_wav path or base64 bytes (admin calls this)."""
    import base64
    import shutil
    from pathlib import Path

    prompt_text = (payload.get("prompt_text") or settings.prompt_text).strip()
    name = (payload.get("name") or voice_id).strip()
    dest = settings.root_dir / "voices" / voice_id
    dest.mkdir(parents=True, exist_ok=True)
    wav_path = dest / "prompt.wav"

    if payload.get("prompt_wav_b64"):
        wav_path.write_bytes(base64.b64decode(payload["prompt_wav_b64"]))
    elif payload.get("prompt_wav_path"):
        src = Path(payload["prompt_wav_path"])
        if not src.exists():
            return JSONResponse(status_code=400, content={"ok": False, "message": "wav missing"})
        shutil.copy2(src, wav_path)
    elif not wav_path.exists():
        return JSONResponse(status_code=400, content={"ok": False, "message": "no wav provided"})

    meta = {
        "voice_id": voice_id,
        "name": name,
        "prompt_text": prompt_text,
        "prompt_wav": str(wav_path),
    }
    (dest / "meta.json").write_text(__import__("json").dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    engine.register_voice(voice_id, prompt_wav=wav_path, prompt_text=prompt_text)
    return {"ok": True, "voice_id": voice_id, "voices": engine.list_voices()}


@app.delete("/internal/tts/voices/{voice_id}")
async def delete_voice(voice_id: str):
    import shutil
    from pathlib import Path

    if voice_id == settings.default_voice_id:
        return JSONResponse(status_code=400, content={"ok": False, "message": "cannot delete default voice"})
    dest = settings.root_dir / "voices" / voice_id
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    engine.unregister_voice(voice_id)
    return {"ok": True, "voice_id": voice_id, "voices": engine.list_voices()}


@app.post("/internal/tts/cancel")
async def cancel_job(payload: dict[str, Any]):
    session_id = str(payload.get("session_id") or "")
    question_id = str(payload.get("question_id") or "")
    if not session_id or not question_id:
        return JSONResponse(status_code=400, content={"ok": False, "message": "session_id/question_id required"})
    key = _job_key(session_id, question_id)
    with _active_lock:
        ev = _active_jobs.get(key)
    if ev:
        ev.set()
        logger.info("TTS cancel signaled key=%s", key)
        return {"ok": True, "cancelled": True, "key": key}
    return {"ok": True, "cancelled": False, "key": key}


@app.post("/internal/tts/synthesize")
async def synthesize_once(payload: dict[str, Any]):
    """HTTP 整段合成；内部按流式 chunk 拉取以便 cancel 能尽快打断。"""
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"code": "EMPTY_TEXT", "message": "text required"})
    voice_id = payload.get("voice_id") or settings.default_voice_id
    session_id = str(payload.get("session_id") or "")
    question_id = str(payload.get("question_id") or "")
    key = _job_key(session_id, question_id) if session_id and question_id else ""
    cancel_event = threading.Event()
    if key:
        with _active_lock:
            old = _active_jobs.get(key)
            if old:
                old.set()
            _active_jobs[key] = cancel_event
    loop = asyncio.get_running_loop()

    def _run() -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        try:
            # stream=True：chunk 间隙可响应 cancel；仍汇总成整段返回
            for pcm in engine.synthesize_stream(
                text, voice_id=voice_id, stream=True, cancel_event=cancel_event
            ):
                if cancel_event.is_set():
                    break
                chunks.append(pcm)
        except Exception:
            if cancel_event.is_set():
                return b"", True
            raise
        return b"".join(chunks), cancel_event.is_set()

    try:
        pcm, cancelled = await loop.run_in_executor(None, _run)
    finally:
        if key:
            with _active_lock:
                if _active_jobs.get(key) is cancel_event:
                    _active_jobs.pop(key, None)

    return JSONResponse(
        content={
            "code": 0,
            "sample_rate": engine.sample_rate,
            "bytes": len(pcm),
            "cancelled": cancelled,
            "pcm_base64": base64.b64encode(pcm).decode("ascii") if pcm else "",
        }
    )


async def _synth_phrase(
    ws: WebSocket,
    *,
    text: str,
    voice_id: str,
    request_id: Optional[str],
    cancel_event: threading.Event,
    pending: Deque[dict],
) -> str:
    """Synthesize one phrase; concurrently accept cancel/text/finish into pending.

    Returns: "ok" | "cancelled" | "error"
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()

    def producer() -> None:
        try:
            for pcm in engine.synthesize_stream(
                text,
                voice_id=voice_id,
                stream=True,
                cancel_event=cancel_event,
            ):
                if cancel_event.is_set():
                    break
                asyncio.run_coroutine_threadsafe(queue.put(pcm), loop).result()
        except Exception as exc:  # noqa: BLE001
            logger.exception("TTS synthesize failed: %s", exc)
            asyncio.run_coroutine_threadsafe(
                queue.put(b"__ERROR__:" + str(exc).encode("utf-8")),
                loop,
            ).result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

    threading.Thread(target=producer, daemon=True).start()

    async def watch() -> None:
        while not cancel_event.is_set():
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            if msg.get("type") == "websocket.disconnect":
                cancel_event.set()
                return
            raw_msg = msg.get("text")
            if not raw_msg:
                continue
            try:
                payload = json.loads(raw_msg)
            except json.JSONDecodeError:
                continue
            t = payload.get("type")
            if t == "cancel":
                cancel_event.set()
                return
            if t in ("text", "finish", "start"):
                pending.append(payload)

    watch_task = asyncio.create_task(watch())
    status = "ok"
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            if cancel_event.is_set():
                status = "cancelled"
                break
            if isinstance(item, bytes) and item.startswith(b"__ERROR__:"):
                await ws.send_json(
                    {
                        "type": "error",
                        "code": "TTS_INFERENCE_FAILED",
                        "message": item[len(b"__ERROR__:") :].decode("utf-8", errors="ignore"),
                        "request_id": request_id,
                    }
                )
                return "error"
            await ws.send_bytes(item)
    finally:
        if not watch_task.done():
            watch_task.cancel()
            try:
                await watch_task
            except asyncio.CancelledError:
                pass
    if cancel_event.is_set():
        return "cancelled"
    return status


@app.websocket("/internal/tts/stream")
async def tts_stream(ws: WebSocket) -> None:
    await ws.accept()
    request_id: Optional[str] = None
    voice_id = settings.default_voice_id
    cancel_event = threading.Event()
    started = False
    audio_started = False
    pending: Deque[dict] = deque()
    got_text = False

    async def next_msg() -> Optional[dict]:
        if pending:
            return pending.popleft()
        message = await ws.receive()
        if message.get("type") == "websocket.disconnect":
            return None
        raw = message.get("text")
        if raw is None:
            return {"type": "_skip"}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            await ws.send_json({"type": "error", "code": "BAD_JSON", "message": "invalid json"})
            return {"type": "_skip"}

    try:
        while True:
            data = await next_msg()
            if data is None:
                break
            msg_type = data.get("type")
            if msg_type == "_skip":
                continue

            if msg_type == "start":
                request_id = data.get("request_id") or "tts_unknown"
                voice_id = data.get("voice_id") or settings.default_voice_id
                cancel_event.clear()
                started = True
                audio_started = False
                got_text = False
                pending.clear()
                logger.info(
                    "TTS start request_id=%s session=%s question=%s voice=%s",
                    request_id,
                    data.get("session_id"),
                    data.get("question_id"),
                    voice_id,
                )
                await ws.send_json(
                    {
                        "type": "ready",
                        "request_id": request_id,
                        "sample_rate": engine.sample_rate,
                    }
                )
            elif msg_type == "text":
                if not started:
                    await ws.send_json(
                        {"type": "error", "code": "NOT_STARTED", "message": "send start first"}
                    )
                    continue
                if cancel_event.is_set():
                    continue
                chunk = (data.get("text") or "").strip()
                if not chunk:
                    continue
                got_text = True
                if not audio_started:
                    audio_started = True
                    await ws.send_json({"type": "audio_start", "request_id": request_id})
                status = await _synth_phrase(
                    ws,
                    text=chunk,
                    voice_id=voice_id,
                    request_id=request_id,
                    cancel_event=cancel_event,
                    pending=pending,
                )
                if status == "cancelled":
                    await ws.send_json({"type": "cancelled", "request_id": request_id})
                    started = False
                    audio_started = False
                elif status == "error":
                    started = False
                    audio_started = False
            elif msg_type == "finish":
                if not started:
                    await ws.send_json(
                        {"type": "error", "code": "NOT_STARTED", "message": "send start first"}
                    )
                    continue
                if cancel_event.is_set():
                    await ws.send_json({"type": "cancelled", "request_id": request_id})
                    started = False
                    audio_started = False
                    continue
                if not got_text:
                    await ws.send_json(
                        {
                            "type": "error",
                            "code": "EMPTY_TEXT",
                            "message": "no text received",
                            "request_id": request_id,
                        }
                    )
                    continue
                if not audio_started:
                    # 极端情况：只有 finish、无有效 text（不应发生）
                    await ws.send_json({"type": "audio_start", "request_id": request_id})
                await ws.send_json({"type": "audio_end", "request_id": request_id})
                started = False
                audio_started = False
                got_text = False
            elif msg_type == "cancel":
                cancel_event.set()
                started = False
                audio_started = False
                got_text = False
                await ws.send_json({"type": "cancelled", "request_id": request_id})
            else:
                await ws.send_json(
                    {"type": "error", "code": "UNKNOWN_TYPE", "message": str(msg_type)}
                )
    except WebSocketDisconnect:
        cancel_event.set()
        logger.info("TTS websocket disconnected request_id=%s", request_id)
    except Exception:  # noqa: BLE001
        cancel_event.set()
        logger.exception("TTS websocket error request_id=%s", request_id)


def main() -> None:
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    main()

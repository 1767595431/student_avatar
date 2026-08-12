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
from factory import build_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("tts_qwen")

engine = build_engine()

app = FastAPI(title="student-tts-qwen", version="0.1.0")

_active_jobs: dict[str, threading.Event] = {}
_active_lock = threading.Lock()


def _job_key(session_id: str, question_id: str) -> str:
    return f"{session_id}:{question_id}"


def _register_job(session_id: str, question_id: str, cancel_event: threading.Event) -> str:
    key = _job_key(session_id, question_id) if session_id and question_id else ""
    if not key:
        return ""
    with _active_lock:
        old = _active_jobs.get(key)
        if old:
            old.set()
        _active_jobs[key] = cancel_event
    return key


def _unregister_job(key: str, cancel_event: threading.Event) -> None:
    if not key:
        return
    with _active_lock:
        if _active_jobs.get(key) is cancel_event:
            _active_jobs.pop(key, None)


@app.on_event("startup")
def _startup() -> None:
    engine.load()


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "worker_id": settings.worker_id,
        "device": settings.device,
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES") or "",
        "sample_rate": engine.sample_rate,
        "default_voice_id": settings.default_voice_id,
        "voices": engine.list_voices(),
        "active_jobs": len(_active_jobs),
        "engine": "qwen3-tts-0.6b-base",
        "backend": settings.backend,
        "model_id": settings.model_id,
        "streaming": True,
        "vllm_urls": settings.vllm_urls,
    }


@app.post("/internal/tts/voices/reload")
async def reload_voices():
    loop = asyncio.get_running_loop()
    loaded = await loop.run_in_executor(None, engine.reload_voices_from_disk)
    return {"ok": True, "voices": loaded}


@app.post("/internal/tts/voices/{voice_id}")
async def upsert_voice(voice_id: str, payload: dict[str, Any]):
    import shutil
    from pathlib import Path

    prompt_text = (payload.get("prompt_text") or "").strip()
    name = (payload.get("name") or voice_id).strip()
    dest = settings.voice_root / voice_id
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
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    engine.register_voice(voice_id, prompt_wav=wav_path, prompt_text=prompt_text, name=name)
    return {"ok": True, "voice_id": voice_id, "voices": engine.list_voices()}


@app.delete("/internal/tts/voices/{voice_id}")
async def delete_voice(voice_id: str):
    import shutil

    if voice_id == settings.default_voice_id:
        return JSONResponse(status_code=400, content={"ok": False, "message": "cannot delete default voice"})
    dest = settings.voice_root / voice_id
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
    prefix = _job_key(session_id, question_id)
    hit: list[str] = []
    with _active_lock:
        for key, ev in list(_active_jobs.items()):
            if key == prefix or key.startswith(prefix + "#"):
                ev.set()
                hit.append(key)
    if hit:
        logger.info("TTS cancel signaled keys=%s", hit)
        return {"ok": True, "cancelled": True, "keys": hit}
    return {"ok": True, "cancelled": False, "keys": []}


@app.post("/internal/tts/synthesize")
async def synthesize_once(payload: dict[str, Any]):
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"code": "EMPTY_TEXT", "message": "text required"})
    voice_id = payload.get("voice_id") or settings.default_voice_id
    session_id = str(payload.get("session_id") or "")
    question_id = str(payload.get("question_id") or "")
    cancel_event = threading.Event()
    key = _register_job(session_id, question_id, cancel_event)
    loop = asyncio.get_running_loop()

    def _run() -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        try:
            for pcm in engine.synthesize_stream(text, voice_id=voice_id, cancel_event=cancel_event):
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
        _unregister_job(key, cancel_event)

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
    """边收边发 PCM（Qwen 多请求流式；业务侧有开播缓冲）。"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    def producer() -> None:
        err: Optional[str] = None
        try:
            for pcm in engine.synthesize_stream(text, voice_id=voice_id, cancel_event=cancel_event):
                if cancel_event.is_set():
                    break
                if pcm:
                    loop.call_soon_threadsafe(q.put_nowait, pcm)
        except Exception as exc:  # noqa: BLE001
            logger.exception("TTS synthesize failed: %s", exc)
            err = str(exc)
        loop.call_soon_threadsafe(q.put_nowait, (SENTINEL, err))

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
    got_any = False
    try:
        while True:
            item = await q.get()
            if isinstance(item, tuple) and item and item[0] is SENTINEL:
                err = item[1]
                if cancel_event.is_set():
                    return "cancelled"
                if err:
                    await ws.send_json(
                        {
                            "type": "error",
                            "code": "TTS_INFERENCE_FAILED",
                            "message": err,
                            "request_id": request_id,
                        }
                    )
                    return "error"
                if not got_any:
                    await ws.send_json(
                        {
                            "type": "error",
                            "code": "TTS_EMPTY",
                            "message": "empty pcm",
                            "request_id": request_id,
                        }
                    )
                    return "error"
                return "ok"
            if cancel_event.is_set():
                return "cancelled"
            got_any = True
            await ws.send_bytes(item)
    finally:
        if not watch_task.done():
            watch_task.cancel()
            try:
                await watch_task
            except asyncio.CancelledError:
                pass


@app.websocket("/internal/tts/stream")
async def tts_stream(ws: WebSocket) -> None:
    await ws.accept()
    request_id: Optional[str] = None
    voice_id = settings.default_voice_id
    cancel_event = threading.Event()
    job_key = ""
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
                cancel_event = threading.Event()
                job_key = _register_job(
                    str(data.get("session_id") or ""),
                    str(data.get("question_id") or ""),
                    cancel_event,
                )
                started = True
                audio_started = False
                got_text = False
                pending.clear()
                logger.info(
                    "TTS stream start request_id=%s session=%s question=%s voice=%s",
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
                    await ws.send_json({"type": "audio_end", "request_id": request_id, "empty": True})
                    started = False
                    audio_started = False
                    got_text = False
                    _unregister_job(job_key, cancel_event)
                    job_key = ""
                    continue
                if not audio_started:
                    await ws.send_json({"type": "audio_start", "request_id": request_id})
                await ws.send_json({"type": "audio_end", "request_id": request_id})
                started = False
                audio_started = False
                got_text = False
                _unregister_job(job_key, cancel_event)
                job_key = ""
            elif msg_type == "cancel":
                cancel_event.set()
                started = False
                audio_started = False
                got_text = False
                await ws.send_json({"type": "cancelled", "request_id": request_id})
                _unregister_job(job_key, cancel_event)
                job_key = ""
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
    finally:
        _unregister_job(job_key, cancel_event)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=int(os.environ.get("TTS_PORT") or settings.port),
        log_level="info",
    )

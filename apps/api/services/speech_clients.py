"""Client for local ASR HTTP and TTS (batch HTTP + optional WS) services."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

import httpx
import websockets

from config import settings, tts_http_bases, tts_worker_urls

logger = logging.getLogger("api.speech")

# Least in-flight + round-robin across TTS worker bases (http://host:port)
_tts_lock = threading.Lock()
_tts_inflight: dict[str, int] = {}
_tts_rr = 0
_tts_sem: Optional[asyncio.Semaphore] = None


def _bases() -> list[str]:
    bases = tts_http_bases()
    if bases:
        return bases
    # fallback from ws
    out = []
    for w in tts_worker_urls():
        b = w.replace("ws://", "http://").replace("wss://", "https://")
        if "/internal/" in b:
            b = b.split("/internal/")[0]
        out.append(b.rstrip("/"))
    return out


def _pick_base() -> str:
    global _tts_rr
    bases = _bases()
    if not bases:
        raise RuntimeError("no TTS worker URLs configured")
    with _tts_lock:
        best = min(bases, key=lambda u: _tts_inflight.get(u, 0))
        min_v = _tts_inflight.get(best, 0)
        cands = [u for u in bases if _tts_inflight.get(u, 0) == min_v]
        uri = cands[_tts_rr % len(cands)]
        _tts_rr += 1
        _tts_inflight[uri] = _tts_inflight.get(uri, 0) + 1
        return uri


def _release_base(uri: str) -> None:
    with _tts_lock:
        _tts_inflight[uri] = max(0, _tts_inflight.get(uri, 0) - 1)


def tts_inflight_snapshot() -> dict[str, int]:
    with _tts_lock:
        return dict(_tts_inflight)


def _sem() -> asyncio.Semaphore:
    global _tts_sem
    if _tts_sem is None:
        _tts_sem = asyncio.Semaphore(max(1, int(settings.max_tts_active_jobs)))
    return _tts_sem


async def asr_transcribe(
    audio_path: Path,
    session_id: str,
    question_id: str,
) -> dict:
    url = f"{settings.asr_url.rstrip('/')}/internal/asr/transcribe"
    async with httpx.AsyncClient(timeout=60.0) as client:
        with audio_path.open("rb") as f:
            files = {"audio": (audio_path.name, f, "application/octet-stream")}
            data = {
                "session_id": session_id,
                "question_id": question_id,
                "language": "zh",
            }
            resp = await client.post(url, data=data, files=files)
            resp.raise_for_status()
            return resp.json()


# session:question → worker base（打断时打到正确进程）
_tts_job_base: dict[str, str] = {}


def _job_key(session_id: str, question_id: str) -> str:
    return f"{session_id}:{question_id}"


async def tts_cancel(*, session_id: str, question_id: str) -> None:
    """Ask the assigned TTS worker to abort in-flight synthesize for this question."""
    key = _job_key(session_id, question_id)
    bases: list[str] = []
    with _tts_lock:
        b = _tts_job_base.get(key)
        if b:
            bases.append(b)
    if not bases:
        bases = _bases()
    payload = {"session_id": session_id, "question_id": question_id}
    async with httpx.AsyncClient(timeout=3.0) as client:
        for base in bases:
            try:
                await client.post(f"{base}/internal/tts/cancel", json=payload)
            except Exception as exc:  # noqa: BLE001
                logger.debug("tts cancel %s: %s", base, exc)


async def tts_synthesize_full(
    text: str,
    *,
    session_id: str,
    question_id: str,
    voice_id: str,
    cancel_event: Optional[asyncio.Event] = None,
) -> bytes:
    """Non-stream batch TTS via HTTP. One worker job = one full utterance."""
    text = (text or "").strip()
    if not text:
        return b""
    if cancel_event and cancel_event.is_set():
        return b""

    async with _sem():
        if cancel_event and cancel_event.is_set():
            return b""
        base = _pick_base()
        key = _job_key(session_id, question_id)
        with _tts_lock:
            _tts_job_base[key] = base
        logger.info(
            "TTS batch assign session=%s question=%s base=%s chars=%s",
            session_id,
            question_id,
            base,
            len(text),
        )
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
                post_task = asyncio.create_task(
                    client.post(
                        f"{base}/internal/tts/synthesize",
                        json={
                            "text": text,
                            "voice_id": voice_id,
                            "session_id": session_id,
                            "question_id": question_id,
                        },
                    )
                )

                async def _watch_cancel() -> None:
                    while not post_task.done():
                        if cancel_event and cancel_event.is_set():
                            try:
                                await client.post(
                                    f"{base}/internal/tts/cancel",
                                    json={"session_id": session_id, "question_id": question_id},
                                )
                            except Exception:  # noqa: BLE001
                                pass
                            return
                        await asyncio.sleep(0.05)

                watch = asyncio.create_task(_watch_cancel())
                try:
                    resp = await post_task
                finally:
                    watch.cancel()
                resp.raise_for_status()
                data = resp.json()
            if cancel_event and cancel_event.is_set():
                return b""
            if data.get("cancelled"):
                return b""
            b64 = data.get("pcm_base64") or ""
            return base64.b64decode(b64) if b64 else b""
        finally:
            with _tts_lock:
                _tts_job_base.pop(key, None)
            _release_base(base)


async def tts_stream_synthesize(
    text_chunks: AsyncIterator[str],
    *,
    session_id: str,
    question_id: str,
    voice_id: str,
    on_audio_start: Optional[Callable[[], None]] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> AsyncIterator[bytes]:
    """Legacy WS streaming path (kept for benches). Prefer tts_synthesize_full."""
    request_id = f"tts_{uuid.uuid4().hex[:12]}"
    base = _pick_base()
    uri = base.replace("http://", "ws://").replace("https://", "wss://") + "/internal/tts/stream"
    logger.info("TTS ws assign session=%s question=%s uri=%s", session_id, question_id, uri)
    try:
        async with websockets.connect(uri, max_size=20_000_000) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "start",
                        "request_id": request_id,
                        "session_id": session_id,
                        "question_id": question_id,
                        "voice_id": voice_id,
                        "sample_rate": settings.tts_sample_rate,
                    }
                )
            )
            ready = json.loads(await ws.recv())
            if ready.get("type") != "ready":
                logger.warning("unexpected TTS ready: %s", ready)

            cancel_sent = False

            async def send_cancel() -> None:
                nonlocal cancel_sent
                if cancel_sent:
                    return
                cancel_sent = True
                try:
                    await ws.send(json.dumps({"type": "cancel"}))
                except Exception:  # noqa: BLE001
                    logger.debug("TTS cancel send failed", exc_info=True)

            async def sender() -> None:
                async for chunk in text_chunks:
                    if cancel_event and cancel_event.is_set():
                        await send_cancel()
                        return
                    if chunk:
                        await ws.send(json.dumps({"type": "text", "text": chunk}))
                if cancel_event and cancel_event.is_set():
                    await send_cancel()
                else:
                    await ws.send(json.dumps({"type": "finish"}))

            async def cancel_watch() -> None:
                if not cancel_event:
                    return
                await cancel_event.wait()
                await send_cancel()

            send_task = asyncio.create_task(sender())
            watch_task = asyncio.create_task(cancel_watch())
            started = False
            try:
                while True:
                    if cancel_event and cancel_event.is_set():
                        await send_cancel()
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.05)
                    except asyncio.TimeoutError:
                        if cancel_event and cancel_event.is_set() and cancel_sent:
                            try:
                                msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                            except asyncio.TimeoutError:
                                break
                        else:
                            continue
                    if isinstance(msg, bytes):
                        if cancel_event and cancel_event.is_set():
                            continue
                        if not started:
                            started = True
                            if on_audio_start:
                                on_audio_start()
                        yield msg
                        continue
                    data = json.loads(msg)
                    t = data.get("type")
                    if t == "audio_start":
                        continue
                    if t in ("audio_end", "cancelled"):
                        break
                    if t == "error":
                        raise RuntimeError(data.get("message") or "tts error")
            finally:
                if not send_task.done():
                    send_task.cancel()
                    try:
                        await send_task
                    except asyncio.CancelledError:
                        pass
                if not watch_task.done():
                    watch_task.cancel()
                    try:
                        await watch_task
                    except asyncio.CancelledError:
                        pass
    finally:
        _release_base(base)

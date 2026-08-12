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

from config import settings, asr_http_bases, tts_http_bases, tts_worker_urls
from services.concurrency import meter

logger = logging.getLogger("api.speech")

# Least in-flight + round-robin across TTS adapter bases (8300→GPU0, 8301→GPU1)
_tts_lock = threading.Lock()
_tts_inflight: dict[str, int] = {}
_tts_rr = 0

# ASR：8100→GPU0 / 8101→GPU1，同样 least-inflight
_asr_lock = threading.Lock()
_asr_inflight: dict[str, int] = {}
_asr_rr = 0

# ponytail: 全局槽 + 首句(P0)优先；有 P0 排队时预留 tts_p0_reserved_slots
_slot_cond: Optional[asyncio.Condition] = None
_slot_active = 0
_slot_p0_waiting = 0


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


def _pick_asr_base() -> str:
    global _asr_rr
    bases = asr_http_bases()
    if not bases:
        raise RuntimeError("no ASR URLs configured")
    with _asr_lock:
        best = min(bases, key=lambda u: _asr_inflight.get(u, 0))
        min_v = _asr_inflight.get(best, 0)
        cands = [u for u in bases if _asr_inflight.get(u, 0) == min_v]
        uri = cands[_asr_rr % len(cands)]
        _asr_rr += 1
        _asr_inflight[uri] = _asr_inflight.get(uri, 0) + 1
        return uri


def _release_asr_base(uri: str) -> None:
    with _asr_lock:
        _asr_inflight[uri] = max(0, _asr_inflight.get(uri, 0) - 1)


def asr_inflight_snapshot() -> dict[str, int]:
    with _asr_lock:
        return dict(_asr_inflight)


def _slot_limits() -> tuple[int, int]:
    mx = max(1, int(settings.max_tts_active_jobs))
    reserved = max(0, min(mx - 1, int(getattr(settings, "tts_p0_reserved_slots", 2))))
    return mx, reserved


async def _slot_acquire(priority: int = 1) -> None:
    """priority=0：首句；有 P0 等待时普通句不能吃光预留槽。"""
    global _slot_cond, _slot_active, _slot_p0_waiting
    if _slot_cond is None:
        _slot_cond = asyncio.Condition()
    is_p0 = priority <= 0
    async with _slot_cond:
        if is_p0:
            _slot_p0_waiting += 1
        try:
            while True:
                mx, reserved = _slot_limits()
                free = mx - _slot_active
                if free <= 0:
                    await _slot_cond.wait()
                    continue
                # 非首句：若有 P0 在等，至少留 reserved 给它们
                if (not is_p0) and _slot_p0_waiting > 0 and free <= reserved:
                    await _slot_cond.wait()
                    continue
                _slot_active += 1
                return
        finally:
            if is_p0:
                _slot_p0_waiting = max(0, _slot_p0_waiting - 1)


async def _slot_release() -> None:
    global _slot_active
    if _slot_cond is None:
        return
    async with _slot_cond:
        _slot_active = max(0, _slot_active - 1)
        _slot_cond.notify_all()


async def asr_transcribe(
    audio_path: Path,
    session_id: str,
    question_id: str,
) -> dict:
    base = _pick_asr_base()
    url = f"{base}/internal/asr/transcribe"
    try:
        with meter.track_asr():
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
    finally:
        _release_asr_base(base)


# session:question → 可能多路并行分句打在多个 worker 上
_tts_job_bases: dict[str, set[str]] = {}


def _job_key(session_id: str, question_id: str) -> str:
    return f"{session_id}:{question_id}"


def _bind_job_base(key: str, base: str) -> None:
    with _tts_lock:
        _tts_job_bases.setdefault(key, set()).add(base)


def _unbind_job_base(key: str, base: str) -> None:
    with _tts_lock:
        s = _tts_job_bases.get(key)
        if not s:
            return
        s.discard(base)
        if not s:
            _tts_job_bases.pop(key, None)


async def tts_cancel(*, session_id: str, question_id: str) -> None:
    """Ask assigned TTS worker(s) to abort in-flight synthesize for this question."""
    key = _job_key(session_id, question_id)
    with _tts_lock:
        bound = set(_tts_job_bases.get(key) or ())
    # 分句可能散落多卡；打断时打全部 worker（cancel 按 question_id 前缀）
    bases = list(bound | set(_bases()))
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
    priority: int = 1,
) -> bytes:
    """Non-stream batch TTS via HTTP. One worker job = one full utterance.

    priority=0：首句，可抢占预留槽（见 tts_p0_reserved_slots）。
    """
    text = (text or "").strip()
    if not text:
        return b""
    if cancel_event and cancel_event.is_set():
        return b""

    await _slot_acquire(priority)
    try:
        if cancel_event and cancel_event.is_set():
            return b""
        with meter.track_tts():
            return await _tts_synthesize_full_inner(
                text,
                session_id=session_id,
                question_id=question_id,
                voice_id=voice_id,
                cancel_event=cancel_event,
            )
    finally:
        await _slot_release()


async def _tts_synthesize_full_inner(
    text: str,
    *,
    session_id: str,
    question_id: str,
    voice_id: str,
    cancel_event: Optional[asyncio.Event] = None,
) -> bytes:
    base = _pick_base()
    key = _job_key(session_id, question_id)
    _bind_job_base(key, base)
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
        _unbind_job_base(key, base)
        _release_base(base)


async def tts_synthesize_phrases_ordered(
    phrases: AsyncIterator[str],
    *,
    session_id: str,
    question_id: str,
    voice_id: str,
    cancel_event: Optional[asyncio.Event] = None,
) -> AsyncIterator[bytes]:
    """分句非流式并行合成，按文本顺序 yield PCM（会话内有序推流）。

    双适配层 least-inflight（8300/8301 各绑一卡）；句 0 为 P0 优先占槽。
    """
    n_workers = max(1, len(_bases()))
    # 同会话并行度≈适配层数（通常 2），避免一会话占满全局 8 槽
    window = max(1, min(n_workers, int(settings.max_tts_active_jobs)))
    next_seq = 0
    done: dict[int, bytes] = {}
    inflight: dict[int, asyncio.Task[tuple[int, bytes]]] = {}
    seq = 0
    agen = phrases.__aiter__()
    input_done = False

    async def _synth(i: int, text: str) -> tuple[int, bytes]:
        # #pN：同会话多句并行互不 cancel；打断时 worker 按 question_id 前缀取消
        # 首句 P0，其余普通优先级
        pcm = await tts_synthesize_full(
            text,
            session_id=session_id,
            question_id=f"{question_id}#p{i}",
            voice_id=voice_id,
            cancel_event=cancel_event,
            priority=0 if i == 0 else 1,
        )
        return i, pcm

    def _fill_from_done() -> list[bytes]:
        nonlocal next_seq
        out: list[bytes] = []
        while next_seq in done:
            pcm = done.pop(next_seq)
            next_seq += 1
            if pcm:
                out.append(pcm)
        return out

    while True:
        if cancel_event and cancel_event.is_set():
            for t in inflight.values():
                t.cancel()
            break

        while not input_done and len(inflight) < window:
            try:
                phrase = await agen.__anext__()
            except StopAsyncIteration:
                input_done = True
                break
            text = (phrase or "").strip()
            if not text:
                continue
            inflight[seq] = asyncio.create_task(_synth(seq, text))
            logger.info(
                "TTS phrase queued session=%s question=%s seq=%s chars=%s inflight=%s",
                session_id,
                question_id,
                seq,
                len(text),
                len(inflight),
            )
            seq += 1

        if not inflight:
            if input_done:
                break
            await asyncio.sleep(0.01)
            continue

        finished, _ = await asyncio.wait(
            set(inflight.values()), return_when=asyncio.FIRST_COMPLETED
        )
        for t in finished:
            try:
                i, pcm = t.result()
            except asyncio.CancelledError:
                continue
            except Exception:
                logger.exception(
                    "TTS phrase failed session=%s question=%s", session_id, question_id
                )
                raise
            inflight.pop(i, None)
            done[i] = pcm
            logger.info(
                "TTS phrase ready session=%s question=%s seq=%s bytes=%s",
                session_id,
                question_id,
                i,
                len(pcm),
            )
            for pcm_out in _fill_from_done():
                yield pcm_out

    for pcm_out in _fill_from_done():
        yield pcm_out


async def tts_stream_synthesize(
    text_chunks: AsyncIterator[str],
    *,
    session_id: str,
    question_id: str,
    voice_id: str,
    on_audio_start: Optional[Callable[[], None]] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> AsyncIterator[bytes]:
    """遗留 WS 流式路径；业务主路径用 tts_synthesize_phrases_ordered。"""
    request_id = f"tts_{uuid.uuid4().hex[:12]}"
    await _slot_acquire(0)
    try:
        if cancel_event and cancel_event.is_set():
            return
        meter.enter_tts()
        base = _pick_base()
        key = _job_key(session_id, question_id)
        _bind_job_base(key, base)
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
                    try:
                        async with httpx.AsyncClient(timeout=3.0) as client:
                            await client.post(
                                f"{base}/internal/tts/cancel",
                                json={"session_id": session_id, "question_id": question_id},
                            )
                    except Exception:  # noqa: BLE001
                        pass

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
            _unbind_job_base(key, base)
            _release_base(base)
            meter.leave_tts()
    finally:
        await _slot_release()

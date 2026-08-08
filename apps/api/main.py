"""学生端业务 API — P1 单路闭环编排。"""
from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from livekit import api as livekit_api
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
PUB_DIR = ROOT / "apps" / "publisher"
API_DIR = Path(__file__).resolve().parent
for p in (str(API_DIR), str(PUB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from config import settings, tts_http_bases, tts_worker_urls  # noqa: E402
from services.dify_adapter import dify_adapter  # noqa: E402
from services.session_store import BusinessState, MediaState, store  # noqa: E402
from services.speech_clients import asr_transcribe, tts_cancel, tts_synthesize_full  # noqa: E402
from services import asset_store  # noqa: E402
from publisher import publisher_pool  # noqa: E402
from routers_admin import router as admin_router  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("api.main")

app = FastAPI(title="Student Business API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(admin_router)

TEMP = ROOT / "data" / "temp_audio"
TEMP.mkdir(parents=True, exist_ok=True)
WEB_DIR = ROOT / "apps" / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


class CreateSessionBody(BaseModel):
    student_id: str
    avatar_id: str | None = None
    avatar_version_id: str | None = None
    voice_id: str | None = None
    agent_id: str | None = None
    class_id: str = ""
    course_id: str = ""


def _avatar_package_dir(avatar_id: str, version_id: str) -> Path:
    return settings.avatar_root / avatar_id / version_id


def _session_voice_id(sess) -> str:  # noqa: ANN001
    if sess.voice_id:
        return sess.voice_id
    _, _, default_voice, _ = asset_store.defaults()
    pkg = _avatar_package_dir(sess.avatar_id, sess.avatar_version_id)
    man = pkg / "manifest.json"
    if man.exists():
        try:
            vid = __import__("json").loads(man.read_text(encoding="utf-8")).get("voice_id")
            if vid:
                return vid
        except Exception:  # noqa: BLE001
            pass
    return default_voice or settings.default_voice_id


def _session_agent(sess) -> dict:  # noqa: ANN001
    aid = sess.agent_id or ""
    if not aid:
        _, _, _, dag = asset_store.defaults()
        aid = dag
    if not aid:
        return {
            "agent_id": "",
            "name": settings.dify_agent_name or "默认",
            "api_key": settings.dify_api_key,
            "base_url": settings.dify_base_url,
        }
    meta = asset_store.get_agent(aid)
    if not meta:
        raise HTTPException(400, f"agent not found: {aid}")
    return meta


def _subscriber_token(session_id: str, room_name: str, student_id: str) -> str:
    grant = livekit_api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=False,
        can_subscribe=True,
    )
    token = (
        livekit_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(f"student_{student_id}")
        .with_name(student_id)
        .with_grants(grant)
    )
    return token.to_jwt()


@app.on_event("startup")
async def on_startup() -> None:
    try:
        info = await dify_adapter.refresh_info()
        asset_store.ensure_env_agent(
            settings.dify_api_key,
            settings.dify_base_url,
            name=info.get("name") or settings.dify_agent_name or "聊天机器人",
            mode=info.get("mode") or "",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dify bootstrap failed: %s", exc)
        asset_store.ensure_env_agent(
            settings.dify_api_key,
            settings.dify_base_url,
            name=settings.dify_agent_name or "默认智能体",
        )
    asyncio.create_task(_media_reaper())
    logger.info("Business API ready on %s:%s", settings.host, settings.port)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await dify_adapter.aclose()
    for s in store.all():
        await publisher_pool.release(s.session_id)


@app.get("/health")
@app.get("/api/v1/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "business-api",
        "dify": settings.dify_base_url,
        "dify_agent": dify_adapter.agent_name or settings.dify_agent_name or None,
        "asr": settings.asr_url,
        "tts": tts_http_bases() or tts_worker_urls(),
        "tts_mode": "batch_http",
        "livekit": settings.livekit_url,
        "max_tts_active_jobs": settings.max_tts_active_jobs,
        "media_idle_timeout_s": settings.media_idle_timeout_s,
    }


@app.get("/")
async def index():
    index_path = WEB_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "student api", "web": "missing"}


@app.get("/concurrent")
async def concurrent_page():
    path = WEB_DIR / "concurrent.html"
    if path.exists():
        return FileResponse(path)
    raise HTTPException(404, "concurrent.html missing")


@app.get("/admin")
async def admin_page():
    path = WEB_DIR / "admin.html"
    if path.exists():
        return FileResponse(path)
    raise HTTPException(404, "admin.html missing")


@app.get("/api/v1/avatars/{avatar_id}/versions/{version_id}/idle.png")
async def idle_png(avatar_id: str, version_id: str):
    pkg = _avatar_package_dir(avatar_id, version_id)
    idle = pkg / "idle.png"
    if not idle.exists():
        # try frames
        frames = pkg / "frames"
        if frames.exists():
            candidates = sorted(frames.glob("frame_*.png"))
            if candidates:
                return FileResponse(candidates[0])
        raise HTTPException(404, "idle frame not found")
    return FileResponse(idle)


@app.get("/api/v1/avatars/{avatar_id}/versions/{version_id}/frames/{frame_idx}.png")
async def avatar_frame_png(avatar_id: str, version_id: str, frame_idx: int):
    pkg = _avatar_package_dir(avatar_id, version_id)
    path = pkg / "frames" / f"frame_{frame_idx:05d}.png"
    if not path.exists():
        raise HTTPException(404, "frame not found")
    return FileResponse(path)


@app.post("/api/v1/sessions")
async def create_session(body: CreateSessionBody):
    da, dv, dvoice, dag = asset_store.defaults()
    avatar_id = body.avatar_id or da or settings.default_avatar_id
    version_id = body.avatar_version_id or dv or settings.default_avatar_version_id
    voice_id = (body.voice_id or "").strip() or dvoice or settings.default_voice_id
    agent_id = (body.agent_id or "").strip() or dag
    if not agent_id:
        raise HTTPException(400, "agent_id required（请先在管理端添加智能体）")
    if not asset_store.get_agent(agent_id):
        raise HTTPException(400, f"agent not found: {agent_id}")
    pkg = _avatar_package_dir(avatar_id, version_id)
    man = pkg / "manifest.json"
    if not man.exists():
        raise HTTPException(400, f"avatar package not ready: {pkg}")
    status = __import__("json").loads(man.read_text(encoding="utf-8")).get("status")
    if status not in (None, "ready", "published"):
        raise HTTPException(400, f"avatar not ready: status={status}")
    sess = store.create(
        student_id=body.student_id,
        avatar_id=avatar_id,
        avatar_version_id=version_id,
        voice_id=voice_id,
        agent_id=agent_id,
        class_id=body.class_id,
        course_id=body.course_id,
    )
    token = _subscriber_token(sess.session_id, sess.room_name, sess.student_id)
    agent = asset_store.get_agent(agent_id) or {}
    return {
        "session_id": sess.session_id,
        "avatar_id": sess.avatar_id,
        "avatar_version_id": sess.avatar_version_id,
        "voice_id": sess.voice_id,
        "agent_id": sess.agent_id,
        "agent_name": agent.get("name") or "",
        "livekit_url": settings.livekit_url,
        "livekit_token": token,
        "room_name": sess.room_name,
        "state": sess.state.value,
        "media_state": sess.media_state.value,
        "idle_image_url": f"/api/v1/avatars/{avatar_id}/versions/{version_id}/idle.png",
    }


@app.get("/api/v1/sessions/{session_id}")
async def get_session(session_id: str):
    sess = store.get(session_id)
    if not sess:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    return {
        "session_id": sess.session_id,
        "state": sess.state.value,
        "media_state": sess.media_state.value,
        "current_question_id": sess.current_question_id,
        "recognized_text": sess.recognized_text,
        "avatar_version_id": sess.avatar_version_id,
        "generation": sess.generation,
    }


@app.post("/api/v1/sessions/{session_id}/media/ensure")
async def ensure_media(session_id: str):
    sess = store.get(session_id)
    if not sess or sess.state == BusinessState.CLOSED:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    sess.media_state = MediaState.CONNECTING
    sess.touch()
    pkg = _avatar_package_dir(sess.avatar_id, sess.avatar_version_id)
    try:
        pub = await publisher_pool.ensure(
            session_id=sess.session_id,
            avatar_package_dir=pkg,
            room_name=sess.room_name,
            livekit_url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
            sample_rate=settings.tts_sample_rate,
        )
    except Exception:
        # 失败不得卡在 CONNECTING（否则 reaper/submit 都绕不开）
        if store.get(session_id) is sess and sess.media_state == MediaState.CONNECTING:
            sess.media_state = MediaState.CLOSED
        raise
    # 关页并发：ensure 完成后 session 可能已删 —— 释放孤儿 publisher
    if store.get(session_id) is not sess:
        await publisher_pool.release(session_id)
        raise HTTPException(404, "SESSION_NOT_FOUND")
    # READY 即暖待机，与答完后的 WARM_IDLE 一样可被 idle reaper 回收
    sess.media_state = MediaState.WARM_IDLE
    sess.touch()
    token = _subscriber_token(sess.session_id, sess.room_name, sess.student_id)
    return {
        "ok": True,
        "media_state": sess.media_state.value,
        "livekit_url": settings.livekit_url,
        "livekit_token": token,
        "room_name": sess.room_name,
        "publisher_metrics": {
            "video_track_recreate_count": pub.controller.video_track_recreate_count,
            "video_pts_discontinuity_count": pub.controller.video_pts_discontinuity_count,
        },
    }


@app.post("/api/v1/sessions/{session_id}/questions")
async def submit_question(
    session_id: str,
    audio: UploadFile = File(...),
):
    sess = store.get(session_id)
    if not sess or sess.state == BusinessState.CLOSED:
        raise HTTPException(404, "SESSION_NOT_FOUND")

    question_id = f"q_{uuid.uuid4().hex[:12]}"
    # 新提问前先打断旧回答（防旧 PCM / 旧 pipeline 污染）
    old_qid = sess.current_question_id
    old_state = sess.state
    if sess.cancel_event and not sess.cancel_event.is_set():
        sess.cancel_event.set()
    # 含 RECOGNIZING：连点提问时旧 pipeline 可能尚未进入 THINKING
    if old_qid and old_state in (
        BusinessState.RECOGNIZING,
        BusinessState.THINKING,
        BusinessState.SPEAKING,
        BusinessState.INTERRUPTING,
    ):
        await tts_cancel(session_id=session_id, question_id=old_qid)
        await dify_adapter.cancel(
            old_qid,
            session_id=session_id,
            api_key=(asset_store.get_agent(sess.agent_id) or {}).get("api_key") or "",
            base_url=(asset_store.get_agent(sess.agent_id) or {}).get("base_url") or "",
        )
        pub = publisher_pool.get(session_id)
        if pub:
            sess.generation = pub.bump_generation()
            pub.clear_pcm()
            pub.stop_speaking()

    sess.current_question_id = question_id
    sess.state = BusinessState.RECOGNIZING
    sess.touch()
    cancel_event = asyncio.Event()
    sess.cancel_event = cancel_event

    # ensure media（CLOSED/RELEASING/CONNECTING 半失败都要拉起来）
    if sess.media_state in (MediaState.CLOSED, MediaState.RELEASING, MediaState.CONNECTING):
        await ensure_media(session_id)

    suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
    raw_path = TEMP / f"{question_id}{suffix}"
    raw_path.write_bytes(await audio.read())

    try:
        asr = await asr_transcribe(raw_path, session_id, question_id)
    except Exception as exc:  # noqa: BLE001
        sess.state = BusinessState.IDLE
        raise HTTPException(500, f"ASR_INFERENCE_FAILED: {exc}") from exc
    finally:
        raw_path.unlink(missing_ok=True)

    text = (asr.get("text") or "").strip()
    sess.recognized_text = text
    sess.state = BusinessState.THINKING
    sess.touch()

    # kick async pipeline: Dify → 整段 TTS → publisher（捕获本轮 cancel_event，避免被换掉）
    asyncio.create_task(_run_answer_pipeline(session_id, question_id, text, cancel_event))

    return {
        "question_id": question_id,
        "recognized_text": text,
        "state": sess.state.value,
        "asr_request_id": asr.get("request_id"),
        "processing_ms": asr.get("processing_ms"),
    }


async def _run_answer_pipeline(
    session_id: str,
    question_id: str,
    text: str,
    cancel_event: asyncio.Event,
) -> None:
    sess = store.get(session_id)
    if not sess:
        return
    pub = publisher_pool.get(session_id)
    if not pub:
        try:
            await ensure_media(session_id)
            pub = publisher_pool.get(session_id)
        except Exception:  # noqa: BLE001
            logger.exception("media ensure failed")
            sess.state = BusinessState.IDLE
            return
    assert pub is not None

    generation = pub.bump_generation()
    sess.generation = generation
    context = {"conversation_id": sess.conversation_id}

    try:
        agent = _session_agent(sess)
        parts: list[str] = []
        async for d in dify_adapter.stream_answer(
            session_id,
            question_id,
            text,
            context,
            cancel_event=cancel_event,
            api_key=agent.get("api_key") or "",
            base_url=agent.get("base_url") or "",
            agent_name=agent.get("name") or "",
        ):
            if cancel_event.is_set():
                break
            if d:
                parts.append(d)
        if context.get("conversation_id"):
            sess.conversation_id = context["conversation_id"]

        answer = "".join(parts).strip()
        if cancel_event.is_set() or pub.generation != generation:
            return
        if not answer:
            logger.warning("empty dify answer session=%s", session_id)
            return

        pcm = await tts_synthesize_full(
            answer,
            session_id=session_id,
            question_id=question_id,
            voice_id=_session_voice_id(sess),
            cancel_event=cancel_event,
        )
        if cancel_event.is_set() or pub.generation != generation or not pcm:
            return

        sess.state = BusinessState.SPEAKING
        sess.media_state = MediaState.SPEAKING
        sess.touch()
        pub.start_speaking()

        # 按帧粒度推入播放队列（约 40ms @24k mono int16）
        step = max(2, int(settings.tts_sample_rate * 0.04) * 2)
        for i in range(0, len(pcm), step):
            if cancel_event.is_set() or pub.generation != generation:
                break
            pub.push_pcm(pcm[i : i + step], generation=generation)
            sess.touch()
            # 轻微让出，避免一次性塞爆队列卡住事件循环
            if i // step % 25 == 0:
                await asyncio.sleep(0)

        if not cancel_event.is_set() and pub.generation == generation:
            for _ in range(80):
                if cancel_event.is_set() or pub.generation != generation:
                    break
                with pub._pcm_lock:
                    empty = not pub._pcm_queue
                if empty:
                    break
                await asyncio.sleep(0.04)
    except Exception:  # noqa: BLE001
        logger.exception("answer pipeline failed session=%s", session_id)
    finally:
        # 被新问题 / interrupt 顶掉时，不得清掉新 generation 的 PCM / 状态
        if pub.generation != generation:
            logger.info(
                "pipeline superseded session=%s gen=%s current=%s",
                session_id,
                generation,
                pub.generation,
            )
            return
        pub.stop_speaking()
        pub.clear_pcm()
        if sess.state != BusinessState.CLOSED:
            sess.state = BusinessState.IDLE
            sess.media_state = MediaState.WARM_IDLE
            sess.touch()


@app.post("/api/v1/sessions/{session_id}/interrupt")
async def interrupt(session_id: str):
    """P2 打断：cancel Dify/TTS → bump generation 丢弃旧 PCM → 回 idle。"""
    sess = store.get(session_id)
    if not sess:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    if sess.state in (BusinessState.CLOSED, BusinessState.IDLE, BusinessState.INIT):
        return {
            "ok": True,
            "state": sess.state.value,
            "generation": sess.generation,
            "noop": True,
        }

    sess.state = BusinessState.INTERRUPTING
    sess.touch()
    qid = sess.current_question_id

    # 1) 标记取消，打断 pipeline / TTS 客户端 + Worker 内合成
    if sess.cancel_event:
        sess.cancel_event.set()
    if qid:
        await tts_cancel(session_id=session_id, question_id=qid)

    # 2) 取消 Dify（user 必须与流式请求一致）
    if qid:
        ag = asset_store.get_agent(sess.agent_id) or {}
        await dify_adapter.cancel(
            qid,
            session_id=session_id,
            api_key=ag.get("api_key") or "",
            base_url=ag.get("base_url") or "",
        )

    # 3) generation+1 并清空 PCM，拒绝迟到音频
    pub = publisher_pool.get(session_id)
    new_gen = sess.generation
    if pub:
        new_gen = pub.bump_generation()
        pub.clear_pcm()
        pub.stop_speaking()
    sess.generation = new_gen

    # 4) 回 idle（Avatar 经 crossfade）
    sess.state = BusinessState.IDLE
    sess.media_state = MediaState.WARM_IDLE
    sess.touch()
    logger.info(
        "interrupt session=%s question=%s generation=%s",
        session_id,
        qid,
        new_gen,
    )
    return {
        "ok": True,
        "state": sess.state.value,
        "generation": new_gen,
        "interrupted_question_id": qid,
    }


@app.delete("/api/v1/sessions/{session_id}")
async def close_session(session_id: str):
    sess = store.get(session_id)
    if not sess:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    if sess.cancel_event:
        sess.cancel_event.set()
    await publisher_pool.release(session_id)
    sess.state = BusinessState.CLOSED
    sess.media_state = MediaState.CLOSED
    store.delete(session_id)
    return {"ok": True}


async def _media_reaper() -> None:
    """回收暖待机媒体：WARM_IDLE / 遗留 READY；忙碌态与刚活动过的不收。

    release 与 ensure 共用 per-session 锁；持锁后二次校验，避免误杀刚重建的 Publisher。
    """
    reclaimable = (MediaState.WARM_IDLE, MediaState.READY)
    busy = (
        BusinessState.RECOGNIZING,
        BusinessState.THINKING,
        BusinessState.SPEAKING,
        BusinessState.INTERRUPTING,
    )

    def _idle_ok(sess, now: float) -> bool:  # noqa: ANN001
        if sess.media_state not in reclaimable:
            return False
        if sess.state in busy:
            return False
        return now - sess.last_media_activity_at >= settings.media_idle_timeout_s

    while True:
        await asyncio.sleep(5)
        now = time_now()
        for sess in store.all():
            if not _idle_ok(sess, now):
                continue

            def _gate(s=sess) -> bool:
                if not _idle_ok(s, time_now()):
                    return False
                s.media_state = MediaState.RELEASING
                return True

            released = await publisher_pool.reap_if(sess.session_id, _gate)
            # ensure 可能在等锁并已把状态改成 CONNECTING/WARM_IDLE —— 勿覆盖
            if released and sess.media_state == MediaState.RELEASING:
                sess.media_state = MediaState.CLOSED
                logger.info(
                    "Media reaped session=%s idle_s=%.0f",
                    sess.session_id,
                    settings.media_idle_timeout_s,
                )


def time_now() -> float:
    import time

    return time.time()


def main() -> None:
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()

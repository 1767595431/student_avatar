"""学生端业务 API — P1 单路闭环编排。"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import uuid
from contextlib import asynccontextmanager
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

from config import asr_http_bases, settings, tts_http_bases, tts_worker_urls  # noqa: E402
from services.dify_adapter import dify_adapter  # noqa: E402
from services.monitor_probe import probe_services  # noqa: E402
from services.session_store import BusinessState, MediaState, store  # noqa: E402
from services.speech_clients import (  # noqa: E402
    asr_inflight_snapshot,
    asr_transcribe,
    tts_cancel,
    tts_inflight_snapshot,
    tts_synthesize_phrases_ordered,
)
from services.chunker import TextChunker  # noqa: E402
from services import asset_store  # noqa: E402
from services.concurrency import meter  # noqa: E402
from publisher import publisher_pool  # noqa: E402
from routers_admin import router as admin_router  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("api.main")

TEMP = ROOT / "data" / "temp_audio"
TEMP.mkdir(parents=True, exist_ok=True)
WEB_DIR = ROOT / "apps" / "web"


class CreateSessionBody(BaseModel):
    student_id: str
    avatar_id: str | None = None
    avatar_version_id: str | None = None
    voice_id: str | None = None
    agent_id: str | None = None
    class_id: str = ""
    course_id: str = ""

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "student_id": "stu_001",
                    "avatar_id": None,
                    "avatar_version_id": None,
                    "voice_id": None,
                    "agent_id": None,
                }
            ]
        }
    }


OPENAPI_TAGS = [
    {"name": "健康检查", "description": "服务存活与配置摘要"},
    {"name": "会话", "description": "学生端问答会话：建连、提问、打断、关闭"},
    {"name": "总控", "description": "后台监听：并发与后端连通状态"},
    {"name": "选项", "description": "学生端/并发页可选形象、音模、智能体"},
    {"name": "形象管理", "description": "数字人形象上传、默认、改名、删除与资源"},
    {"name": "音模管理", "description": "TTS 克隆音模上传、试听、默认、删除"},
    {"name": "智能体管理", "description": "Dify App Key 登记与默认智能体"},
]


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
    return default_voice or ""


def _session_agent(sess) -> dict:  # noqa: ANN001
    aid = sess.agent_id or ""
    if not aid:
        _, _, _, dag = asset_store.defaults()
        aid = dag
    if not aid:
        raise HTTPException(400, "agent_id required（请先在管理端添加智能体）")
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


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    # 智能体只来自管理端添加，不从 .env 种子
    n_agents = len(asset_store.list_agents())
    reaper_tasks = [
        asyncio.create_task(_media_reaper()),
        asyncio.create_task(_session_orphan_reaper()),
    ]
    logger.info(
        "Business API ready on %s:%s agents=%s orphan_timeout_s=%s",
        settings.host,
        settings.port,
        n_agents,
        settings.session_orphan_timeout_s,
    )
    try:
        yield
    finally:
        for t in reaper_tasks:
            t.cancel()
        for t in reaper_tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        await dify_adapter.aclose()
        for s in store.all():
            await publisher_pool.release(s.session_id)


app = FastAPI(
    title="实时数字人 · 业务 API",
    version="1.0.0",
    description=(
        "课堂数字人业务接口：浏览器录音 → ASR → Dify → TTS → LiveKit 推流。\n\n"
        "中文接口说明书：仓库 `docs/api.md`。"
    ),
    lifespan=lifespan,
    openapi_tags=OPENAPI_TAGS,
    docs_url="/docs",
    redoc_url="/redoc",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(admin_router)
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/health", include_in_schema=False)
@app.get("/api/v1/health", summary="健康检查", tags=["健康检查"])
async def health() -> dict:
    return {
        "status": "ok",
        "service": "business-api",
        "dify_base_url": settings.dify_base_url,
        "agents": len(asset_store.list_agents()),
        "asr": asr_http_bases(),
        "tts": tts_http_bases() or tts_worker_urls(),
        "tts_mode": "batch_http",
        "livekit": settings.livekit_url,
        "max_asr_jobs": settings.max_asr_jobs,
        "max_tts_active_jobs": settings.max_tts_active_jobs,
        "media_idle_timeout_s": settings.media_idle_timeout_s,
    }


@app.get("/", include_in_schema=False)
async def index():
    index_path = WEB_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "student api", "web": "missing"}


@app.get("/concurrent", include_in_schema=False)
async def concurrent_page():
    path = WEB_DIR / "concurrent.html"
    if path.exists():
        return FileResponse(path)
    raise HTTPException(404, "concurrent.html missing")


@app.get("/admin", include_in_schema=False)
async def admin_page():
    path = WEB_DIR / "admin.html"
    if path.exists():
        return FileResponse(path)
    raise HTTPException(404, "admin.html missing")


@app.get("/monitor", include_in_schema=False)
async def monitor_page():
    path = WEB_DIR / "monitor.html"
    if path.exists():
        return FileResponse(path)
    raise HTTPException(404, "monitor.html missing")


@app.get("/api/v1/monitor/stats", summary="总控统计与服务连通", tags=["总控"])
async def monitor_stats() -> dict:
    """总控：会话占用 + ASR / Dify / TTS 并发 + 各服务连通。"""
    sessions = store.all()
    used = len(sessions)
    cap = max(1, int(settings.max_sessions))
    by_state: dict[str, int] = {}
    for s in sessions:
        k = s.state.value if hasattr(s.state, "value") else str(s.state)
        by_state[k] = by_state.get(k, 0) + 1
    m = meter.snapshot()
    tts_by_base = tts_inflight_snapshot()
    services = await probe_services()
    up = sum(1 for s in services if s.get("ok"))
    return {
        "ts": time_now(),
        "sessions": {
            "used": used,
            "max": cap,
            "remaining": max(0, cap - used),
            "total_capacity": cap,
            "by_state": by_state,
        },
        "asr": {
            "active": m["asr_active"],
            "max": int(settings.max_asr_jobs),
            "remaining": max(0, int(settings.max_asr_jobs) - m["asr_active"]),
            "peak": m["asr_peak"],
            "capped": True,
            "by_base": asr_inflight_snapshot(),
            "note": "双卡 ASR×2 workers；API 不二次硬拒",
        },
        "dify": {
            "active": m["dify_active"],
            "peak": m["dify_peak"],
            "capped": False,
            "note": "仅观测；真实限额在 Dify/通义侧",
        },
        "tts": {
            "active": m["tts_active"],
            "max": int(settings.max_tts_active_jobs),
            "remaining": max(0, int(settings.max_tts_active_jobs) - m["tts_active"]),
            "peak": m["tts_peak"],
            "capped": True,
            "by_base": tts_by_base,
            "note": "业务槽位 MAX_TTS_ACTIVE_JOBS（默认双卡 8）",
        },
        "publishers": {
            "active": publisher_pool.count(),
            "note": "LiveKit 推流中的 Publisher 数",
        },
        "services": {
            "up": up,
            "total": len(services),
            "items": services,
        },
    }


@app.get(
    "/api/v1/avatars/{avatar_id}/versions/{version_id}/idle.png",
    summary="形象待机图",
    tags=["形象管理"],
)
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


@app.get(
    "/api/v1/avatars/{avatar_id}/versions/{version_id}/idle.mp4",
    summary="形象待机视频",
    tags=["形象管理"],
)
async def idle_mp4(avatar_id: str, version_id: str):
    """待机循环：仅 idle→talk_start 闭嘴片段，不含说话口型。说话仍走 LiveKit。"""
    from avatar_preprocess import ensure_idle_mp4  # noqa: WPS433

    pkg = _avatar_package_dir(avatar_id, version_id)
    try:
        path = await asyncio.to_thread(ensure_idle_mp4, pkg)
    except Exception as exc:
        raise HTTPException(404, f"idle video unavailable: {exc}") from exc
    return FileResponse(path, media_type="video/mp4")


@app.get(
    "/api/v1/avatars/{avatar_id}/versions/{version_id}/frames/{frame_idx}.png",
    summary="形象帧图片",
    tags=["形象管理"],
)
async def avatar_frame_png(avatar_id: str, version_id: str, frame_idx: int):
    pkg = _avatar_package_dir(avatar_id, version_id)
    path = pkg / "frames" / f"frame_{frame_idx:05d}.png"
    if not path.exists():
        raise HTTPException(404, "frame not found")
    return FileResponse(path)


@app.post("/api/v1/sessions", summary="创建会话", tags=["会话"])
async def create_session(body: CreateSessionBody):
    da, dv, dvoice, dag = asset_store.defaults()
    avatar_id = (body.avatar_id or da or "").strip()
    version_id = (body.avatar_version_id or dv or "").strip()
    voice_id = (body.voice_id or "").strip() or dvoice
    agent_id = (body.agent_id or "").strip() or dag
    if not avatar_id or not version_id:
        raise HTTPException(400, "avatar required（请先在管理端上传形象并设默认）")
    if not voice_id:
        raise HTTPException(400, "voice_id required（请先在管理端注册音模并设默认）")
    if not agent_id:
        raise HTTPException(400, "agent_id required（请先在管理端添加智能体）")
    if not asset_store.get_agent(agent_id):
        raise HTTPException(400, f"agent not found: {agent_id}")
    cap = max(1, int(settings.max_sessions))
    if store.count() >= cap:
        raise HTTPException(429, f"会话已满（{cap}/{cap}），请稍后重试或关闭空闲会话")
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
        "idle_video_url": f"/api/v1/avatars/{avatar_id}/versions/{version_id}/idle.mp4",
    }


@app.get("/api/v1/sessions/{session_id}", summary="查询会话状态", tags=["会话"])
async def get_session(session_id: str):
    sess = store.get(session_id)
    if not sess:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    # 轮询即心跳：只刷新 updated_at，不延长 media idle（Publisher 仍可被回收）
    sess.updated_at = time_now()
    return {
        "session_id": sess.session_id,
        "state": sess.state.value,
        "media_state": sess.media_state.value,
        "current_question_id": sess.current_question_id,
        "recognized_text": sess.recognized_text,
        "avatar_version_id": sess.avatar_version_id,
        "generation": sess.generation,
        "pipeline_stage": sess.pipeline_stage,
        "last_error": sess.last_error,
        "qa_to_speak_ms": sess.qa_to_speak_ms,
        "turn_started_at": sess.turn_started_at,
        "speak_started_at": sess.speak_started_at,
    }


@app.post("/api/v1/sessions/{session_id}/media/ensure", summary="确保媒体推流", tags=["会话"])
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


@app.post("/api/v1/sessions/{session_id}/questions", summary="提交语音问题", tags=["会话"])
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
    sess.turn_started_at = time_now()
    sess.speak_started_at = 0.0
    sess.qa_to_speak_ms = 0
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
    sess.pipeline_stage = "dify"
    sess.last_error = ""
    sess.touch()

    # kick async pipeline: Dify 流式分句 → TTS 流式 → 边推 PCM
    asyncio.create_task(_run_answer_pipeline(session_id, question_id, text, cancel_event))

    return {
        "question_id": question_id,
        "recognized_text": text,
        "state": sess.state.value,
        "asr_request_id": asr.get("request_id"),
        "processing_ms": asr.get("processing_ms"),
    }


def _plain_for_tts(text: str, *, max_chars: int = 0) -> str:
    """课堂口播：去 markdown。流式分句默认不截断；max_chars>0 时截断。"""
    s = (text or "").strip()
    s = re.sub(r"```[\s\S]*?```", " ", s)
    s = re.sub(r"[#>*`]+", " ", s)
    s = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", s)
    s = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    if max_chars <= 0 or len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    for sep in ("。", "！", "？", "；", ".", "!", "?"):
        i = cut.rfind(sep)
        if i >= max_chars // 2:
            return cut[: i + 1]
    return cut + "…"


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
            sess.pipeline_stage = ""
            sess.last_error = "媒体启动失败"
            return
    assert pub is not None

    generation = pub.bump_generation()
    sess.generation = generation
    context = {"conversation_id": sess.conversation_id}
    pcm_total = 0
    speaking_started = False

    try:
        agent = _session_agent(sess)
        sess.pipeline_stage = "dify"
        sess.touch()
        chunker = TextChunker()
        voice_id = _session_voice_id(sess)
        step = max(2, int(settings.tts_sample_rate * 0.04) * 2)

        dify_chars = 0
        # ponytail: Dify 与 TTS 解耦。TTS WS 提前 audio_end/断连会 cancel sender，
        # 若直接挂在同一 async for 链上会把尚未出首 token 的 Dify 一起掐死（表现为 200ms 级 empty）。
        phrase_q: asyncio.Queue[str | None] = asyncio.Queue()

        async def dify_deltas():
            nonlocal dify_chars
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
                sess.touch()
                if d:
                    dify_chars += len(d)
                    yield d
            if context.get("conversation_id"):
                sess.conversation_id = context["conversation_id"]

        async def _produce_phrases() -> None:
            first = True
            hold = ""
            # 首句尽快出（P0 占槽）；后续略长利于音色稳定与双卡并行
            try:
                async for phrase in chunker.chunk_stream(dify_deltas()):
                    if cancel_event.is_set():
                        break
                    clean = _plain_for_tts(phrase)
                    if not clean:
                        continue
                    hold = f"{hold}{clean}" if hold else clean
                    strong_end = hold.endswith(("。", "！", "？", "；", ".", "!", "?", ";"))
                    min_send = 14 if first else 24
                    if len(hold) < min_send and not strong_end:
                        continue
                    if first:
                        sess.pipeline_stage = "tts"
                        sess.touch()
                        first = False
                    await phrase_q.put(hold.strip())
                    hold = ""
                if hold.strip() and not cancel_event.is_set():
                    if first:
                        sess.pipeline_stage = "tts"
                        sess.touch()
                    await phrase_q.put(hold.strip())
            finally:
                await phrase_q.put(None)

        async def phrases():
            while True:
                item = await phrase_q.get()
                if item is None:
                    break
                yield item

        def _on_audio_start() -> None:
            nonlocal speaking_started, pub, generation
            if speaking_started or cancel_event.is_set():
                return
            live = publisher_pool.get(session_id) or pub
            if live is not pub:
                pub = live
                generation = pub.bump_generation()
                sess.generation = generation
                logger.info("publisher refreshed before speak session=%s gen=%s", session_id, generation)
            elif pub.generation != generation:
                logger.info(
                    "skip speak superseded session=%s gen=%s current=%s",
                    session_id,
                    generation,
                    pub.generation,
                )
                return
            speaking_started = True
            sess.state = BusinessState.SPEAKING
            sess.media_state = MediaState.SPEAKING
            sess.pipeline_stage = "speaking"
            sess.speak_started_at = time_now()
            if sess.turn_started_at > 0:
                sess.qa_to_speak_ms = max(
                    0, int(round((sess.speak_started_at - sess.turn_started_at) * 1000))
                )
            sess.touch()
            pub.start_speaking()
            logger.info(
                "speaking start session=%s question=%s gen=%s qa_to_speak_ms=%s (ordered-batch)",
                session_id,
                question_id,
                generation,
                sess.qa_to_speak_ms,
            )

        async def _keepalive() -> None:
            while not cancel_event.is_set():
                sess.touch()
                await asyncio.sleep(5)

        ka = asyncio.create_task(_keepalive())
        produce_task = asyncio.create_task(_produce_phrases())
        # 分句非流式：首句整段 PCM 到齐即开播；后续句并行合成、按序推
        try:
            async for pcm in tts_synthesize_phrases_ordered(
                phrases(),
                session_id=session_id,
                question_id=question_id,
                voice_id=voice_id,
                cancel_event=cancel_event,
            ):
                if cancel_event.is_set() or pub.generation != generation:
                    break
                if not pcm:
                    continue
                pcm_total += len(pcm)
                if not speaking_started:
                    sess.pipeline_stage = "tts"
                    sess.touch()
                    _on_audio_start()
                for i in range(0, len(pcm), step):
                    if cancel_event.is_set() or pub.generation != generation:
                        break
                    pub.push_pcm(pcm[i : i + step], generation=generation)
                sess.touch()
                await asyncio.sleep(0)
        finally:
            ka.cancel()
            try:
                await ka
            except asyncio.CancelledError:
                pass
            if cancel_event.is_set() and not produce_task.done():
                produce_task.cancel()
            try:
                await produce_task
            except asyncio.CancelledError:
                pass

        if cancel_event.is_set() or pub.generation != generation:
            return
        if not speaking_started or pcm_total <= 0:
            logger.warning(
                "empty batch answer session=%s pcm=%s dify_chars=%s speaking=%s",
                session_id,
                pcm_total,
                dify_chars,
                speaking_started,
            )
            if dify_chars <= 0:
                sess.last_error = "智能体无有效回答"
            else:
                sess.last_error = "语音合成失败"
            return

        bytes_per_s = max(1, settings.tts_sample_rate * 2)
        max_wait_s = min(120.0, pcm_total / bytes_per_s + 2.0)
        waited = 0.0
        while waited < max_wait_s:
            if cancel_event.is_set() or pub.generation != generation:
                break
            with pub._pcm_lock:
                empty = not pub._pcm_queue
            if empty:
                # 队列空后再留一点给 LiveKit/播放端 jitter，避免尾句被掐
                await asyncio.sleep(0.5)
                break
            await asyncio.sleep(0.04)
            waited += 0.04
            if int(waited * 25) % 25 == 0:
                sess.touch()
    except Exception as exc:  # noqa: BLE001
        logger.exception("answer pipeline failed session=%s", session_id)
        sess.last_error = f"回答失败: {exc}"
    finally:
        live = publisher_pool.get(session_id) or pub
        if live.generation != generation:
            logger.info(
                "pipeline superseded session=%s gen=%s current=%s",
                session_id,
                generation,
                live.generation,
            )
            if sess.state == BusinessState.THINKING:
                sess.state = BusinessState.IDLE
                sess.pipeline_stage = ""
                sess.touch()
            return
        live.stop_speaking()
        live.clear_pcm()
        if sess.state != BusinessState.CLOSED:
            sess.state = BusinessState.IDLE
            sess.media_state = MediaState.WARM_IDLE
            sess.pipeline_stage = ""
            sess.touch()


@app.post("/api/v1/sessions/{session_id}/interrupt", summary="打断回答", tags=["会话"])
async def interrupt(session_id: str):
    """打断：取消 Dify/TTS → bump generation 丢弃旧 PCM → 回待机。"""
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


async def _force_close_session(session_id: str, *, reason: str) -> bool:
    """幂等：取消进行中任务、释放 Publisher、从 store 删除。返回是否刚关掉一个会话。"""
    sess = store.get(session_id)
    if not sess:
        await publisher_pool.release(session_id)
        return False
    if sess.cancel_event:
        sess.cancel_event.set()
    qid = sess.current_question_id
    if qid:
        try:
            await tts_cancel(session_id=session_id, question_id=qid)
        except Exception:
            logger.exception("tts_cancel on close session=%s", session_id)
        try:
            ag = asset_store.get_agent(sess.agent_id) or {}
            await dify_adapter.cancel(
                qid,
                session_id=session_id,
                api_key=ag.get("api_key") or "",
                base_url=ag.get("base_url") or "",
            )
        except Exception:
            logger.exception("dify_cancel on close session=%s", session_id)
    await publisher_pool.release(session_id)
    sess.state = BusinessState.CLOSED
    sess.media_state = MediaState.CLOSED
    store.delete(session_id)
    logger.info("session closed session=%s reason=%s", session_id, reason)
    return True


@app.delete("/api/v1/sessions/{session_id}", summary="结束会话", tags=["会话"])
async def close_session(session_id: str):
    await _force_close_session(session_id, reason="client_delete")
    return {"ok": True}


@app.post("/api/v1/sessions/{session_id}/close", summary="结束会话（POST）", tags=["会话"])
async def close_session_post(session_id: str):
    """unload 用 sendBeacon（只能 POST）时走这里，语义同 DELETE。"""
    await _force_close_session(session_id, reason="client_beacon")
    return {"ok": True}


async def _session_orphan_reaper() -> None:
    """关页/刷新漏删时：无客户端心跳则整会话销毁，避免并发下僵尸占坑。"""
    busy = (
        BusinessState.RECOGNIZING,
        BusinessState.THINKING,
        BusinessState.SPEAKING,
        BusinessState.INTERRUPTING,
    )
    while True:
        await asyncio.sleep(5)
        now = time_now()
        timeout = settings.session_orphan_timeout_s
        for sess in store.all():
            if sess.state in busy:
                continue
            if now - sess.updated_at < timeout:
                continue
            await _force_close_session(sess.session_id, reason="orphan_timeout")


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

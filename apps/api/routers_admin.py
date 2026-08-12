"""Admin API routes: avatar / voice / agent."""
from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
PUB_DIR = ROOT / "apps" / "publisher"
if str(PUB_DIR) not in sys.path:
    sys.path.insert(0, str(PUB_DIR))

from avatar_frame_controller import AvatarPackage  # noqa: E402
from avatar_preprocess import (  # noqa: E402
    build_avatar_package_dual,
    update_frames,
)
from config import settings, tts_http_bases  # noqa: E402
from services import asset_store  # noqa: E402
from services.dify_adapter import dify_adapter  # noqa: E402

logger = logging.getLogger("api.admin")
router = APIRouter(prefix="/api/v1")


class FramesBody(BaseModel):
    idle_frame: int
    talk_start: int
    talk_end: int
    transition_frames: int | None = None


async def _reload_tts_voices() -> None:
    bases = tts_http_bases()
    if not bases:
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        for base in bases:
            try:
                await client.post(f"{base}/internal/tts/voices/reload")
            except Exception as exc:  # noqa: BLE001
                logger.warning("tts voice reload failed %s: %s", base, exc)


async def _upsert_tts_voice(meta: dict) -> None:
    """Push voice to every TTS worker (no restart)."""
    import base64
    from pathlib import Path

    bases = tts_http_bases()
    if not bases:
        return
    wav = Path(meta.get("prompt_wav") or "")
    if not wav.exists():
        return
    payload = {
        "name": meta.get("name") or meta.get("voice_id"),
        "prompt_text": meta.get("prompt_text") or "",
        "prompt_wav_b64": base64.b64encode(wav.read_bytes()).decode("ascii"),
    }
    vid = meta["voice_id"]
    async with httpx.AsyncClient(timeout=30.0) as client:
        for base in bases:
            try:
                r = await client.post(f"{base}/internal/tts/voices/{vid}", json=payload)
                if r.status_code >= 300:
                    logger.warning("tts voice upsert %s -> %s %s", base, r.status_code, r.text[:200])
            except Exception as exc:  # noqa: BLE001
                logger.warning("tts voice upsert failed %s: %s", base, exc)


async def _delete_tts_voice(voice_id: str) -> None:
    bases = tts_http_bases()
    if not bases:
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        for base in bases:
            try:
                await client.delete(f"{base}/internal/tts/voices/{voice_id}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("tts voice delete failed %s: %s", base, exc)


def _run_preprocess_dual(
    idle_src: Path,
    talk_src: Path,
    *,
    avatar_id: str,
    version_id: str,
    name: str,
    voice_id: str,
) -> None:
    try:
        build_avatar_package_dual(
            idle_src,
            talk_src,
            avatar_id=avatar_id,
            version_id=version_id,
            out_root=settings.avatar_root,
            voice_id=voice_id,
            name=name,
        )
        logger.info("avatar ready %s/%s (dual)", avatar_id, version_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("avatar preprocess failed")
        asset_store.mark_avatar_failed(avatar_id, version_id, str(exc))


@router.get("/avatars", summary="列出形象", tags=["形象管理"])
async def list_avatars():
    da, dv, _, _ = asset_store.defaults()
    return {"avatars": asset_store.list_avatars(), "default_avatar_id": da, "default_version_id": dv}


@router.get("/voices", summary="列出音模", tags=["音模管理"])
async def list_voices():
    _, _, dv, _ = asset_store.defaults()
    return {"voices": asset_store.list_voices(), "default_voice_id": dv}


@router.get("/agents", summary="列出智能体", tags=["智能体管理"])
async def list_agents():
    _, _, _, dag = asset_store.defaults()
    return {"agents": asset_store.list_agents(), "default_agent_id": dag}


@router.post("/agents", summary="添加智能体", tags=["智能体管理"])
async def register_agent(api_key: str = Form(..., description="Dify App API Key")):
    """只需 API Key；Base URL 用 .env 的 DIFY_BASE_URL，名称从 Dify /v1/info 拉取。"""
    key = api_key.strip()
    base = (settings.dify_base_url or "").rstrip("/")
    if not base:
        raise HTTPException(400, "服务端未配置 DIFY_BASE_URL")
    try:
        info = await dify_adapter.fetch_info(key, base)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Dify /v1/info failed: {exc}") from exc
    aname = info.get("name") or "智能体"
    aid = f"agent_{uuid.uuid4().hex[:8]}"
    try:
        meta = asset_store.save_agent(
            agent_id=aid,
            api_key=key,
            name=aname,
            mode=info.get("mode") or "",
            base_url=base,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "agent": meta, "dify_info": {"name": info.get("name"), "mode": info.get("mode")}}


@router.post("/agents/{agent_id}/default", summary="设为默认智能体", tags=["智能体管理"])
async def set_default_agent(agent_id: str):
    try:
        asset_store.set_default_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "default_agent_id": agent_id}


@router.delete("/agents/{agent_id}", summary="删除智能体", tags=["智能体管理"])
async def delete_agent(agent_id: str):
    try:
        asset_store.delete_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "agent_id": agent_id}


@router.get("/options", summary="学生端可选资源", tags=["选项"])
async def list_options():
    """学生端/并发页：可选的就绪形象、音模、智能体及默认项。"""
    da, dv, dvoice, dag = asset_store.defaults()
    avatars = []
    for a in asset_store.list_avatars():
        ready_versions = [
            v for v in (a.get("versions") or [])
            if (v.get("status") in (None, "ready", "published"))
        ]
        if not ready_versions:
            continue
        # pick default version if this avatar is default, else last ready
        pick = None
        if a["avatar_id"] == da:
            pick = next((v for v in ready_versions if v.get("version_id") == dv), None)
        pick = pick or ready_versions[-1]
        avatars.append(
            {
                "avatar_id": a["avatar_id"],
                "version_id": pick.get("version_id"),
                "name": a.get("name") or a["avatar_id"],
                "voice_id": pick.get("voice_id") or a.get("voice_id") or "",
                "idle_image_url": f"/api/v1/avatars/{a['avatar_id']}/versions/{pick.get('version_id')}/idle.png",
                "idle_video_url": f"/api/v1/avatars/{a['avatar_id']}/versions/{pick.get('version_id')}/idle.mp4",
                "is_default": a["avatar_id"] == da and pick.get("version_id") == dv,
            }
        )
    return {
        "avatars": avatars,
        "voices": asset_store.list_voices(),
        "agents": asset_store.list_agents(),
        "defaults": {
            "avatar_id": da,
            "version_id": dv,
            "voice_id": dvoice,
            "agent_id": dag,
        },
    }


@router.post("/avatars", summary="上传形象", tags=["形象管理"])
async def upload_avatar(
    name: str = Form(..., description="形象名称"),
    idle_video: UploadFile = File(..., description="待机视频（闭嘴）"),
    talk_video: UploadFile = File(..., description="动嘴视频（说话）"),
):
    """管理端：形象名 + 待机视频 + 动嘴视频。待机正放倒放循环，说话用动嘴片。"""
    display = (name or "").strip()
    if not display:
        raise HTTPException(400, "name required")
    idle_data = await idle_video.read()
    talk_data = await talk_video.read()
    if not idle_data:
        raise HTTPException(400, "empty idle_video")
    if not talk_data:
        raise HTTPException(400, "empty talk_video")
    _, _, dvoice, _ = asset_store.defaults()
    aid, vid, upload_dir = asset_store.register_avatar_version(
        avatar_id=None,
        name=display,
        voice_id=dvoice or "",
    )
    idle_path = upload_dir / "idle_upload.mp4"
    talk_path = upload_dir / "talk_upload.mp4"
    idle_path.write_bytes(idle_data)
    talk_path.write_bytes(talk_data)
    pkg = settings.avatar_root / aid / vid
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "manifest.json").write_text(
        __import__("json").dumps(
            {
                "avatar_id": aid,
                "version_id": vid,
                "name": display,
                "voice_id": dvoice or "",
                "mode": "dual",
                "status": "processing",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    asyncio.create_task(
        asyncio.to_thread(
            _run_preprocess_dual,
            idle_path,
            talk_path,
            avatar_id=aid,
            version_id=vid,
            name=display,
            voice_id=dvoice or "",
        )
    )
    return {"avatar_id": aid, "version_id": vid, "status": "processing", "name": display, "mode": "dual"}


@router.patch("/avatars/{avatar_id}", summary="形象改名", tags=["形象管理"])
async def rename_avatar(avatar_id: str, name: str = Form(..., description="新名称")):
    try:
        meta = asset_store.rename_avatar(avatar_id, name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **meta}


@router.delete("/avatars/{avatar_id}/versions/{version_id}", summary="删除形象版本", tags=["形象管理"])
async def delete_avatar(avatar_id: str, version_id: str):
    try:
        asset_store.delete_avatar_version(avatar_id, version_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    AvatarPackage.invalidate(settings.avatar_root / avatar_id / version_id)
    return {"ok": True, "avatar_id": avatar_id, "version_id": version_id}


@router.post("/avatars/{avatar_id}/versions/{version_id}/default", summary="设为默认形象", tags=["形象管理"])
async def set_default_avatar(avatar_id: str, version_id: str):
    try:
        asset_store.set_default_avatar(avatar_id, version_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "default_avatar_id": avatar_id, "default_version_id": version_id}


@router.patch("/avatars/{avatar_id}/versions/{version_id}/frames", summary="调整形象帧区间", tags=["形象管理"])
async def patch_frames(avatar_id: str, version_id: str, body: FramesBody):
    pkg = settings.avatar_root / avatar_id / version_id
    if not (pkg / "manifest.json").exists():
        raise HTTPException(404, "version not found")
    try:
        manifest = update_frames(
            pkg,
            idle_frame=body.idle_frame,
            talk_start=body.talk_start,
            talk_end=body.talk_end,
            transition_frames=body.transition_frames,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    AvatarPackage.apply_frame_update(pkg)
    return {"ok": True, "manifest": manifest}


@router.post("/voices", summary="上传音模", tags=["音模管理"])
async def upload_voice(
    prompt_wav: UploadFile = File(..., description="参考音频"),
    name: str = Form(..., description="音模名称"),
):
    """音模名手填；prompt_text 走 ASR 自动识别。"""
    import tempfile

    from services.speech_clients import asr_transcribe

    display = name.strip()
    if not display:
        raise HTTPException(400, "音模名必填")
    data = await prompt_wav.read()
    if not data:
        raise HTTPException(400, "empty audio")
    voice_id = f"voice_{uuid.uuid4().hex[:8]}"
    suffix = Path(prompt_wav.filename or "ref.wav").suffix or ".wav"
    tmp = Path(tempfile.mkdtemp(prefix="voice_asr_")) / f"ref{suffix}"
    try:
        tmp.write_bytes(data)
        asr = await asr_transcribe(tmp, session_id="admin_voice", question_id=voice_id)
        prompt_text = (asr.get("text") or "").strip()
        if not prompt_text:
            raise HTTPException(400, "ASR 未识别到文本，请换一段更清晰的参考音频")
        meta = asset_store.save_voice(
            voice_id=voice_id,
            wav_bytes=data,
            prompt_text=prompt_text,
            name=display,
            source_suffix=suffix,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"ASR failed: {exc}") from exc
    finally:
        import shutil

        shutil.rmtree(tmp.parent, ignore_errors=True)
    await _upsert_tts_voice(meta)
    return {"ok": True, "voice": meta}


@router.get("/voices/{voice_id}/prompt.wav", summary="试听音模", tags=["音模管理"])
async def voice_prompt_wav(voice_id: str):
    """管理端试听参考音。"""
    path = asset_store.voice_dir(voice_id) / "prompt.wav"
    if not path.is_file():
        raise HTTPException(404, "prompt.wav not found")
    return FileResponse(path, media_type="audio/wav", filename=f"{voice_id}.wav")


@router.delete("/voices/{voice_id}", summary="删除音模", tags=["音模管理"])
async def delete_voice(voice_id: str):
    import shutil

    d = asset_store.VOICE_ROOT / voice_id
    if not d.exists():
        raise HTTPException(404, "voice not found")
    shutil.rmtree(d, ignore_errors=True)
    # 清默认指向
    with asset_store._lock:  # type: ignore[attr-defined]
        r = asset_store._read_registry()  # type: ignore[attr-defined]
        if r.get("default_voice_id") == voice_id:
            rest = [
                p.name
                for p in sorted(asset_store.VOICE_ROOT.iterdir())
                if p.is_dir() and (p / "meta.json").exists()
            ] if asset_store.VOICE_ROOT.exists() else []
            r["default_voice_id"] = rest[0] if rest else ""
            asset_store._write_registry(r)  # type: ignore[attr-defined]
    await _delete_tts_voice(voice_id)
    return {"ok": True, "voice_id": voice_id}


@router.post("/voices/{voice_id}/default", summary="设为默认音模", tags=["音模管理"])
async def set_default_voice(voice_id: str):
    try:
        asset_store.set_default_voice(voice_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "default_voice_id": voice_id}

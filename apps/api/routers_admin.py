"""Admin API routes: avatar / voice / agent."""
from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
PUB_DIR = ROOT / "apps" / "publisher"
if str(PUB_DIR) not in sys.path:
    sys.path.insert(0, str(PUB_DIR))

from avatar_frame_controller import AvatarPackage  # noqa: E402
from avatar_preprocess import build_avatar_package, update_frames  # noqa: E402
from config import settings, tts_http_bases  # noqa: E402
from services import asset_store  # noqa: E402
from services.dify_adapter import dify_adapter  # noqa: E402

logger = logging.getLogger("api.admin")
router = APIRouter(prefix="/api/v1", tags=["admin"])


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


def _run_preprocess(
    src: Path,
    *,
    avatar_id: str,
    version_id: str,
    name: str,
    voice_id: str,
    idle_frame: int,
    talk_start: int,
    talk_end: int,
) -> None:
    try:
        build_avatar_package(
            src,
            avatar_id=avatar_id,
            version_id=version_id,
            out_root=settings.avatar_root,
            idle_frame=idle_frame,
            talk_start=talk_start,
            talk_end=talk_end,
            voice_id=voice_id,
            name=name,
        )
        logger.info("avatar ready %s/%s", avatar_id, version_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("avatar preprocess failed")
        asset_store.mark_avatar_failed(avatar_id, version_id, str(exc))


@router.get("/avatars")
async def list_avatars():
    da, dv, _, _ = asset_store.defaults()
    return {"avatars": asset_store.list_avatars(), "default_avatar_id": da, "default_version_id": dv}


@router.get("/voices")
async def list_voices():
    _, _, dv, _ = asset_store.defaults()
    return {"voices": asset_store.list_voices(), "default_voice_id": dv}


@router.get("/agents")
async def list_agents():
    _, _, _, dag = asset_store.defaults()
    return {"agents": asset_store.list_agents(), "default_agent_id": dag}


@router.post("/agents")
async def register_agent(
    api_key: str = Form(...),
    agent_id: str = Form(""),
    base_url: str = Form(""),
    name: str = Form(""),
):
    key = api_key.strip()
    base = (base_url.strip() or settings.dify_base_url).rstrip("/")
    try:
        info = await dify_adapter.fetch_info(key, base)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Dify /v1/info failed: {exc}") from exc
    aname = name.strip() or info.get("name") or "智能体"
    aid = agent_id.strip() or f"agent_{uuid.uuid4().hex[:8]}"
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


@router.post("/agents/{agent_id}/default")
async def set_default_agent(agent_id: str):
    try:
        asset_store.set_default_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "default_agent_id": agent_id}


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    try:
        asset_store.delete_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "agent_id": agent_id}


@router.get("/options")
async def list_options():
    """Student/concurrent pages: selectable ready avatars + voices + agents."""
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


@router.post("/avatars")
async def upload_avatar(
    name: str = Form(...),
    video: UploadFile = File(...),
    voice_id: str = Form(""),
    idle_frame: int = Form(0),
    talk_start: int = Form(21),
    talk_end: int = Form(-1),
    avatar_id: str = Form(""),
):
    aid, vid, src_path = asset_store.register_avatar_version(
        avatar_id=avatar_id.strip() or None,
        name=name.strip(),
        voice_id=voice_id.strip(),
    )
    data = await video.read()
    if not data:
        raise HTTPException(400, "empty video")
    src_path.write_bytes(data)
    # placeholder processing manifest
    pkg = settings.avatar_root / aid / vid
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "manifest.json").write_text(
        __import__("json").dumps(
            {
                "avatar_id": aid,
                "version_id": vid,
                "name": name,
                "voice_id": voice_id,
                "status": "processing",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    asyncio.create_task(
        asyncio.to_thread(
            _run_preprocess,
            src_path,
            avatar_id=aid,
            version_id=vid,
            name=name.strip(),
            voice_id=voice_id.strip(),
            idle_frame=idle_frame,
            talk_start=talk_start,
            talk_end=talk_end,
        )
    )
    return {"avatar_id": aid, "version_id": vid, "status": "processing"}


@router.post("/avatars/{avatar_id}/versions/{version_id}/default")
async def set_default_avatar(avatar_id: str, version_id: str):
    try:
        asset_store.set_default_avatar(avatar_id, version_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "default_avatar_id": avatar_id, "default_version_id": version_id}


@router.patch("/avatars/{avatar_id}/versions/{version_id}/frames")
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


@router.post("/voices")
async def upload_voice(
    voice_id: str = Form(...),
    prompt_wav: UploadFile = File(...),
    prompt_text: str = Form("希望你以后能够做的比我还好呦。"),
    name: str = Form(""),
):
    data = await prompt_wav.read()
    if not data:
        raise HTTPException(400, "empty wav")
    try:
        meta = asset_store.save_voice(
            voice_id=voice_id.strip(),
            wav_bytes=data,
            prompt_text=prompt_text.strip() or "希望你以后能够做的比我还好呦。",
            name=name.strip(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await _upsert_tts_voice(meta)
    return {"ok": True, "voice": meta}


@router.delete("/voices/{voice_id}")
async def delete_voice(voice_id: str):
    import shutil

    if voice_id == asset_store.defaults()[2]:
        raise HTTPException(400, "cannot delete default voice")
    d = asset_store.VOICE_ROOT / voice_id
    t = asset_store.TTS_VOICE_ROOT / voice_id
    if not d.exists() and not t.exists():
        raise HTTPException(404, "voice not found")
    shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(t, ignore_errors=True)
    await _delete_tts_voice(voice_id)
    return {"ok": True, "voice_id": voice_id}


@router.post("/voices/{voice_id}/default")
async def set_default_voice(voice_id: str):
    try:
        asset_store.set_default_voice(voice_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "default_voice_id": voice_id}

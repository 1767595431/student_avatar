"""Avatar + voice registry on disk (data/avatars, data/voices)."""
from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
AVATAR_ROOT = ROOT / "data" / "avatars"
VOICE_ROOT = ROOT / "data" / "voices"
AGENT_ROOT = ROOT / "data" / "agents"
TTS_VOICE_ROOT = ROOT / "tts" / "voices"
REGISTRY = AVATAR_ROOT / "registry.json"

_lock = threading.Lock()


def _read_registry() -> dict[str, Any]:
    if not REGISTRY.exists():
        data = {
            "default_avatar_id": "avatar_001",
            "default_version_id": "avv_001",
            "default_voice_id": "avatar_voice_001",
            "default_agent_id": "",
            "avatars": {},
        }
        # seed existing package if present
        pkg = AVATAR_ROOT / "avatar_001" / "avv_001" / "manifest.json"
        if pkg.exists():
            m = json.loads(pkg.read_text(encoding="utf-8"))
            data["avatars"]["avatar_001"] = {
                "name": m.get("name") or "avatar_001",
                "voice_id": m.get("voice_id") or "avatar_voice_001",
                "versions": ["avv_001"],
            }
        REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _write_registry(data: dict[str, Any]) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def defaults() -> tuple[str, str, str, str]:
    with _lock:
        r = _read_registry()
        return (
            r.get("default_avatar_id") or "avatar_001",
            r.get("default_version_id") or "avv_001",
            r.get("default_voice_id") or "avatar_voice_001",
            r.get("default_agent_id") or "",
        )


def set_default_avatar(avatar_id: str, version_id: str) -> dict[str, Any]:
    with _lock:
        r = _read_registry()
        if avatar_id not in r.get("avatars", {}):
            raise KeyError("avatar not found")
        if version_id not in r["avatars"][avatar_id].get("versions", []):
            raise KeyError("version not found")
        r["default_avatar_id"] = avatar_id
        r["default_version_id"] = version_id
        _write_registry(r)
        return r


def set_default_voice(voice_id: str) -> dict[str, Any]:
    with _lock:
        meta = VOICE_ROOT / voice_id / "meta.json"
        if not meta.exists() and voice_id != "avatar_voice_001":
            raise KeyError("voice not found")
        r = _read_registry()
        r["default_voice_id"] = voice_id
        _write_registry(r)
        return r


def list_avatars() -> list[dict[str, Any]]:
    with _lock:
        r = _read_registry()
    out: list[dict[str, Any]] = []
    for aid, info in (r.get("avatars") or {}).items():
        versions = []
        for vid in info.get("versions") or []:
            mpath = AVATAR_ROOT / aid / vid / "manifest.json"
            if mpath.exists():
                versions.append(json.loads(mpath.read_text(encoding="utf-8")))
            else:
                versions.append({"avatar_id": aid, "version_id": vid, "status": "processing"})
        out.append(
            {
                "avatar_id": aid,
                "name": info.get("name") or aid,
                "voice_id": info.get("voice_id") or "",
                "versions": versions,
                "is_default": aid == r.get("default_avatar_id")
                and r.get("default_version_id") in (info.get("versions") or []),
            }
        )
    # also discover packages not in registry
    if AVATAR_ROOT.exists():
        known = {a["avatar_id"] for a in out}
        for d in sorted(AVATAR_ROOT.iterdir()):
            if not d.is_dir() or d.name.startswith(".") or d.name == "uploads":
                continue
            if d.name in known:
                continue
            versions = []
            for v in sorted(d.iterdir()):
                m = v / "manifest.json"
                if m.exists():
                    versions.append(json.loads(m.read_text(encoding="utf-8")))
            if versions:
                out.append(
                    {
                        "avatar_id": d.name,
                        "name": versions[-1].get("name") or d.name,
                        "voice_id": versions[-1].get("voice_id") or "",
                        "versions": versions,
                        "is_default": False,
                    }
                )
    return out


def register_avatar_version(
    *,
    avatar_id: str | None,
    name: str,
    voice_id: str,
) -> tuple[str, str, Path]:
    """Allocate ids + upload dir. Returns avatar_id, version_id, upload_target_path."""
    with _lock:
        r = _read_registry()
        aid = avatar_id or f"avatar_{uuid.uuid4().hex[:8]}"
        existing = (r.get("avatars") or {}).get(aid, {})
        n = len(existing.get("versions") or []) + 1
        vid = f"avv_{n:03d}"
        upload_dir = AVATAR_ROOT / "uploads" / aid / vid
        upload_dir.mkdir(parents=True, exist_ok=True)
        src = upload_dir / "source.mp4"
        avatars = r.setdefault("avatars", {})
        entry = avatars.setdefault(aid, {"name": name, "voice_id": voice_id, "versions": []})
        entry["name"] = name or entry.get("name") or aid
        entry["voice_id"] = voice_id or entry.get("voice_id") or ""
        if vid not in entry["versions"]:
            entry["versions"].append(vid)
        _write_registry(r)
        return aid, vid, src


def mark_avatar_failed(avatar_id: str, version_id: str, error: str) -> None:
    pkg = AVATAR_ROOT / avatar_id / version_id
    pkg.mkdir(parents=True, exist_ok=True)
    path = pkg / "manifest.json"
    meta: dict[str, Any] = {}
    if path.exists():
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
    meta.update(
        {
            "avatar_id": avatar_id,
            "version_id": version_id,
            "status": "failed",
            "error": error,
        }
    )
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def list_voices() -> list[dict[str, Any]]:
    with _lock:
        r = _read_registry()
        default_vid = r.get("default_voice_id") or "avatar_voice_001"
    out: list[dict[str, Any]] = []
    VOICE_ROOT.mkdir(parents=True, exist_ok=True)
    # ensure default from tts/voices/default_prompt.wav is listed
    default_wav = TTS_VOICE_ROOT / "default_prompt.wav"
    if default_wav.exists() and not (VOICE_ROOT / "avatar_voice_001" / "meta.json").exists():
        save_voice(
            voice_id="avatar_voice_001",
            wav_bytes=default_wav.read_bytes(),
            prompt_text="希望你以后能够做的比我还好呦。",
            name="默认音模",
        )
    for d in sorted(VOICE_ROOT.iterdir()):
        meta = d / "meta.json"
        if not meta.exists():
            continue
        m = json.loads(meta.read_text(encoding="utf-8"))
        m["is_default"] = m.get("voice_id") == default_vid
        out.append(m)
    return out


def save_voice(
    *,
    voice_id: str,
    wav_bytes: bytes,
    prompt_text: str,
    name: str = "",
) -> dict[str, Any]:
    if not voice_id or "/" in voice_id or ".." in voice_id:
        raise ValueError("invalid voice_id")
    dest = VOICE_ROOT / voice_id
    dest.mkdir(parents=True, exist_ok=True)
    wav_path = dest / "prompt.wav"
    wav_path.write_bytes(wav_bytes)
    # mirror for TTS workers that scan tts/voices/<id>/
    tts_dest = TTS_VOICE_ROOT / voice_id
    tts_dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wav_path, tts_dest / "prompt.wav")
    meta = {
        "voice_id": voice_id,
        "name": name or voice_id,
        "prompt_text": prompt_text,
        "prompt_wav": str(wav_path),
        "created_at": int(time.time()),
    }
    meta_json = json.dumps(meta, ensure_ascii=False, indent=2)
    (dest / "meta.json").write_text(meta_json, encoding="utf-8")
    (tts_dest / "meta.json").write_text(meta_json, encoding="utf-8")
    return meta


def voice_dir(voice_id: str) -> Path:
    return VOICE_ROOT / voice_id


def list_agents(*, include_secret: bool = False) -> list[dict[str, Any]]:
    with _lock:
        r = _read_registry()
        default_aid = r.get("default_agent_id") or ""
    AGENT_ROOT.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    for d in sorted(AGENT_ROOT.iterdir()):
        meta = d / "meta.json"
        if not meta.exists():
            continue
        m = json.loads(meta.read_text(encoding="utf-8"))
        item = {
            "agent_id": m.get("agent_id") or d.name,
            "name": m.get("name") or d.name,
            "mode": m.get("mode") or "",
            "base_url": m.get("base_url") or "",
            "is_default": (m.get("agent_id") or d.name) == default_aid,
        }
        if include_secret:
            item["api_key"] = m.get("api_key") or ""
        out.append(item)
    return out


def get_agent(agent_id: str) -> dict[str, Any] | None:
    meta = AGENT_ROOT / agent_id / "meta.json"
    if not meta.exists():
        return None
    return json.loads(meta.read_text(encoding="utf-8"))


def save_agent(
    *,
    agent_id: str,
    api_key: str,
    name: str = "",
    mode: str = "",
    base_url: str = "",
) -> dict[str, Any]:
    if not agent_id or "/" in agent_id or ".." in agent_id:
        raise ValueError("invalid agent_id")
    if not api_key.strip():
        raise ValueError("api_key required")
    dest = AGENT_ROOT / agent_id
    dest.mkdir(parents=True, exist_ok=True)
    meta = {
        "agent_id": agent_id,
        "name": name or agent_id,
        "api_key": api_key.strip(),
        "mode": mode or "",
        "base_url": (base_url or "").rstrip("/"),
        "created_at": int(time.time()),
    }
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    with _lock:
        r = _read_registry()
        if not r.get("default_agent_id"):
            r["default_agent_id"] = agent_id
            _write_registry(r)
    return {k: v for k, v in meta.items() if k != "api_key"} | {"agent_id": agent_id, "name": meta["name"]}


def set_default_agent(agent_id: str) -> dict[str, Any]:
    if not get_agent(agent_id):
        raise KeyError("agent not found")
    with _lock:
        r = _read_registry()
        r["default_agent_id"] = agent_id
        _write_registry(r)
        return r


def delete_agent(agent_id: str) -> None:
    import shutil

    if not agent_id or "/" in agent_id or ".." in agent_id:
        raise ValueError("invalid agent_id")
    dest = AGENT_ROOT / agent_id
    with _lock:
        if not dest.exists():
            raise KeyError("agent not found")
        r = _read_registry()
        if r.get("default_agent_id") == agent_id:
            raise ValueError("cannot delete default agent")
        shutil.rmtree(dest, ignore_errors=True)
        r = _read_registry()
        if r.get("default_agent_id") == agent_id:
            r["default_agent_id"] = ""
            _write_registry(r)


def ensure_env_agent(api_key: str, base_url: str, name: str = "", mode: str = "") -> dict[str, Any] | None:
    """Seed one agent from .env if registry empty / missing."""
    if not api_key:
        return None
    AGENT_ROOT.mkdir(parents=True, exist_ok=True)
    existing = list_agents()
    if existing:
        with _lock:
            r = _read_registry()
            if not r.get("default_agent_id"):
                r["default_agent_id"] = existing[0]["agent_id"]
                _write_registry(r)
        return get_agent(existing[0]["agent_id"])
    aid = "agent_default"
    return save_agent(
        agent_id=aid,
        api_key=api_key,
        name=name or "默认智能体",
        mode=mode,
        base_url=base_url,
    )

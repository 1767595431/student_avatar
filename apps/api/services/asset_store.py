"""Avatar + voice registry on disk (data/avatars, data/voices)."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
AVATAR_ROOT = ROOT / "data" / "avatars"
VOICE_ROOT = ROOT / "data" / "voices"
AGENT_ROOT = ROOT / "data" / "agents"
REGISTRY = AVATAR_ROOT / "registry.json"

_lock = threading.Lock()


def _mask_api_key(key: str) -> str:
    k = (key or "").strip()
    if len(k) <= 12:
        return "••••" if k else ""
    return f"{k[:6]}…{k[-4:]}"


def normalize_prompt_wav(audio_bytes: bytes, *, suffix: str = ".bin") -> bytes:
    """任意上传格式 → 真 RIFF PCM s16le mono 24k（Qwen Base 克隆要求 data:audio/wav）。"""
    if len(audio_bytes) >= 12 and audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
        # 已是 WAV：仍统一成 24k mono，避免 44.1k/立体声把克隆搞糊
        pass
    with tempfile.TemporaryDirectory(prefix="voice_norm_") as td:
        src = Path(td) / f"in{suffix if suffix.startswith('.') else '.' + suffix}"
        dst = Path(td) / "out.wav"
        src.write_bytes(audio_bytes)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg not found; required to normalize voice prompt") from exc
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or b"").decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"ffmpeg normalize failed: {err}") from exc
        out = dst.read_bytes()
        if len(out) < 44 or out[:4] != b"RIFF":
            raise RuntimeError("normalized prompt is not a WAV")
        return out


def _empty_registry() -> dict[str, Any]:
    return {
        "default_avatar_id": "",
        "default_version_id": "",
        "default_voice_id": "",
        "default_agent_id": "",
        "avatars": {},
    }


def _read_registry() -> dict[str, Any]:
    if not REGISTRY.exists():
        data = _empty_registry()
        REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _write_registry(data: dict[str, Any]) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def defaults() -> tuple[str, str, str, str]:
    """当前默认（未设置则为空串）。新系统初始全空，仅管理端添加后才有。"""
    with _lock:
        r = _read_registry()
        return (
            r.get("default_avatar_id") or "",
            r.get("default_version_id") or "",
            r.get("default_voice_id") or "",
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
        if not meta.exists():
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


def rename_avatar(avatar_id: str, name: str) -> dict[str, Any]:
    """Update display name on registry + each version manifest."""
    display = (name or "").strip()
    if not avatar_id or "/" in avatar_id:
        raise ValueError("invalid id")
    if not display:
        raise ValueError("name required")
    with _lock:
        r = _read_registry()
        entry = (r.get("avatars") or {}).get(avatar_id)
        if not entry:
            raise KeyError(avatar_id)
        entry["name"] = display
        _write_registry(r)
        for vid in entry.get("versions") or []:
            mpath = AVATAR_ROOT / avatar_id / vid / "manifest.json"
            if not mpath.exists():
                continue
            meta = json.loads(mpath.read_text(encoding="utf-8"))
            meta["name"] = display
            mpath.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"avatar_id": avatar_id, "name": display}


def register_avatar_version(
    *,
    avatar_id: str | None,
    name: str,
    voice_id: str,
) -> tuple[str, str, Path]:
    """Allocate ids + upload dir. Returns avatar_id, version_id, upload_dir."""
    with _lock:
        r = _read_registry()
        aid = avatar_id or f"avatar_{uuid.uuid4().hex[:8]}"
        existing = (r.get("avatars") or {}).get(aid, {})
        n = len(existing.get("versions") or []) + 1
        vid = f"avv_{n:03d}"
        upload_dir = AVATAR_ROOT / "uploads" / aid / vid
        upload_dir.mkdir(parents=True, exist_ok=True)
        avatars = r.setdefault("avatars", {})
        entry = avatars.setdefault(aid, {"name": name, "voice_id": voice_id, "versions": []})
        entry["name"] = name or entry.get("name") or aid
        entry["voice_id"] = voice_id or entry.get("voice_id") or ""
        if vid not in entry["versions"]:
            entry["versions"].append(vid)
        # 第一个形象自动成为默认
        if not r.get("default_avatar_id"):
            r["default_avatar_id"] = aid
            r["default_version_id"] = vid
        _write_registry(r)
        return aid, vid, upload_dir


def delete_avatar_version(avatar_id: str, version_id: str) -> None:
    """Remove one avatar version from disk + registry；删默认则顺延或清空。"""
    import shutil

    if not avatar_id or not version_id or "/" in avatar_id or "/" in version_id:
        raise ValueError("invalid id")
    pkg = AVATAR_ROOT / avatar_id / version_id
    with _lock:
        r = _read_registry()
        entry = (r.get("avatars") or {}).get(avatar_id)
        in_reg = bool(entry and version_id in (entry.get("versions") or []))
        if not in_reg and not pkg.exists():
            raise KeyError("version not found")
        was_default = (
            r.get("default_avatar_id") == avatar_id and r.get("default_version_id") == version_id
        )
        if entry:
            entry["versions"] = [v for v in (entry.get("versions") or []) if v != version_id]
            if not entry["versions"]:
                (r.get("avatars") or {}).pop(avatar_id, None)
        if was_default:
            nxt_a, nxt_v = "", ""
            for aid, info in (r.get("avatars") or {}).items():
                vers = info.get("versions") or []
                if vers:
                    nxt_a, nxt_v = aid, vers[0]
                    break
            r["default_avatar_id"] = nxt_a
            r["default_version_id"] = nxt_v
        _write_registry(r)
    shutil.rmtree(pkg, ignore_errors=True)
    shutil.rmtree(AVATAR_ROOT / "uploads" / avatar_id / version_id, ignore_errors=True)
    adir = AVATAR_ROOT / avatar_id
    if adir.is_dir() and not any(adir.iterdir()):
        shutil.rmtree(adir, ignore_errors=True)


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
        default_vid = r.get("default_voice_id") or ""
    out: list[dict[str, Any]] = []
    VOICE_ROOT.mkdir(parents=True, exist_ok=True)
    for d in sorted(VOICE_ROOT.iterdir()):
        meta = d / "meta.json"
        if not meta.exists():
            continue
        m = json.loads(meta.read_text(encoding="utf-8"))
        m["is_default"] = bool(default_vid) and m.get("voice_id") == default_vid
        out.append(m)
    return out


def save_voice(
    *,
    voice_id: str,
    wav_bytes: bytes,
    prompt_text: str,
    name: str = "",
    source_suffix: str = ".wav",
) -> dict[str, Any]:
    if not voice_id or "/" in voice_id or ".." in voice_id:
        raise ValueError("invalid voice_id")
    dest = VOICE_ROOT / voice_id
    dest.mkdir(parents=True, exist_ok=True)
    wav_path = dest / "prompt.wav"
    # 管理端常上传 mp3 却叫 .wav；Qwen 按 WAV 解会直接出噪音
    wav_path.write_bytes(normalize_prompt_wav(wav_bytes, suffix=source_suffix))
    meta = {
        "voice_id": voice_id,
        "name": name or voice_id,
        "prompt_text": prompt_text,
        "prompt_wav": str(wav_path),
        "created_at": int(time.time()),
    }
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    with _lock:
        r = _read_registry()
        if not r.get("default_voice_id"):
            r["default_voice_id"] = voice_id
            _write_registry(r)
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
            "api_key_masked": _mask_api_key(m.get("api_key") or ""),
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
        shutil.rmtree(dest, ignore_errors=True)
        r = _read_registry()
        if r.get("default_agent_id") == agent_id:
            # 持锁内直接扫盘，避免 list_agents() 再抢同一把锁死锁
            rest: list[str] = []
            if AGENT_ROOT.exists():
                for d in sorted(AGENT_ROOT.iterdir()):
                    if d.is_dir() and (d / "meta.json").exists():
                        rest.append(d.name)
            r["default_agent_id"] = rest[0] if rest else ""
            _write_registry(r)

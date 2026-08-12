"""总控：各后端服务连通探测（短超时 + 短缓存）。"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from config import asr_http_bases, settings, tts_http_bases

_CACHE_TTL_S = 2.0
_cache_at = 0.0
_cache: list[dict[str, Any]] | None = None
_lock = asyncio.Lock()


def _livekit_http() -> str:
    u = (settings.livekit_url or "ws://127.0.0.1:7880").strip()
    if u.startswith("ws://"):
        u = "http://" + u[5:]
    elif u.startswith("wss://"):
        u = "https://" + u[6:]
    return u.rstrip("/") + "/"


def _port_label(url: str) -> str:
    try:
        p = urlparse(url if "://" in url else f"http://{url}")
        return str(p.port or "")
    except Exception:  # noqa: BLE001
        return ""


def _vllm_url_for_adapter(adapter_base: str) -> str | None:
    """8300→8091，8301→8092；其它端口不猜。"""
    port = _port_label(adapter_base)
    mapping = {"8300": "8091", "8301": "8092"}
    eng = mapping.get(port)
    if not eng:
        return None
    return f"http://127.0.0.1:{eng}"


async def _one(client: httpx.AsyncClient, *, id_: str, name: str, url: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        r = await client.get(url)
        ms = int((time.perf_counter() - t0) * 1000)
        ok = 200 <= r.status_code < 500
        return {
            "id": id_,
            "name": name,
            "url": url,
            "ok": ok,
            "status": "up" if ok else "down",
            "http_status": r.status_code,
            "latency_ms": ms,
            "detail": "" if ok else f"HTTP {r.status_code}",
        }
    except Exception as exc:  # noqa: BLE001
        ms = int((time.perf_counter() - t0) * 1000)
        return {
            "id": id_,
            "name": name,
            "url": url,
            "ok": False,
            "status": "down",
            "http_status": 0,
            "latency_ms": ms,
            "detail": str(exc)[:100],
        }


def _targets() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = [
        ("livekit", "LiveKit", _livekit_http()),
    ]
    for i, base in enumerate(asr_http_bases()):
        port = _port_label(base) or str(i)
        out.append((f"asr_{port}", f"ASR :{port}", f"{base.rstrip('/')}/health"))
    for i, base in enumerate(tts_http_bases()):
        port = _port_label(base) or str(i)
        out.append((f"tts_ad_{port}", f"TTS适配 :{port}", f"{base.rstrip('/')}/health"))
        eng = _vllm_url_for_adapter(base)
        if eng:
            ep = _port_label(eng)
            out.append((f"tts_eng_{ep}", f"TTS引擎 :{ep}", f"{eng}/v1/models"))
    dify = (settings.dify_base_url or "").rstrip("/")
    if dify:
        out.append(("dify", "Dify", f"{dify}/"))
    return out


async def probe_services(*, force: bool = False) -> list[dict[str, Any]]:
    global _cache_at, _cache
    async with _lock:
        now = time.monotonic()
        if not force and _cache is not None and (now - _cache_at) < _CACHE_TTL_S:
            return list(_cache)
        targets = _targets()
        async with httpx.AsyncClient(timeout=1.5, follow_redirects=True) as client:
            rows = await asyncio.gather(*[
                _one(client, id_=i, name=n, url=u) for i, n, u in targets
            ])
        _cache = list(rows)
        _cache_at = now
        return list(_cache)

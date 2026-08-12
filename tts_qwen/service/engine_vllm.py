"""Qwen3-TTS via vLLM-Omni：双卡 least-inflight，真并发（无同卡串行锁）。"""
from __future__ import annotations

import base64
import json
import logging
import threading
from pathlib import Path
from typing import Any, Iterator, Optional

import httpx

from config import settings

logger = logging.getLogger("tts_qwen.vllm")


class VllmQwenEngine:
    def __init__(self) -> None:
        self._voices: dict[str, dict[str, Any]] = {}
        self._ref_cache: dict[str, str] = {}
        self._inflight: dict[str, int] = {}
        self._lock = threading.Lock()
        self.sample_rate = settings.output_sample_rate
        bases = [u.strip().rstrip("/") for u in (settings.vllm_urls or "").split(",") if u.strip()]
        self._bases = bases or ["http://127.0.0.1:8091", "http://127.0.0.1:8092"]
        for b in self._bases:
            self._inflight[b] = 0

    def load(self) -> None:
        self.reload_voices_from_disk()
        self._model_id = (settings.model_id or "").strip()
        if not self._model_id:
            self._model_id = self._discover_model_id()
        logger.info(
            "vLLM Qwen3-TTS ready model=%s bases=%s voices=%s",
            self._model_id,
            self._bases,
            list(self._voices),
        )

    def _discover_model_id(self) -> str:
        """本地 snapshot 启动时 /v1/models 的 id 是路径，不是 HF repo 名。"""
        for base in self._bases:
            try:
                with httpx.Client(timeout=5.0) as client:
                    r = client.get(f"{base}/v1/models")
                    r.raise_for_status()
                    data = r.json().get("data") or []
                    if data and data[0].get("id"):
                        return str(data[0]["id"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("discover model from %s failed: %s", base, exc)
        return "Qwen/Qwen3-TTS-12Hz-0.6B-Base"

    def reload_voices_from_disk(self) -> list[str]:
        root = settings.voice_root
        root.mkdir(parents=True, exist_ok=True)
        found: list[str] = []
        fresh: dict[str, dict[str, Any]] = {}
        self._ref_cache.clear()
        for d in sorted(root.iterdir()) if root.exists() else []:
            if not d.is_dir():
                continue
            wav = d / "prompt.wav"
            if not wav.exists():
                continue
            raw_head = wav.read_bytes()[:12]
            # Qwen Base 要真 WAV；mp3 改后缀会直接出噪音
            if raw_head[:4] != b"RIFF" or raw_head[8:12] != b"WAVE":
                logger.error("skip voice %s: prompt.wav is not RIFF/WAVE (got %r)", d.name, raw_head[:4])
                continue
            prompt_text = ""
            name = d.name
            meta_path = d / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    prompt_text = (meta.get("prompt_text") or "").strip()
                    name = meta.get("name") or name
                except Exception:  # noqa: BLE001
                    pass
            fresh[d.name] = {
                "voice_id": d.name,
                "name": name,
                "prompt_text": prompt_text,
                "prompt_wav": str(wav.resolve()),
            }
            found.append(d.name)
        self._voices = fresh
        return found

    def register_voice(self, voice_id: str, *, prompt_wav: Path, prompt_text: str, name: str = "") -> None:
        self._voices[voice_id] = {
            "voice_id": voice_id,
            "name": name or voice_id,
            "prompt_text": (prompt_text or "").strip(),
            "prompt_wav": str(Path(prompt_wav).resolve()),
        }
        self._ref_cache.pop(voice_id, None)

    def unregister_voice(self, voice_id: str) -> None:
        self._voices.pop(voice_id, None)
        self._ref_cache.pop(voice_id, None)

    def list_voices(self) -> list[dict[str, Any]]:
        return list(self._voices.values())

    def _resolve_voice(self, voice_id: Optional[str]) -> dict[str, Any]:
        vid = (voice_id or settings.default_voice_id or "").strip()
        if vid and vid in self._voices:
            return self._voices[vid]
        if settings.default_voice_id in self._voices:
            return self._voices[settings.default_voice_id]
        if self._voices:
            return next(iter(self._voices.values()))
        raise RuntimeError("no voice registered")

    def _ref_data_url(self, voice: dict[str, Any]) -> str:
        vid = voice["voice_id"]
        cached = self._ref_cache.get(vid)
        if cached:
            return cached
        raw = Path(voice["prompt_wav"]).read_bytes()
        if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
            raise RuntimeError(
                f"voice {vid} prompt.wav is not RIFF/WAVE (often mp3 renamed); re-upload voice"
            )
        url = "data:audio/wav;base64," + base64.b64encode(raw).decode("ascii")
        self._ref_cache[vid] = url
        return url

    def _pick_base(self) -> str:
        with self._lock:
            base = min(self._bases, key=lambda u: self._inflight.get(u, 0))
            self._inflight[base] = self._inflight.get(base, 0) + 1
            return base

    def _release_base(self, base: str) -> None:
        with self._lock:
            self._inflight[base] = max(0, self._inflight.get(base, 0) - 1)

    def synthesize_stream(
        self,
        text: str,
        *,
        voice_id: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Iterator[bytes]:
        text = (text or "").strip()
        if not text:
            return
        if cancel_event and cancel_event.is_set():
            return
        voice = self._resolve_voice(voice_id)
        ref_text = (voice.get("prompt_text") or "").strip()
        if not ref_text:
            raise RuntimeError(f"voice {voice['voice_id']} missing prompt_text (required for Base clone)")
        payload = {
            "model": getattr(self, "_model_id", None) or settings.model_id,
            "input": text,
            "task_type": settings.task_type,
            "language": settings.language,
            "ref_audio": self._ref_data_url(voice),
            "ref_text": ref_text,
            "stream": True,
            "stream_format": "audio",
            "response_format": "pcm",
        }
        base = self._pick_base()
        url = f"{base}/v1/audio/speech"
        try:
            got_pcm = False
            with httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
                with client.stream("POST", url, json=payload) as resp:
                    if resp.status_code >= 400:
                        body = resp.read().decode("utf-8", errors="replace")[:800]
                        raise RuntimeError(f"vLLM {base} HTTP {resp.status_code}: {body}")
                    for chunk in resp.iter_bytes():
                        if cancel_event and cancel_event.is_set():
                            break
                        if not chunk:
                            continue
                        got_pcm = True
                        yield chunk
            if not got_pcm and not (cancel_event and cancel_event.is_set()):
                raise RuntimeError(f"vLLM empty audio from {base}")
        finally:
            self._release_base(base)

    def synthesize(
        self,
        text: str,
        *,
        voice_id: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> bytes:
        return b"".join(self.synthesize_stream(text, voice_id=voice_id, cancel_event=cancel_event))

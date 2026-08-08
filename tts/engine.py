"""CosyVoice3 engine wrapper (batch PCM; WS path legacy)."""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Generator, Iterable, Optional
import json

import numpy as np
import torch

from config import settings

logger = logging.getLogger("tts.engine")


def _patch_torch_numpy_compat() -> None:
    """Work around torch/numpy C-API mismatch on this host.

    Symptom: torch.from_numpy(ndarray) raises
    TypeError: expected np.ndarray (got numpy.ndarray)
    """
    if getattr(torch, "_student_numpy_patched", False):
        return

    _orig_from_numpy = torch.from_numpy
    _dtype_map = {
        np.dtype("float32"): torch.float32,
        np.dtype("float64"): torch.float64,
        np.dtype("int16"): torch.int16,
        np.dtype("int32"): torch.int32,
        np.dtype("int64"): torch.int64,
        np.dtype("uint8"): torch.uint8,
        np.dtype("bool"): torch.bool,
    }

    def _from_numpy_safe(arr):  # noqa: ANN001
        try:
            return _orig_from_numpy(arr)
        except TypeError:
            data = np.asarray(arr)
            return torch.tensor(data.tolist(), dtype=_dtype_map.get(data.dtype, torch.float32))

    torch.from_numpy = _from_numpy_safe  # type: ignore[assignment]
    torch._student_numpy_patched = True  # type: ignore[attr-defined]
    logger.warning("Applied torch.from_numpy compatibility patch")


class TTSEngine:
    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()
        self._voice_prompt_wav: dict[str, Path] = {}
        self._voice_prompt_text: dict[str, str] = {}

    def load(self) -> None:
        if self._model is not None:
            return

        _patch_torch_numpy_compat()

        cosy_root = settings.cosyvoice_dir
        matcha = cosy_root / "third_party" / "Matcha-TTS"
        for p in (str(cosy_root), str(matcha)):
            if p not in sys.path:
                sys.path.insert(0, p)

        from cosyvoice.cli.cosyvoice import AutoModel

        if not settings.model_dir.exists():
            raise FileNotFoundError(f"CosyVoice model not found: {settings.model_dir}")

        logger.info("Loading CosyVoice3 from %s on %s", settings.model_dir, settings.device)
        # CosyVoice AutoModel picks CUDA automatically; pin via CUDA_VISIBLE_DEVICES in launcher
        self._model = AutoModel(model_dir=str(settings.model_dir))

        # Register default voice
        prompt = settings.prompt_wav
        if not prompt.exists():
            # Fall back to CosyVoice bundled asset
            bundled = cosy_root / "asset" / "zero_shot_prompt.wav"
            if bundled.exists():
                prompt = bundled
            else:
                raise FileNotFoundError(f"prompt wav missing: {settings.prompt_wav}")
        self.register_voice(
            settings.default_voice_id,
            prompt_wav=prompt,
            prompt_text=settings.prompt_text,
        )
        self.reload_voices_from_disk()
        logger.info("CosyVoice3 ready, sample_rate=%s", self.sample_rate)

    def reload_voices_from_disk(self) -> list[str]:
        """Scan tts/voices/<voice_id>/{prompt.wav,meta.json} and register."""
        root = settings.root_dir / "voices"
        loaded: list[str] = []
        if not root.exists():
            return loaded
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            wav = d / "prompt.wav"
            meta_path = d / "meta.json"
            if not wav.exists():
                continue
            prompt_text = settings.prompt_text
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    prompt_text = meta.get("prompt_text") or prompt_text
                except Exception:  # noqa: BLE001
                    pass
            self.register_voice(d.name, prompt_wav=wav, prompt_text=prompt_text)
            loaded.append(d.name)
        logger.info("Voices loaded from disk: %s", loaded)
        return loaded

    def list_voices(self) -> list[str]:
        return sorted(self._voice_prompt_wav.keys())

    def warmup(self, text: str = "同学们好，我们开始上课。") -> float:
        """Run one short synth to warm CUDA kernels / caches. Returns wall seconds."""
        if self._model is None:
            self.load()
        t0 = time.perf_counter()
        n = 0
        for pcm in self.synthesize_stream(text, stream=True):
            n += len(pcm)
        dt = time.perf_counter() - t0
        logger.info("TTS warmup done bytes=%s wall_s=%.2f text=%s", n, dt, text)
        return dt

    @property
    def sample_rate(self) -> int:
        if self._model is None:
            return settings.sample_rate
        return int(getattr(self._model, "sample_rate", settings.sample_rate))

    def register_voice(self, voice_id: str, prompt_wav: Path, prompt_text: str) -> None:
        self._voice_prompt_wav[voice_id] = Path(prompt_wav)
        self._voice_prompt_text[voice_id] = prompt_text

    def unregister_voice(self, voice_id: str) -> bool:
        if voice_id == settings.default_voice_id:
            return False
        gone = voice_id in self._voice_prompt_wav
        self._voice_prompt_wav.pop(voice_id, None)
        self._voice_prompt_text.pop(voice_id, None)
        return gone

    def _try_load_voice_dir(self, voice_id: str) -> bool:
        d = settings.root_dir / "voices" / voice_id
        wav = d / "prompt.wav"
        if not wav.exists():
            return False
        prompt_text = settings.prompt_text
        meta_path = d / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                prompt_text = meta.get("prompt_text") or prompt_text
            except Exception:  # noqa: BLE001
                pass
        self.register_voice(voice_id, prompt_wav=wav, prompt_text=prompt_text)
        return True

    def _resolve_voice(self, voice_id: Optional[str]) -> tuple[Path, str]:
        vid = voice_id or settings.default_voice_id
        if vid not in self._voice_prompt_wav:
            # 热路径：合成时按 voice_id 从磁盘懒加载，无需重启 Worker
            if not self._try_load_voice_dir(vid):
                logger.warning("voice_id=%s missing, fallback to %s", vid, settings.default_voice_id)
                vid = settings.default_voice_id
                if vid not in self._voice_prompt_wav:
                    self._try_load_voice_dir(vid)
        return self._voice_prompt_wav[vid], self._voice_prompt_text[vid]

    def synthesize_stream(
        self,
        text: str | Iterable[str],
        *,
        voice_id: Optional[str] = None,
        stream: bool = True,
        cancel_event: Optional[threading.Event] = None,
    ) -> Generator[bytes, None, None]:
        """Yield int16 little-endian PCM chunks."""
        if self._model is None:
            raise RuntimeError("TTS engine not loaded")

        prompt_wav, prompt_text = self._resolve_voice(voice_id)
        prompt_text_full = f"{settings.instruct_prefix}{prompt_text}"

        # CosyVoice inference is not fully thread-safe across concurrent GPU jobs on one process;
        # serialize model calls within this worker. Multi-GPU concurrency is via multiple workers.
        with self._lock:
            for chunk in self._model.inference_zero_shot(
                text,
                prompt_text_full,
                str(prompt_wav),
                stream=stream,
            ):
                if cancel_event is not None and cancel_event.is_set():
                    logger.info("TTS cancelled mid-stream")
                    break
                speech: torch.Tensor = chunk["tts_speech"]
                # speech shape: [1, T] float32 -1..1
                pcm = speech.detach().cpu().numpy().reshape(-1)
                pcm_i16 = np.clip(pcm * 32767.0, -32768, 32767).astype(np.int16)
                yield pcm_i16.tobytes()


engine = TTSEngine()

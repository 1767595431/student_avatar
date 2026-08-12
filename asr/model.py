"""FunASR model wrapper with queue-based multi-task inference."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import settings

logger = logging.getLogger("asr.model")


@dataclass
class TranscribeResult:
    request_id: str
    text: str
    duration_ms: int
    processing_ms: int


class ASREngine:
    def __init__(self) -> None:
        self._model = None
        self._executor = ThreadPoolExecutor(max_workers=settings.max_workers)
        self._sema = asyncio.Semaphore(settings.max_workers)
        self._load_lock = asyncio.Lock()

    def load(self) -> None:
        if self._model is not None:
            return
        from funasr import AutoModel

        logger.info(
            "Loading FunASR models: asr=%s vad=%s punc=%s device=%s",
            settings.asr_model,
            settings.vad_model,
            settings.punc_model,
            settings.device,
        )
        self._model = AutoModel(
            model=settings.asr_model,
            vad_model=settings.vad_model,
            # 与 68M 单段 ≤20s 对齐
            vad_kwargs={"max_single_segment_time": 20000},
            punc_model=settings.punc_model,
            device=settings.device,
            disable_update=True,
        )
        logger.info("FunASR models ready")

    async def ensure_loaded(self) -> None:
        if self._model is not None:
            return
        async with self._load_lock:
            if self._model is None:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(self._executor, self.load)

    def _infer_sync(self, wav_path: str) -> str:
        assert self._model is not None
        res: list[dict[str, Any]] = self._model.generate(input=wav_path, batch_size_s=300)
        if not res:
            return ""
        text = res[0].get("text", "") if isinstance(res[0], dict) else str(res[0])
        return (text or "").strip()

    async def transcribe(
        self,
        wav_path: Path,
        *,
        session_id: str,
        question_id: str,
        duration_s: float,
    ) -> TranscribeResult:
        await self.ensure_loaded()
        request_id = f"asr_{uuid.uuid4().hex[:12]}"
        started = time.perf_counter()

        try:
            await asyncio.wait_for(self._sema.acquire(), timeout=settings.queue_timeout_s)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("ASR_QUEUE_TIMEOUT") from exc

        try:
            loop = asyncio.get_running_loop()
            text = await asyncio.wait_for(
                loop.run_in_executor(self._executor, self._infer_sync, str(wav_path)),
                timeout=settings.inference_timeout_s,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError("ASR_TIMEOUT") from exc
        finally:
            self._sema.release()

        processing_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "ASR done request_id=%s session=%s question=%s processing_ms=%s text=%s",
            request_id,
            session_id,
            question_id,
            processing_ms,
            text[:80],
        )
        return TranscribeResult(
            request_id=request_id,
            text=text,
            duration_ms=int(duration_s * 1000),
            processing_ms=processing_ms,
        )


engine = ASREngine()

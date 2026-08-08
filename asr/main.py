"""学生端 ASR 独立服务：完整录音 → 最终文字（非流式）。"""
from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

from audio import AudioError, normalize_to_wav16k, probe_duration_seconds, validate_duration
from config import settings
from model import engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("asr.main")

app = FastAPI(title="Student ASR Service", version="1.0.0")


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Warming up FunASR on %s ...", settings.device)
    await engine.ensure_loaded()
    logger.info("ASR service ready on %s:%s", settings.host, settings.port)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "asr",
        "device": settings.device,
        "asr_model": settings.asr_model,
        "vad_model": settings.vad_model,
        "punc_model": settings.punc_model,
    }


@app.post("/internal/asr/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    session_id: str = Form(...),
    question_id: str = Form(...),
    language: str = Form("zh"),
):
    _ = language  # reserved
    raw_suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
    raw_path = settings.temp_dir / f"{uuid.uuid4().hex}{raw_suffix}"
    wav_path: Path | None = None

    try:
        content = await audio.read()
        if not content:
            return JSONResponse(
                status_code=400,
                content={"code": "AUDIO_EMPTY", "message": "empty audio", "request_id": None},
            )
        raw_path.write_bytes(content)

        try:
            wav_path = await normalize_to_wav16k(raw_path)
            duration_s = probe_duration_seconds(wav_path)
            validate_duration(duration_s)
        except AudioError as exc:
            return JSONResponse(
                status_code=400,
                content={"code": exc.code, "message": exc.message, "request_id": None},
            )

        try:
            result = await asyncio.wait_for(
                engine.transcribe(
                    wav_path,
                    session_id=session_id,
                    question_id=question_id,
                    duration_s=duration_s,
                ),
                timeout=settings.request_timeout_s,
            )
        except TimeoutError as exc:
            code = str(exc) if str(exc).startswith("ASR_") else "ASR_TIMEOUT"
            return JSONResponse(
                status_code=504,
                content={"code": code, "message": "speech recognition timeout", "request_id": None},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("ASR inference failed")
            return JSONResponse(
                status_code=500,
                content={
                    "code": "ASR_INFERENCE_FAILED",
                    "message": str(exc),
                    "request_id": None,
                },
            )

        return {
            "code": 0,
            "request_id": result.request_id,
            "session_id": session_id,
            "question_id": question_id,
            "text": result.text,
            "duration_ms": result.duration_ms,
            "processing_ms": result.processing_ms,
        }
    finally:
        if raw_path.exists():
            raw_path.unlink(missing_ok=True)
        if wav_path is not None and wav_path.exists():
            wav_path.unlink(missing_ok=True)


def main() -> None:
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    main()

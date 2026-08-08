"""Audio normalization helpers for ASR."""
from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from config import settings


class AudioError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


async def normalize_to_wav16k(src_path: Path, dst_path: Path | None = None) -> Path:
    """Convert arbitrary audio to 16kHz mono PCM WAV via ffmpeg."""
    if dst_path is None:
        import os

        fd, name = tempfile.mkstemp(suffix=".wav", dir=settings.temp_dir)
        os.close(fd)
        dst_path = Path(name)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(dst_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise AudioError("AUDIO_DECODE_FAILED", stderr.decode(errors="ignore")[-500:])
    return dst_path


def probe_duration_seconds(wav_path: Path) -> float:
    """Return audio duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(wav_path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
        return float(out)
    except Exception as exc:  # noqa: BLE001
        raise AudioError("AUDIO_DECODE_FAILED", f"ffprobe failed: {exc}") from exc


def validate_duration(duration_s: float) -> None:
    if duration_s * 1000 < settings.min_duration_ms:
        raise AudioError(
            "AUDIO_TOO_SHORT",
            f"audio shorter than {settings.min_duration_ms} ms",
        )
    if duration_s > settings.max_duration_s:
        raise AudioError(
            "AUDIO_TOO_LONG",
            f"audio longer than {settings.max_duration_s} s hard limit",
        )

"""进程内并发计数：会话/ASR/Dify/TTS 总控用。"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class ConcurrencyMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._asr = 0
        self._dify = 0
        self._tts = 0
        self._asr_peak = 0
        self._dify_peak = 0
        self._tts_peak = 0

    def _bump(self, name: str, delta: int) -> None:
        with self._lock:
            cur = getattr(self, f"_{name}") + delta
            if cur < 0:
                cur = 0
            setattr(self, f"_{name}", cur)
            peak = f"_{name}_peak"
            if cur > getattr(self, peak):
                setattr(self, peak, cur)

    @contextmanager
    def track_asr(self) -> Iterator[None]:
        self._bump("asr", 1)
        try:
            yield
        finally:
            self._bump("asr", -1)

    @contextmanager
    def track_dify(self) -> Iterator[None]:
        self._bump("dify", 1)
        try:
            yield
        finally:
            self._bump("dify", -1)

    @contextmanager
    def track_tts(self) -> Iterator[None]:
        self._bump("tts", 1)
        try:
            yield
        finally:
            self._bump("tts", -1)

    def enter_dify(self) -> None:
        self._bump("dify", 1)

    def leave_dify(self) -> None:
        self._bump("dify", -1)

    def enter_tts(self) -> None:
        self._bump("tts", 1)

    def leave_tts(self) -> None:
        self._bump("tts", -1)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "asr_active": self._asr,
                "dify_active": self._dify,
                "tts_active": self._tts,
                "asr_peak": self._asr_peak,
                "dify_peak": self._dify_peak,
                "tts_peak": self._tts_peak,
            }


meter = ConcurrencyMeter()

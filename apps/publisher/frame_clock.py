"""Continuous 25 FPS frame clock — PTS never resets on material loop."""
from __future__ import annotations

import time


class FrameClock:
    def __init__(self, fps: float = 25.0) -> None:
        self.fps = fps
        self.interval = 1.0 / fps
        self._index = 0
        self._start = time.monotonic()
        self._next_deadline = self._start

    def next(self) -> tuple[int, float]:
        """Return (output_frame_index, pts_seconds)."""
        idx = self._index
        pts = idx * self.interval
        self._index += 1
        self._next_deadline = self._start + self._index * self.interval
        return idx, pts

    def sleep_until_next(self) -> float:
        now = time.monotonic()
        delay = self._next_deadline - now
        if delay > 0:
            time.sleep(delay)
        else:
            # catch up without sleeping; avoid drift accumulation via deadline
            delay = 0.0
        return delay

    def resync(self) -> None:
        """欠载暂停后对齐时钟，避免 sleep_until_next 连发追帧造成听感卡顿。"""
        now = time.monotonic()
        self._start = now - self._index * self.interval
        self._next_deadline = now

    @property
    def frame_index(self) -> int:
        return self._index

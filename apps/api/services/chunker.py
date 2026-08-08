"""TTS text chunker — aggregate Dify tokens into speakable phrases."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional


STRONG = set("。！？；!?;\n")
WEAK = set("，、：,:")


class TextChunker:
    """Prefer early first speakable phrase (TTFA), then steadier later chunks."""

    def __init__(
        self,
        first_min_chars: int = 6,
        soft_min_chars: int = 8,
        target_chars: int = 14,
        hard_max_chars: int = 28,
        flush_wait_ms: int = 120,
    ) -> None:
        self.first_min_chars = first_min_chars
        self.soft_min_chars = soft_min_chars
        self.target_chars = target_chars
        self.hard_max_chars = hard_max_chars
        self.flush_wait_ms = flush_wait_ms

    async def chunk_stream(self, deltas: AsyncIterator[str]) -> AsyncIterator[str]:
        buf = ""
        emitted = 0
        agen = deltas.__aiter__()
        pending: Optional[asyncio.Task] = None

        async def _next() -> Optional[str]:
            try:
                return await agen.__anext__()
            except StopAsyncIteration:
                return None

        while True:
            timeout = (self.flush_wait_ms / 1000.0) if buf.strip() else None
            if pending is None:
                pending = asyncio.create_task(_next())
            done, _ = await asyncio.wait({pending}, timeout=timeout)
            if pending in done:
                delta = pending.result()
                pending = None
                if delta is None:
                    break
                buf += delta
            else:
                # timeout: flush first/early phrase to cut TTFA
                emit, buf = self._try_emit(buf, force=True, emitted=emitted)
                if emit:
                    emitted += 1
                    yield emit
                continue

            while True:
                emit, buf = self._try_emit(buf, force=False, emitted=emitted)
                if not emit:
                    break
                emitted += 1
                yield emit

        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except asyncio.CancelledError:
                pass

        if buf.strip():
            yield buf.strip()

    def _min_chars(self, emitted: int) -> int:
        return self.first_min_chars if emitted == 0 else self.soft_min_chars

    def _try_emit(
        self, buf: str, force: bool, emitted: int
    ) -> tuple[Optional[str], str]:
        if not buf:
            return None, buf
        min_chars = self._min_chars(emitted)
        # strong punctuation
        for i, ch in enumerate(buf):
            if ch in STRONG and i + 1 >= min_chars:
                return buf[: i + 1].strip(), buf[i + 1 :]
        # weak punctuation with enough length
        need = min_chars if emitted == 0 else self.target_chars
        for i, ch in enumerate(buf):
            if ch in WEAK and i + 1 >= need:
                return buf[: i + 1].strip(), buf[i + 1 :]
        if len(buf) >= self.hard_max_chars:
            for i in range(len(buf) - 1, min_chars - 1, -1):
                if buf[i] in WEAK or buf[i].isspace():
                    return buf[: i + 1].strip(), buf[i + 1 :]
            return buf[: self.hard_max_chars].strip(), buf[self.hard_max_chars :]
        if force and len(buf.strip()) >= min_chars:
            return buf.strip(), ""
        return None, buf

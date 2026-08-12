"""TTS text chunker — aggregate Dify tokens into speakable phrases.

克隆 TTS 对碎句敏感：千分位逗号、Markdown 标题冒号被切开后会胡讲/拖腔。
策略：优先整句（。！？），中文逗号仅在足够长时弱切；英文逗号不切。
"""
from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator, Optional


STRONG = set("。！？；!?;")
# 不含英文逗号 ,（千分位）；不含冒号（标题「人工智能：」单独合成会跑飞）
WEAK = set("，、")


def _soften_delta(delta: str) -> str:
    """流式轻清洗：换行当空格，去掉未闭合 markdown 噪音，避免强切碎标题。"""
    s = (delta or "").replace("\r", "\n")
    s = s.replace("\n", " ")
    s = re.sub(r"[#>*`]+", "", s)
    s = re.sub(r"\*{1,3}", "", s)
    s = re.sub(r"_{1,3}", "", s)
    return s


class TextChunker:
    """首句尽快开口，之后拼成更稳的口播句（降 vLLM 句间 TTFT 卡顿）。"""

    def __init__(
        self,
        first_min_chars: int = 12,
        soft_min_chars: int = 18,
        target_chars: int = 40,
        hard_max_chars: int = 72,
        flush_wait_ms: int = 220,
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
                buf += _soften_delta(delta)
                buf = re.sub(r" {2,}", " ", buf)
            else:
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

    @staticmethod
    def _bad_cut_tail(piece: str) -> bool:
        """避免以冒号/顿号标题或数字千分位尾巴单独成句。"""
        t = piece.rstrip()
        if not t:
            return True
        if t.endswith(("：", ":", "、")) and len(t) < 24:
            return True
        # 「16,」这类（若上游仍带入英文逗号）
        if re.search(r"\d,$", t):
            return True
        return False

    def _try_emit(
        self, buf: str, force: bool, emitted: int
    ) -> tuple[Optional[str], str]:
        if not buf:
            return None, buf
        min_chars = self._min_chars(emitted)
        for i, ch in enumerate(buf):
            if ch in STRONG and i + 1 >= min_chars:
                piece = buf[: i + 1].strip()
                if piece and not self._bad_cut_tail(piece):
                    return piece, buf[i + 1 :]
        need = min_chars if emitted == 0 else self.target_chars
        for i, ch in enumerate(buf):
            if ch in WEAK and i + 1 >= need:
                piece = buf[: i + 1].strip()
                if piece and not self._bad_cut_tail(piece):
                    return piece, buf[i + 1 :]
        if len(buf) >= self.hard_max_chars:
            for i in range(len(buf) - 1, min_chars - 1, -1):
                if buf[i] in STRONG or buf[i] in WEAK or buf[i].isspace():
                    piece = buf[: i + 1].strip()
                    if piece and not self._bad_cut_tail(piece):
                        return piece, buf[i + 1 :]
            # 硬切也避开「数字,」中间
            cut = self.hard_max_chars
            while cut > min_chars and buf[cut - 1].isdigit():
                cut -= 1
            return buf[:cut].strip(), buf[cut:]
        if force and len(buf.strip()) >= min_chars:
            piece = buf.strip()
            # 超时强制：若以冒号结尾则再等等（除非已经很长）
            if self._bad_cut_tail(piece) and len(piece) < self.target_chars:
                return None, buf
            return piece, ""
        return None, buf

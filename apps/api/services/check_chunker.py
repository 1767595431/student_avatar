"""chunker first-flush self-check (no framework)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chunker import TextChunker  # noqa: E402


async def _gen():
    for p in ["你好", "，", "同学们", "。", "今天", "我们", "学习", "函数", "。"]:
        await asyncio.sleep(0.05)
        yield p


async def main() -> None:
    c = TextChunker(first_min_chars=6, flush_wait_ms=80)
    parts = []
    async for x in c.chunk_stream(_gen()):
        parts.append(x)
    assert parts, "no chunks"
    assert len(parts[0]) >= 6, parts
    # first chunk should arrive before full answer assembled
    assert "。" in "".join(parts)
    print("OK chunks=", parts)


if __name__ == "__main__":
    asyncio.run(main())

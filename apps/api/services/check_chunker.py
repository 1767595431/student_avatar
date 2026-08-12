"""chunker self-check（防千分位/标题碎句）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chunker import TextChunker  # noqa: E402


async def _gen(parts: list[str], delay: float = 0.02):
    for p in parts:
        await asyncio.sleep(delay)
        yield p


async def main() -> None:
    c = TextChunker()
    parts: list[str] = []
    async for x in c.chunk_stream(
        _gen(["你好", "，", "同学们", "。", "今天", "我们", "学习", "函数", "。"])
    ):
        parts.append(x)
    assert parts, "no chunks"
    assert any("。" in p for p in parts), parts

    # 千分位与标题：不应拆出「16,」或单独「人工智能：」
    blob = (
        "**人工智能**：\n"
        "面积：行政辖区总面积为16,410平方公里。"
        "地势西北高、东南低。"
    )
    out: list[str] = []
    async for x in c.chunk_stream(_gen([blob[i : i + 3] for i in range(0, len(blob), 3)])):
        out.append(x)
    joined = "|".join(out)
    assert "16," not in joined.replace("16,410", ""), out
    assert not any(p.strip() in ("人工智能：", "人工智能:") for p in out), out
    assert any("16410" in p.replace(",", "") or "16,410" in p for p in out), out
    print("OK chunks=", out)


if __name__ == "__main__":
    asyncio.run(main())

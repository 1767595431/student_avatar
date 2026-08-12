"""ponytail: 确认流式分句 + 口播清洗可串起来（不启服务）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API))

from services.chunker import TextChunker  # noqa: E402


async def _deltas():
    for x in ["你好", "，", "今天", "天气", "不错", "。", "我们", "开始", "上课", "吧", "！"]:
        yield x
        await asyncio.sleep(0)


async def main() -> None:
    # lazy import：避免无 livekit 时挂掉
    import importlib.util

    main_py = API / "main.py"
    spec = importlib.util.spec_from_file_location("_api_main_plain", main_py)
    assert spec and spec.loader
    # 只测 chunker + 本地清洗逻辑副本
    import re

    def plain(text: str) -> str:
        s = (text or "").strip()
        s = re.sub(r"\s+", " ", s).strip()
        return s

    out = []
    async for p in TextChunker().chunk_stream(_deltas()):
        c = plain(p)
        if c:
            out.append(c)
    assert out, out
    assert any("。" in x or "！" in x for x in out), out
    print("ok", out)


if __name__ == "__main__":
    asyncio.run(main())

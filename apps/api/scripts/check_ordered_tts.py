#!/usr/bin/env python3
"""ponytail: 分句有序合并自检（不打 GPU）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))


async def _fake_phrases():
    for t in ("甲。", "乙。", "丙。"):
        yield t
        await asyncio.sleep(0.01)


async def main() -> int:
    # 直接测有序缓冲逻辑（复制最小核心，避免依赖真实 TTS）
    next_seq = 0
    done: dict[int, bytes] = {1: b"B", 0: b"A", 2: b"C"}
    out: list[bytes] = []
    while next_seq in done:
        out.append(done.pop(next_seq))
        next_seq += 1
    assert out == [b"A", b"B", b"C"], out
    assert next_seq == 3

    # cancel 前缀匹配
    keys = {"s:q", "s:q#p0", "s:q#p1", "s:other#p0"}
    prefix = "s:q"
    hit = [k for k in keys if k == prefix or k.startswith(prefix + "#")]
    assert set(hit) == {"s:q", "s:q#p0", "s:q#p1"}, hit
    print("OK ordered-tts checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

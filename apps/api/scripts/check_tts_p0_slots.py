#!/usr/bin/env python3
"""ponytail: P0 槽位优先自检（无 GPU）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from config import settings  # noqa: E402
from services import speech_clients as sc  # noqa: E402


async def main() -> int:
    # 强制小槽便于测
    settings.max_tts_active_jobs = 3
    settings.tts_p0_reserved_slots = 2
    order: list[str] = []

    async def job(name: str, prio: int, hold: float) -> None:
        await sc._slot_acquire(prio)
        order.append(f"enter:{name}")
        await asyncio.sleep(hold)
        order.append(f"leave:{name}")
        await sc._slot_release()

    # 先占满 3 个普通槽，再让 P0 与普通同时等；释放后 P0 应优先进入预留逻辑
    t1 = asyncio.create_task(job("a", 1, 0.15))
    t2 = asyncio.create_task(job("b", 1, 0.15))
    t3 = asyncio.create_task(job("c", 1, 0.15))
    await asyncio.sleep(0.02)
    tp0 = asyncio.create_task(job("p0", 0, 0.05))
    t4 = asyncio.create_task(job("d", 1, 0.05))
    await asyncio.gather(t1, t2, t3, tp0, t4)
    # p0 必须在 d 之前 enter（有预留）
    enters = [x for x in order if x.startswith("enter:")]
    assert "enter:p0" in enters and "enter:d" in enters
    assert enters.index("enter:p0") < enters.index("enter:d"), enters
    print("ok", enters)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

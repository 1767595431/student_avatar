#!/usr/bin/env python3
"""Self-check: ensure vs reap_if cannot kill a freshly ensured publisher."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "publisher"))

from publisher import PublisherPool, SessionPublisher  # noqa: E402


async def main() -> None:
    pool = PublisherPool()
    sid = "sess_race"

    # fake running publisher
    old = SessionPublisher.__new__(SessionPublisher)
    old.session_id = sid
    old._running = True
    old.stop = AsyncMock()
    pool._pubs[sid] = old

    started = asyncio.Event()
    release_hold = asyncio.Event()

    async def slow_stop() -> None:
        started.set()
        await release_hold.wait()

    old.stop = slow_stop

    async def reaper() -> bool:
        return await pool.reap_if(sid, lambda: True)

    reap_task = asyncio.create_task(reaper())
    await started.wait()

    # while reaper holds lock inside stop, ensure must wait — then recreate
    new = SessionPublisher.__new__(SessionPublisher)
    new.session_id = sid
    new._running = True
    new.stop = AsyncMock()
    new.start = AsyncMock()

    real_ensure = pool.ensure

    async def ensure_stub(**kwargs):  # noqa: ANN003
        async with pool._lock_for(sid):
            # after reaper finishes, pubs empty
            assert sid not in pool._pubs or pool._pubs.get(sid) is not old
            await new.start()
            pool._pubs[sid] = new
            return new

    pool.ensure = ensure_stub  # type: ignore[method-assign]
    ens_task = asyncio.create_task(
        pool.ensure(
            session_id=sid,
            avatar_package_dir=Path("."),
            room_name="r",
            livekit_url="ws://x",
            api_key="k",
            api_secret="s",
        )
    )
    await asyncio.sleep(0.05)
    assert not ens_task.done(), "ensure must wait for reaper lock"
    release_hold.set()
    assert await reap_task is True
    got = await ens_task
    assert got is new
    assert pool.get(sid) is new
    assert pool.get(sid) is not old
    print("ok: reaper/ensure lock — new publisher survives")


if __name__ == "__main__":
    asyncio.run(main())

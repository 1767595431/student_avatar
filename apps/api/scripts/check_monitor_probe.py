"""ponytail: monitor probe returns LiveKit + ASR targets."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.monitor_probe import probe_services  # noqa: E402


async def main() -> None:
    rows = await probe_services(force=True)
    assert rows, "empty probe"
    ids = {r["id"] for r in rows}
    assert "livekit" in ids
    assert any(i.startswith("asr_") for i in ids)
    print("ok probe", len(rows), "services")


if __name__ == "__main__":
    asyncio.run(main())

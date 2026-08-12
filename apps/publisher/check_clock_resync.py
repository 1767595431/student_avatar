"""ponytail: FrameClock.resync 自检。"""
from __future__ import annotations

import time

from frame_clock import FrameClock


def main() -> None:
    c = FrameClock(fps=25.0)
    for _ in range(5):
        c.next()
    c.resync()
    # resync 后下一次 sleep 不应因旧 deadline 立刻返回 0 并连发
    t0 = time.monotonic()
    c.next()
    c.sleep_until_next()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.02, elapsed
    print("ok", round(elapsed, 3))


if __name__ == "__main__":
    main()

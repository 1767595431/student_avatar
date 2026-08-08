"""generation 丢弃旧 PCM — P2 最小自检（无框架）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "publisher"))

from publisher import SessionPublisher  # noqa: E402


def main() -> None:
    # 不连 LiveKit，只测队列/generation 逻辑
    pub = object.__new__(SessionPublisher)
    from collections import deque
    import threading

    pub._pcm_queue = deque()
    pub._pcm_lock = threading.Lock()
    pub.generation = 0

    g1 = pub.bump_generation()
    pub.push_pcm(b"AAAA", generation=g1)
    assert len(pub._pcm_queue) == 1

    g2 = pub.bump_generation()
    assert g2 == g1 + 1
    assert len(pub._pcm_queue) == 0  # bump 清空

    pub.push_pcm(b"OLD", generation=g1)  # 旧 generation 丢弃
    assert len(pub._pcm_queue) == 0
    pub.push_pcm(b"NEW", generation=g2)
    assert len(pub._pcm_queue) == 1
    pub.clear_pcm()
    assert len(pub._pcm_queue) == 0
    print("OK generation discard")


if __name__ == "__main__":
    main()

"""ponytail: 并发计数器自检。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.concurrency import ConcurrencyMeter  # noqa: E402


def main() -> None:
    m = ConcurrencyMeter()
    with m.track_asr():
        assert m.snapshot()["asr_active"] == 1
    assert m.snapshot()["asr_active"] == 0
    m.enter_dify()
    m.enter_tts()
    m.enter_tts()
    s = m.snapshot()
    assert s["dify_active"] == 1 and s["tts_active"] == 2 and s["tts_peak"] == 2
    m.leave_tts()
    m.leave_tts()
    m.leave_dify()
    assert m.snapshot()["tts_active"] == 0
    print("ok")


if __name__ == "__main__":
    main()

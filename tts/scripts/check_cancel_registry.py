#!/usr/bin/env python3
"""Self-check: cancel registry signals active job."""
from __future__ import annotations

import threading
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Import only the helpers by executing light stubs — avoid loading torch via engine.
import main as tts_main  # noqa: E402


def main() -> None:
    key = tts_main._job_key("sess_a", "q_b")
    ev = threading.Event()
    with tts_main._active_lock:
        tts_main._active_jobs[key] = ev
    assert tts_main._physical_device().startswith("cuda:")
    # simulate cancel endpoint body
    import asyncio

    async def run() -> None:
        r = await tts_main.cancel_job({"session_id": "sess_a", "question_id": "q_b"})
        assert r["cancelled"] is True
        assert ev.is_set()

    asyncio.run(run())
    with tts_main._active_lock:
        tts_main._active_jobs.pop(key, None)
    print("ok: cancel registry + physical_device")


if __name__ == "__main__":
    main()

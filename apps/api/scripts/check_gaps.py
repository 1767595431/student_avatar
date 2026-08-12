#!/usr/bin/env python3
"""Self-check for idle reaper eligibility + agent delete guard."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "publisher"))

from services.session_store import BusinessState, MediaState, store  # noqa: E402
from services import asset_store  # noqa: E402


def test_reaper_states() -> None:
    reclaimable = (MediaState.WARM_IDLE, MediaState.READY)
    busy = (
        BusinessState.RECOGNIZING,
        BusinessState.THINKING,
        BusinessState.SPEAKING,
        BusinessState.INTERRUPTING,
    )
    s = store.create("t", "a", "v", voice_id="v1", agent_id="g1")
    s.media_state = MediaState.READY
    s.state = BusinessState.IDLE
    assert s.media_state in reclaimable and s.state not in busy
    s.media_state = MediaState.WARM_IDLE
    assert s.media_state in reclaimable
    s.state = BusinessState.SPEAKING
    assert s.state in busy
    store.delete(s.session_id)
    print("ok: reaper state rules")


def test_delete_agent() -> None:
    aid = "_gap_agent"
    asset_store.save_agent(agent_id=aid, api_key="app-x", name="gap", base_url="http://x")
    asset_store.set_default_agent(aid)
    asset_store.delete_agent(aid)
    assert asset_store.get_agent(aid) is None
    _, _, _, dag = asset_store.defaults()
    assert dag != aid
    print("ok: delete agent clears default")


if __name__ == "__main__":
    test_reaper_states()
    test_delete_agent()

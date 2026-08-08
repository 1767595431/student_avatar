#!/usr/bin/env python3
"""ponytail: smoke for asset_store + preprocess helper (no GPU)."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "apps" / "publisher"))

from services import asset_store  # noqa: E402


def main() -> int:
    voices = asset_store.list_voices()
    assert isinstance(voices, list), voices
    avatars = asset_store.list_avatars()
    assert isinstance(avatars, list), avatars
    da, dv, dvoice, dag = asset_store.defaults()
    assert da and dv and dvoice
    # voice roundtrip with tiny wav header + silence-ish bytes (not playable needed for store)
    wav = Path(ROOT / "tts" / "voices" / "default_prompt.wav")
    assert wav.exists(), "default_prompt.wav missing"
    meta = asset_store.save_voice(
        voice_id="_smoke_voice",
        wav_bytes=wav.read_bytes(),
        prompt_text="smoke",
        name="smoke",
    )
    assert meta["voice_id"] == "_smoke_voice"
    ids = {v["voice_id"] for v in asset_store.list_voices()}
    assert "_smoke_voice" in ids
    # cleanup smoke voice dirs
    import shutil

    shutil.rmtree(asset_store.VOICE_ROOT / "_smoke_voice", ignore_errors=True)
    shutil.rmtree(asset_store.TTS_VOICE_ROOT / "_smoke_voice", ignore_errors=True)
    print(json.dumps({"ok": True, "defaults": [da, dv, dvoice, dag], "avatars": len(avatars), "voices": len(voices)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    # voice roundtrip；上传侧会 ffmpeg 归一成真 PCM WAV
    wav = Path(ROOT / "data" / "voices" / "voice_aacd9fa8" / "prompt.wav")
    if not wav.exists():
        wav = Path(ROOT / "data" / "voices" / "default_prompt.wav")
    assert wav.exists(), "no sample prompt.wav under data/voices"
    norm = asset_store.normalize_prompt_wav(wav.read_bytes(), suffix=".wav")
    assert norm[:4] == b"RIFF" and norm[8:12] == b"WAVE"
    meta = asset_store.save_voice(
        voice_id="_smoke_voice",
        wav_bytes=wav.read_bytes(),
        prompt_text="smoke",
        name="smoke",
    )
    assert (asset_store.VOICE_ROOT / "_smoke_voice" / "prompt.wav").read_bytes()[:4] == b"RIFF"
    assert meta["voice_id"] == "_smoke_voice"
    ids = {v["voice_id"] for v in asset_store.list_voices()}
    assert "_smoke_voice" in ids
    import shutil

    shutil.rmtree(asset_store.VOICE_ROOT / "_smoke_voice", ignore_errors=True)
    print(json.dumps({"ok": True, "defaults": [da, dv, dvoice, dag], "avatars": len(avatars), "voices": len(voices)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

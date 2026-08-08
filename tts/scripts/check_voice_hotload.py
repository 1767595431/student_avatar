#!/usr/bin/env python3
"""Self-check: lazy resolve loads voice from disk without full reload."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import TTSEngine  # noqa: E402


def main() -> None:
    eng = TTSEngine.__new__(TTSEngine)
    eng._model = object()  # type: ignore[attr-defined]
    eng._voice_prompt_wav = {}  # type: ignore[attr-defined]
    eng._voice_prompt_text = {}  # type: ignore[attr-defined]
    eng._lock = __import__("threading").Lock()  # type: ignore[attr-defined]

    tmp = Path(tempfile.mkdtemp(prefix="tts_voice_hot_"))
    try:
        # monkeypatch voices root via writing under settings.root_dir/voices — use real tree
        from config import settings

        vid = "_hotload_check"
        dest = settings.root_dir / "voices" / vid
        dest.mkdir(parents=True, exist_ok=True)
        wav = dest / "prompt.wav"
        # minimal wav header + silence not needed; file only needs to exist for resolve
        wav.write_bytes(b"RIFF" + b"\x00" * 40)
        (dest / "meta.json").write_text(
            json.dumps({"voice_id": vid, "prompt_text": "测试热加载"}, ensure_ascii=False),
            encoding="utf-8",
        )
        assert vid not in eng._voice_prompt_wav
        path, text = eng._resolve_voice(vid)
        assert path == wav, path
        assert "热加载" in text
        assert vid in eng.list_voices()
        assert eng.unregister_voice(vid) is True
        assert vid not in eng.list_voices()
        print("ok: lazy voice resolve + unregister")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        dest = ROOT / "voices" / "_hotload_check"
        shutil.rmtree(dest, ignore_errors=True)


if __name__ == "__main__":
    main()

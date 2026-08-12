"""ponytail: ASR multi-base pick must round-robin at equal load."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import asr_http_bases  # noqa: E402
from services import speech_clients as sc  # noqa: E402


def main() -> None:
    bases = asr_http_bases()
    assert len(bases) >= 1
    a = sc._pick_asr_base()
    b = sc._pick_asr_base()
    sc._release_asr_base(a)
    sc._release_asr_base(b)
    if len(bases) >= 2:
        assert a != b, (a, b)
    print("ok asr bases", bases)


if __name__ == "__main__":
    main()

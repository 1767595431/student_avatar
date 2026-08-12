#!/usr/bin/env python3
"""Assert publish scale + bitrate for multi-session (no LiveKit connect)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publisher import (  # noqa: E402
    PUBLISH_BITRATE,
    PUBLISH_MAX_SHORT,
    publish_dims,
    video_publish_options,
)
from livekit import rtc  # noqa: E402


def main() -> None:
    w, h = publish_dims(1080, 1896)
    assert min(w, h) == PUBLISH_MAX_SHORT, (w, h)
    assert w % 2 == 0 and h % 2 == 0
    # 1080→540 ⇒ 1896→948
    assert (w, h) == (540, 948), (w, h)

    opts = video_publish_options(w, h)
    assert opts.simulcast is False
    assert opts.source == rtc.TrackSource.SOURCE_SCREENSHARE
    br = int(opts.video_encoding.max_bitrate)
    assert br == PUBLISH_BITRATE == 1_000_000, br
    dp = getattr(opts, "degradation_preference", None)
    if hasattr(rtc, "DegradationPreference"):
        assert dp == rtc.DegradationPreference.MAINTAIN_RESOLUTION, dp
    print(f"ok pub={w}x{h} bitrate={br} source={opts.source} degradation={dp}")


if __name__ == "__main__":
    main()

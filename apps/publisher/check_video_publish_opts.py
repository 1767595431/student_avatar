#!/usr/bin/env python3
"""Assert video publish options target high start quality (no LiveKit connect)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publisher import video_publish_options  # noqa: E402
from livekit import rtc  # noqa: E402


def main() -> None:
    opts = video_publish_options(1080, 1896)
    assert opts.simulcast is False
    assert opts.source == rtc.TrackSource.SOURCE_SCREENSHARE
    br = int(opts.video_encoding.max_bitrate)
    assert br >= 6_000_000, br
    assert br <= 12_000_000, br
    dp = getattr(opts, "degradation_preference", None)
    # livekit>=1.x should set MAINTAIN_RESOLUTION
    if hasattr(rtc, "DegradationPreference"):
        assert dp == rtc.DegradationPreference.MAINTAIN_RESOLUTION, dp
    print(f"ok bitrate={br} source={opts.source} degradation={dp}")


if __name__ == "__main__":
    main()

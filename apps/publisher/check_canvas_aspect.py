"""ponytail: keep source resolution, cap at 1080p box."""
from __future__ import annotations

from avatar_preprocess import target_canvas


def main() -> None:
    assert target_canvas(1920, 1080) == (1920, 1080)
    assert target_canvas(1280, 720) == (1280, 720)
    assert target_canvas(1080, 1896) == (1080, 1896)
    assert target_canvas(720, 1280) == (720, 1280)
    # 4K landscape → 1080p
    assert target_canvas(3840, 2160) == (1920, 1080)
    # 超高竖屏 → 短边 1080、长边 ≤1920
    assert target_canvas(1440, 2560) == (1080, 1920)
    print("ok canvas keep-res ≤1080p")


if __name__ == "__main__":
    main()

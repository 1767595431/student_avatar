#!/usr/bin/env python3
"""Self-check: frame index hot-reload on cached AvatarPackage."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avatar_frame_controller import AvatarPackage  # noqa: E402


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="avpkg_"))
    try:
        frames = tmp / "frames"
        frames.mkdir()
        for i in range(5):
            img = np.zeros((16, 16, 3), dtype=np.uint8)
            img[:] = (i * 40, 10, 10)
            cv2.imwrite(str(frames / f"frame_{i:05d}.png"), img)
        man = {
            "width": 16,
            "height": 16,
            "fps": 25,
            "idle_frame": 0,
            "talk_start": 1,
            "talk_end": 3,
            "transition_frames": 2,
            "frame_count": 5,
        }
        (tmp / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        pkg = AvatarPackage.get_shared(tmp)
        assert pkg.idle_frame_idx == 0
        man["idle_frame"] = 2
        man["talk_start"] = 2
        man["talk_end"] = 4
        (tmp / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        AvatarPackage.apply_frame_update(tmp)
        assert pkg.idle_frame_idx == 2
        assert pkg.talk_start == 2 and pkg.talk_end == 4
        print("ok: apply_frame_update hot reload")
    finally:
        AvatarPackage.invalidate(tmp)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

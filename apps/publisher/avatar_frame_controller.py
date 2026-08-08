"""Avatar frame controller: IDLE / TRANSITION / TALKING without seeking MP4."""
from __future__ import annotations

import json
import logging
import threading
from enum import Enum
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from crossfade import crossfade_frames

logger = logging.getLogger("publisher.avatar")

# P3: share decoded frames across publishers of the same package path
_package_cache: dict[str, "AvatarPackage"] = {}
_package_lock = threading.Lock()


class AvatarState(str, Enum):
    IDLE = "IDLE"
    TRANSITION = "TRANSITION"
    TALKING = "TALKING"


class AvatarPackage:
    def __init__(self, package_dir: Path, *, _skip_cache: bool = False) -> None:
        self.package_dir = Path(package_dir)
        manifest_path = self.package_dir / "manifest.json"
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.fps = int(self.manifest.get("fps", 25))
        self.width = int(self.manifest.get("width", 1280))
        self.height = int(self.manifest.get("height", 720))
        self.idle_frame_idx = int(self.manifest["idle_frame"])
        self.talk_start = int(self.manifest["talk_start"])
        self.talk_end = int(self.manifest["talk_end"])
        self.transition_frames = int(self.manifest.get("transition_frames", 4))
        self.frames: list[np.ndarray] = []
        self._load_frames()

    @classmethod
    def get_shared(cls, package_dir: Path) -> "AvatarPackage":
        key = str(Path(package_dir).resolve())
        with _package_lock:
            pkg = _package_cache.get(key)
            if pkg is None:
                pkg = cls(Path(package_dir))
                _package_cache[key] = pkg
                logger.info("AvatarPackage cached key=%s", key)
            return pkg

    @classmethod
    def invalidate(cls, package_dir: Path) -> None:
        key = str(Path(package_dir).resolve())
        with _package_lock:
            if _package_cache.pop(key, None) is not None:
                logger.info("AvatarPackage cache invalidated key=%s", key)

    @classmethod
    def apply_frame_update(cls, package_dir: Path) -> None:
        """Reload idle/talk indices into any live cached package (in-place)."""
        key = str(Path(package_dir).resolve())
        with _package_lock:
            pkg = _package_cache.get(key)
            if pkg is not None:
                pkg.reload_manifest_indices()
                logger.info(
                    "AvatarPackage indices refreshed key=%s idle=%s talk=%s-%s",
                    key,
                    pkg.idle_frame_idx,
                    pkg.talk_start,
                    pkg.talk_end,
                )

    def reload_manifest_indices(self) -> None:
        """Hot-apply idle/talk indices from disk without reloading all PNGs."""
        man = json.loads((self.package_dir / "manifest.json").read_text(encoding="utf-8"))
        self.manifest = man
        n = max(0, len(self.frames) - 1)
        self.idle_frame_idx = min(max(0, int(man.get("idle_frame", 0))), n)
        self.talk_start = min(max(0, int(man.get("talk_start", 0))), n)
        self.talk_end = min(max(self.talk_start, int(man.get("talk_end", n))), n)
        self.transition_frames = int(man.get("transition_frames", 4))

    def _load_frames(self) -> None:
        frames_dir = self.package_dir / "frames"
        if frames_dir.exists():
            paths = sorted(frames_dir.glob("frame_*.png"))
            for p in paths:
                img = cv2.imread(str(p))
                if img is None:
                    continue
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                if img.shape[0] != self.height or img.shape[1] != self.width:
                    img = cv2.resize(img, (self.width, self.height))
                self.frames.append(img)
        else:
            # fallback: decode mp4 once
            video = self.package_dir / self.manifest.get("source", "avatar.mp4")
            cap = cv2.VideoCapture(str(video))
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                if frame.shape[0] != self.height or frame.shape[1] != self.width:
                    frame = cv2.resize(frame, (self.width, self.height))
                self.frames.append(frame)
            cap.release()
        if not self.frames:
            raise RuntimeError(f"no frames in avatar package: {self.package_dir}")
        # clamp indices
        n = len(self.frames) - 1
        self.idle_frame_idx = min(max(0, self.idle_frame_idx), n)
        self.talk_start = min(max(0, self.talk_start), n)
        self.talk_end = min(max(self.talk_start, self.talk_end), n)
        logger.info(
            "Avatar loaded %s frames=%s idle=%s talk=%s-%s",
            self.package_dir,
            len(self.frames),
            self.idle_frame_idx,
            self.talk_start,
            self.talk_end,
        )

    @property
    def idle_frame(self) -> np.ndarray:
        return self.frames[self.idle_frame_idx]


class AvatarFrameController:
    def __init__(self, package: AvatarPackage) -> None:
        self.package = package
        self.state = AvatarState.IDLE
        self.talk_index = package.talk_start
        self._transition: list[np.ndarray] = []
        self._transition_i = 0
        self.video_track_recreate_count = 0
        self.video_pts_discontinuity_count = 0
        self.switch_count = 0

    def start_talking(self) -> None:
        if self.state == AvatarState.TALKING:
            return
        idle = self.package.idle_frame
        talk0 = self.package.frames[self.package.talk_start]
        self._transition = crossfade_frames(idle, talk0, self.package.transition_frames)
        self._transition_i = 0
        self.state = AvatarState.TRANSITION
        self.talk_index = self.package.talk_start
        self.switch_count += 1

    def stop_talking(self) -> None:
        if self.state == AvatarState.IDLE:
            return
        cur = self.current_source_frame()
        idle = self.package.idle_frame
        self._transition = crossfade_frames(cur, idle, self.package.transition_frames)
        self._transition_i = 0
        self.state = AvatarState.TRANSITION
        self.switch_count += 1
        # after transition ends -> IDLE

    def current_source_frame(self) -> np.ndarray:
        if self.state == AvatarState.IDLE:
            return self.package.idle_frame
        if self.state == AvatarState.TRANSITION and self._transition:
            i = min(self._transition_i, len(self._transition) - 1)
            return self._transition[i]
        return self.package.frames[self.talk_index]

    def next_frame(self) -> np.ndarray:
        if self.state == AvatarState.IDLE:
            return self.package.idle_frame.copy()

        if self.state == AvatarState.TRANSITION:
            if self._transition_i < len(self._transition):
                frame = self._transition[self._transition_i]
                self._transition_i += 1
                if self._transition_i >= len(self._transition):
                    # decide destination
                    # if last transition was toward talk_start (coming from start_talking)
                    # heuristic: if talk_index == talk_start and we just finished fade from idle -> talking
                    if np.allclose(
                        self._transition[-1].astype(np.float32),
                        self.package.frames[self.package.talk_start].astype(np.float32),
                        atol=2,
                    ):
                        self.state = AvatarState.TALKING
                        self.talk_index = self.package.talk_start
                    else:
                        self.state = AvatarState.IDLE
                return frame.copy()
            self.state = AvatarState.IDLE
            return self.package.idle_frame.copy()

        # TALKING
        frame = self.package.frames[self.talk_index].copy()
        self.talk_index += 1
        if self.talk_index > self.package.talk_end:
            # loop with crossfade
            end_frame = self.package.frames[self.package.talk_end]
            start_frame = self.package.frames[self.package.talk_start]
            self._transition = crossfade_frames(
                end_frame, start_frame, max(2, self.package.transition_frames)
            )
            self._transition_i = 0
            self.state = AvatarState.TRANSITION
            self.talk_index = self.package.talk_start
        return frame

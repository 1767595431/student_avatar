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
        self.idle_frames: list[np.ndarray] = []
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

    def _read_png_dir(self, frames_dir: Path) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        if not frames_dir.exists():
            return out
        for p in sorted(frames_dir.glob("frame_*.png")):
            img = cv2.imread(str(p))
            if img is None:
                continue
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            if img.shape[0] != self.height or img.shape[1] != self.width:
                img = cv2.resize(img, (self.width, self.height))
            out.append(img)
        return out

    def _load_frames(self) -> None:
        frames_dir = self.package_dir / "frames"
        idle_dir = self.package_dir / "idle_frames"
        self.frames = self._read_png_dir(frames_dir)
        self.idle_frames = self._read_png_dir(idle_dir)
        if not self.frames:
            # fallback: decode talk/source mp4 once
            video = self.package_dir / self.manifest.get(
                "talk_video", self.manifest.get("source", "avatar.mp4")
            )
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
        if not self.idle_frames:
            # 单视频包：待机用 idle_frame 单帧（或旧逻辑）
            idx = min(max(0, self.idle_frame_idx), len(self.frames) - 1)
            self.idle_frames = [self.frames[idx]]
        # clamp talk indices
        n = len(self.frames) - 1
        self.idle_frame_idx = min(max(0, self.idle_frame_idx), max(0, len(self.idle_frames) - 1))
        self.talk_start = min(max(0, self.talk_start), n)
        self.talk_end = min(max(self.talk_start, self.talk_end), n)
        logger.info(
            "Avatar loaded %s talk_frames=%s idle_frames=%s talk=%s-%s",
            self.package_dir,
            len(self.frames),
            len(self.idle_frames),
            self.talk_start,
            self.talk_end,
        )

    @property
    def idle_frame(self) -> np.ndarray:
        i = min(max(0, self.idle_frame_idx), len(self.idle_frames) - 1)
        return self.idle_frames[i]


class AvatarFrameController:
    def __init__(self, package: AvatarPackage) -> None:
        self.package = package
        self.state = AvatarState.IDLE
        self.talk_index = package.talk_start
        self._transition: list[np.ndarray] = []
        self._transition_i = 0
        self._idle_i = 0
        self._idle_dir = 1  # +1 正放 / -1 倒放
        self._talk_dir = 1  # 动嘴同理：音频播完前正放↔倒放
        self._trans_to_talk = False
        self.video_track_recreate_count = 0
        self.video_pts_discontinuity_count = 0
        self.switch_count = 0

    def _next_idle_frame(self) -> np.ndarray:
        frames = self.package.idle_frames
        if not frames:
            return self.package.idle_frame.copy()
        if len(frames) == 1:
            return frames[0].copy()
        i = min(max(0, self._idle_i), len(frames) - 1)
        frame = frames[i].copy()
        nxt = i + self._idle_dir
        if nxt >= len(frames):
            self._idle_dir = -1
            self._idle_i = len(frames) - 2
        elif nxt < 0:
            self._idle_dir = 1
            self._idle_i = 1
        else:
            self._idle_i = nxt
        return frame

    def _next_talk_frame(self) -> np.ndarray:
        """动嘴片段正放倒放循环，直到 stop_talking（音频播完）。"""
        start = self.package.talk_start
        end = self.package.talk_end
        frames = self.package.frames
        if start >= end:
            return frames[start].copy()
        i = min(max(self.talk_index, start), end)
        frame = frames[i].copy()
        nxt = i + self._talk_dir
        if nxt > end:
            self._talk_dir = -1
            self.talk_index = end - 1
        elif nxt < start:
            self._talk_dir = 1
            self.talk_index = start + 1
        else:
            self.talk_index = nxt
        return frame

    def start_talking(self) -> None:
        if self.state == AvatarState.TALKING:
            return
        # 从当前正放/倒放位置交叉淡入动嘴，减少跳帧
        idle = self.current_source_frame()
        talk0 = self.package.frames[self.package.talk_start]
        self._transition = crossfade_frames(idle, talk0, self.package.transition_frames)
        self._transition_i = 0
        self._trans_to_talk = True
        self.state = AvatarState.TRANSITION
        self.talk_index = self.package.talk_start
        self._talk_dir = 1
        self.switch_count += 1

    def stop_talking(self) -> None:
        if self.state == AvatarState.IDLE:
            return
        cur = self.current_source_frame()
        idle = self.package.idle_frame
        self._transition = crossfade_frames(cur, idle, self.package.transition_frames)
        self._transition_i = 0
        self._trans_to_talk = False
        self.state = AvatarState.TRANSITION
        self.switch_count += 1
        self._idle_i = 0
        self._idle_dir = 1

    def current_source_frame(self) -> np.ndarray:
        if self.state == AvatarState.IDLE:
            frames = self.package.idle_frames
            if not frames:
                return self.package.idle_frame
            i = min(max(0, self._idle_i), len(frames) - 1)
            return frames[i]
        if self.state == AvatarState.TRANSITION and self._transition:
            i = min(self._transition_i, len(self._transition) - 1)
            return self._transition[i]
        return self.package.frames[self.talk_index]

    def next_frame(self) -> np.ndarray:
        if self.state == AvatarState.IDLE:
            return self._next_idle_frame()

        if self.state == AvatarState.TRANSITION:
            if self._transition_i < len(self._transition):
                frame = self._transition[self._transition_i]
                self._transition_i += 1
                if self._transition_i >= len(self._transition):
                    if self._trans_to_talk:
                        self.state = AvatarState.TALKING
                        self.talk_index = self.package.talk_start
                        self._talk_dir = 1
                    else:
                        self.state = AvatarState.IDLE
                return frame.copy()
            self.state = AvatarState.IDLE
            return self._next_idle_frame()

        return self._next_talk_frame()

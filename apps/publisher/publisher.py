"""Per-session LiveKit publisher: one Video Track + one Audio Track."""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from pathlib import Path
from typing import Deque, Optional

import numpy as np
from livekit import api, rtc

from avatar_frame_controller import AvatarFrameController, AvatarPackage, AvatarState
from config_pub import settings as pub_settings
from frame_clock import FrameClock

logger = logging.getLogger("publisher.core")


class SessionPublisher:
    def __init__(
        self,
        session_id: str,
        avatar_package_dir: Path,
        room_name: str,
        livekit_url: str,
        api_key: str,
        api_secret: str,
        sample_rate: int = 24000,
    ) -> None:
        self.session_id = session_id
        self.room_name = room_name
        self.livekit_url = livekit_url
        self.api_key = api_key
        self.api_secret = api_secret
        self.sample_rate = sample_rate

        self.package = AvatarPackage.get_shared(avatar_package_dir)
        self.controller = AvatarFrameController(self.package)
        self.clock = FrameClock(fps=float(self.package.fps))

        self.room = rtc.Room()
        self._video_source: Optional[rtc.VideoSource] = None
        self._audio_source: Optional[rtc.AudioSource] = None
        self._video_track = None
        self._audio_track = None

        self._pcm_queue: Deque[bytes] = deque()
        self._pcm_lock = threading.Lock()
        self.generation = 0
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
        self._speaking = False

    def create_token(self, identity: str, name: str, can_publish: bool = True) -> str:
        grant = api.VideoGrants(
            room_join=True,
            room=self.room_name,
            can_publish=can_publish,
            can_subscribe=True,
        )
        token = (
            api.AccessToken(self.api_key, self.api_secret)
            .with_identity(identity)
            .with_name(name)
            .with_grants(grant)
        )
        return token.to_jwt()

    async def start(self) -> None:
        if self._running:
            return
        token = self.create_token(
            identity=f"publisher_{self.session_id}",
            name=f"avatar-{self.session_id}",
            can_publish=True,
        )
        await self.room.connect(self.livekit_url, token)
        w, h = self.package.width, self.package.height
        self._video_source = rtc.VideoSource(w, h)
        self._audio_source = rtc.AudioSource(self.sample_rate, 1)
        self._video_track = rtc.LocalVideoTrack.create_video_track("avatar", self._video_source)
        self._audio_track = rtc.LocalAudioTrack.create_audio_track("voice", self._audio_source)
        await self.room.local_participant.publish_track(self._video_track)
        await self.room.local_participant.publish_track(self._audio_track)
        self._running = True
        self._loop_task = asyncio.create_task(self._media_loop())
        logger.info("Publisher started session=%s room=%s", self.session_id, self.room_name)

    async def stop(self) -> None:
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        try:
            await self.room.disconnect()
        except Exception:  # noqa: BLE001
            pass
        with self._pcm_lock:
            self._pcm_queue.clear()
        logger.info("Publisher stopped session=%s", self.session_id)

    def bump_generation(self) -> int:
        self.generation += 1
        with self._pcm_lock:
            self._pcm_queue.clear()
        return self.generation

    def start_speaking(self) -> None:
        self._speaking = True
        self.controller.start_talking()

    def stop_speaking(self) -> None:
        self._speaking = False
        self.controller.stop_talking()

    def push_pcm(self, pcm: bytes, generation: Optional[int] = None) -> None:
        if generation is not None and generation != self.generation:
            return
        with self._pcm_lock:
            self._pcm_queue.append(pcm)

    def clear_pcm(self) -> None:
        with self._pcm_lock:
            self._pcm_queue.clear()

    async def _media_loop(self) -> None:
        assert self._video_source is not None and self._audio_source is not None
        samples_per_frame = int(self.sample_rate / self.package.fps)  # 960 @24k/25fps
        while self._running:
            # video
            frame_rgb = self.controller.next_frame()
            _, pts = self.clock.next()
            # LiveKit VideoFrame expects RGBA or I420; use RGBA
            rgba = np.dstack(
                [frame_rgb, np.full(frame_rgb.shape[:2], 255, dtype=np.uint8)]
            )
            video_frame = rtc.VideoFrame(
                width=self.package.width,
                height=self.package.height,
                type=rtc.VideoBufferType.RGBA,
                data=rgba.tobytes(),
            )
            self._video_source.capture_frame(video_frame)

            # audio: pull PCM for ~1 video frame duration
            need = samples_per_frame * 2  # int16 mono
            buf = bytearray()
            with self._pcm_lock:
                while len(buf) < need and self._pcm_queue:
                    chunk = self._pcm_queue.popleft()
                    buf.extend(chunk)
                if len(buf) > need:
                    leftover = bytes(buf[need:])
                    buf = buf[:need]
                    self._pcm_queue.appendleft(leftover)
            if len(buf) < need:
                buf.extend(b"\x00" * (need - len(buf)))
            audio_frame = rtc.AudioFrame(
                data=bytes(buf[:need]),
                sample_rate=self.sample_rate,
                num_channels=1,
                samples_per_channel=samples_per_frame,
            )
            await self._audio_source.capture_frame(audio_frame)

            await asyncio.get_running_loop().run_in_executor(None, self.clock.sleep_until_next)


class PublisherPool:
    def __init__(self) -> None:
        self._pubs: dict[str, SessionPublisher] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    def get(self, session_id: str) -> Optional[SessionPublisher]:
        return self._pubs.get(session_id)

    async def ensure(
        self,
        session_id: str,
        avatar_package_dir: Path,
        room_name: str,
        livekit_url: str,
        api_key: str,
        api_secret: str,
        sample_rate: int = 24000,
    ) -> SessionPublisher:
        async with self._lock_for(session_id):
            pub = self._pubs.get(session_id)
            if pub and pub._running:
                return pub
            old = self._pubs.pop(session_id, None)
            if old:
                await old.stop()
            pub = SessionPublisher(
                session_id=session_id,
                avatar_package_dir=avatar_package_dir,
                room_name=room_name,
                livekit_url=livekit_url,
                api_key=api_key,
                api_secret=api_secret,
                sample_rate=sample_rate,
            )
            await pub.start()
            self._pubs[session_id] = pub
            return pub

    async def release(self, session_id: str) -> None:
        async with self._lock_for(session_id):
            pub = self._pubs.pop(session_id, None)
            if pub:
                await pub.stop()

    async def reap_if(self, session_id: str, gate) -> bool:
        """Under session lock: if gate() then stop publisher. Returns whether released."""
        async with self._lock_for(session_id):
            if not gate():
                return False
            pub = self._pubs.pop(session_id, None)
            if pub:
                await pub.stop()
            return True


publisher_pool = PublisherPool()

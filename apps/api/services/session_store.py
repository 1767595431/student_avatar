"""In-memory session store for P1."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class BusinessState(str, Enum):
    INIT = "init"
    IDLE = "idle"
    RECOGNIZING = "recognizing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTING = "interrupting"
    CLOSED = "closed"


class MediaState(str, Enum):
    CLOSED = "CLOSED"
    CONNECTING = "CONNECTING"
    READY = "READY"
    SPEAKING = "SPEAKING"
    WARM_IDLE = "WARM_IDLE"
    RELEASING = "RELEASING"


@dataclass
class Session:
    session_id: str
    student_id: str
    avatar_id: str
    avatar_version_id: str
    voice_id: str = ""
    agent_id: str = ""
    class_id: str = ""
    course_id: str = ""
    state: BusinessState = BusinessState.IDLE
    media_state: MediaState = MediaState.CLOSED
    conversation_id: str = ""
    current_question_id: Optional[str] = None
    current_tts_request_id: Optional[str] = None
    recognized_text: str = ""
    generation: int = 0
    room_name: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_media_activity_at: float = field(default_factory=time.time)
    cancel_event: Any = None  # asyncio.Event set at runtime

    def touch(self) -> None:
        self.updated_at = time.time()
        self.last_media_activity_at = self.updated_at


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(
        self,
        student_id: str,
        avatar_id: str,
        avatar_version_id: str,
        voice_id: str = "",
        agent_id: str = "",
        class_id: str = "",
        course_id: str = "",
    ) -> Session:
        sid = f"sess_{uuid.uuid4().hex[:12]}"
        sess = Session(
            session_id=sid,
            student_id=student_id,
            avatar_id=avatar_id,
            avatar_version_id=avatar_version_id,
            voice_id=voice_id,
            agent_id=agent_id,
            class_id=class_id,
            course_id=course_id,
            room_name=f"session_{sid}",
            state=BusinessState.IDLE,
        )
        self._sessions[sid] = sess
        return sess

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def all(self) -> list[Session]:
        return list(self._sessions.values())


store = SessionStore()

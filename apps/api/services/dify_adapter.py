"""Dify chat adapter — per-agent API Key."""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional

import httpx

from config import settings

logger = logging.getLogger("api.dify")


class DifyAdapter:
    def __init__(self) -> None:
        self._base = settings.dify_base_url.rstrip("/")
        self._key = settings.dify_api_key
        self._tasks: dict[str, str] = {}
        self._task_key: dict[str, str] = {}  # question_id -> api_key used
        self._session_of: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        self.agent_name: str = settings.dify_agent_name or ""
        self.agent_mode: str = ""

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_info(self, api_key: str, base_url: str = "") -> dict[str, Any]:
        base = (base_url or self._base).rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"}
        r = await self._client.get(f"{base}/v1/info", headers=headers)
        r.raise_for_status()
        return r.json()

    async def refresh_info(self) -> dict[str, Any]:
        if not self._key:
            logger.warning("DIFY_API_KEY empty")
            return {}
        info = await self.fetch_info(self._key, self._base)
        self.agent_name = info.get("name") or self.agent_name or ""
        self.agent_mode = info.get("mode") or ""
        if self.agent_name:
            settings.dify_agent_name = self.agent_name
        logger.info("Dify env agent name=%s mode=%s", self.agent_name, self.agent_mode)
        return info

    async def stream_answer(
        self,
        session_id: str,
        question_id: str,
        text: str,
        context: Optional[dict[str, Any]] = None,
        cancel_event: Optional[Any] = None,
        *,
        api_key: str = "",
        base_url: str = "",
        agent_name: str = "",
    ) -> AsyncIterator[str]:
        context = context or {}
        conversation_id = context.get("conversation_id") or ""
        user = f"{settings.dify_user_prefix}_{session_id}"
        key = (api_key or self._key).strip()
        base = (base_url or self._base).rstrip("/")
        payload = {
            "inputs": {},
            "query": text,
            "response_mode": "streaming",
            "user": user,
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        url = f"{base}/v1/chat-messages"
        self._cancelled.discard(question_id)
        self._session_of[question_id] = session_id
        self._task_key[question_id] = key
        logger.info(
            "Dify stream start agent=%s session=%s question=%s",
            agent_name or self.agent_name or "?",
            session_id,
            question_id,
        )

        async with self._client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if question_id in self._cancelled:
                    break
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                    break
                if not line:
                    continue
                if line.startswith("data:"):
                    raw = line[5:].strip()
                else:
                    continue
                if raw == "[DONE]":
                    break
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                etype = event.get("event")
                if event.get("task_id"):
                    self._tasks[question_id] = event["task_id"]
                if event.get("conversation_id"):
                    context["conversation_id"] = event["conversation_id"]
                if etype in ("message", "agent_message"):
                    answer = event.get("answer") or ""
                    if answer:
                        yield answer
                elif etype == "message_end":
                    tid = event.get("task_id")
                    if tid:
                        self._tasks[question_id] = tid
                    cid = event.get("conversation_id")
                    if cid:
                        context["conversation_id"] = cid
                elif etype == "error":
                    raise RuntimeError(event.get("message") or "dify error")

    async def cancel(self, question_id: str, session_id: str = "", *, api_key: str = "", base_url: str = "") -> None:
        self._cancelled.add(question_id)
        task_id = self._tasks.get(question_id)
        if not task_id:
            return
        sid = session_id or self._session_of.get(question_id, "")
        user = f"{settings.dify_user_prefix}_{sid}" if sid else f"{settings.dify_user_prefix}_cancel"
        key = (api_key or self._task_key.get(question_id) or self._key).strip()
        base = (base_url or self._base).rstrip("/")
        url = f"{base}/v1/chat-messages/{task_id}/stop"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        try:
            await self._client.post(url, headers=headers, json={"user": user})
            logger.info("Dify cancel ok question=%s task=%s", question_id, task_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dify cancel failed: %s", exc)


dify_adapter = DifyAdapter()

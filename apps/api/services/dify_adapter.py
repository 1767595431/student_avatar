"""Dify chat adapter — per-agent API Key（密钥来自管理端登记，不读 .env）。

对照本机 Dify OpenAPI（/v1/openapi.json）与 advanced-chat 实测 SSE：
- POST /v1/chat-messages，streaming → text/event-stream
- 增量文本：event=message|agent_message，字段 answer
- Chatflow 还会推 workflow_*/node_*；整段兜底在 workflow_finished.data.outputs
- message_end 通常无 answer，不能当作流结束且不能在此 break（会丢后续 workflow_finished）
- 行首可能有 `event: ping`（无 data），忽略即可
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional

import httpx

from config import settings
from services.concurrency import meter

logger = logging.getLogger("api.dify")

# 流可结束的事件（message_end 不够，见模块注释）
_END_EVENTS = frozenset({"workflow_finished"})


def _extract_delta(evt: dict[str, Any]) -> str:
    """兼容 Chat / Agent / Workflow 多种流式字段。"""
    etype = evt.get("event") or ""
    if etype in ("message", "agent_message", "message_replace"):
        return str(evt.get("answer") or "")
    if etype == "text_chunk":
        data = evt.get("data") or {}
        if isinstance(data, dict):
            return str(data.get("text") or data.get("answer") or "")
        return str(evt.get("text") or "")
    if etype == "workflow_finished":
        data = evt.get("data") or {}
        if isinstance(data, dict):
            outputs = data.get("outputs") or {}
            if isinstance(outputs, dict):
                for k in ("answer", "text", "result", "output"):
                    v = outputs.get(k)
                    if isinstance(v, str) and v.strip():
                        return v
    return ""


class DifyAdapter:
    def __init__(self) -> None:
        self._tasks: dict[str, str] = {}
        self._task_key: dict[str, str] = {}  # question_id -> api_key used
        self._session_of: dict[str, str] = {}
        self._cancelled: set[str] = set()
        # 流式首包常 >1s；读超时放宽，避免长答中途掐断
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    def _default_base(self) -> str:
        return (settings.dify_base_url or "").rstrip("/")

    async def fetch_info(self, api_key: str, base_url: str = "") -> dict[str, Any]:
        base = (base_url or self._default_base()).rstrip("/")
        if not base:
            raise ValueError("base_url required")
        headers = {"Authorization": f"Bearer {api_key}"}
        r = await self._client.get(f"{base}/v1/info", headers=headers)
        r.raise_for_status()
        return r.json()

    async def stream_answer(
        self,
        session_id: str,
        question_id: str,
        query: str,
        context: dict[str, Any],
        *,
        cancel_event: Optional[Any] = None,
        api_key: str = "",
        base_url: str = "",
        agent_name: str = "",
    ) -> AsyncIterator[str]:
        key = (api_key or "").strip()
        base = (base_url or self._default_base()).rstrip("/")
        if not key or not base:
            raise RuntimeError("agent api_key/base_url missing")
        user = f"{settings.dify_user_prefix}_{session_id}"
        # OpenAPI: inputs/query/user 必填；conversation_id 空串=新会话
        payload = {
            "inputs": {},
            "query": query,
            "response_mode": "streaming",
            "user": user,
            "conversation_id": context.get("conversation_id") or "",
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        self._task_key[question_id] = key
        self._session_of[question_id] = session_id
        self._cancelled.discard(question_id)
        logger.info(
            "Dify stream start agent=%s session=%s question=%s",
            agent_name or "?",
            session_id,
            question_id,
        )
        chars = 0
        events: list[str] = []
        meter.enter_dify()
        try:
            async with self._client.stream(
                "POST",
                f"{base}/v1/chat-messages",
                headers=headers,
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    if question_id in self._cancelled:
                        break
                    if not line:
                        continue
                    # SSE: `event: ping` / `data: {...}`；只解析 data 行
                    if line.startswith("event:"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("task_id"):
                        self._tasks[question_id] = evt["task_id"]
                    if evt.get("conversation_id"):
                        context["conversation_id"] = evt["conversation_id"]
                    etype = str(evt.get("event") or "")
                    if etype and len(events) < 40:
                        events.append(etype)
                    if etype == "error":
                        raise RuntimeError(evt.get("message") or "dify error")

                    delta = _extract_delta(evt)
                    # workflow_finished 整段仅在无增量时兜底，避免重复整段
                    if etype == "workflow_finished" and chars > 0:
                        delta = ""
                    if delta:
                        chars += len(delta)
                        yield delta

                    if etype == "message_end" and chars == 0:
                        # 少数部署会在 message_end 带整段 answer
                        full = str(evt.get("answer") or "")
                        if full:
                            chars += len(full)
                            yield full
                        # 不 break：等 workflow_finished 或连接结束
                        continue
                    if etype in _END_EVENTS:
                        break
            logger.info(
                "Dify stream end session=%s question=%s chars=%s events=%s",
                session_id,
                question_id,
                chars,
                ",".join(events) or "-",
            )
        finally:
            meter.leave_dify()

    async def cancel(
        self,
        question_id: str,
        session_id: str = "",
        *,
        api_key: str = "",
        base_url: str = "",
    ) -> None:
        self._cancelled.add(question_id)
        task_id = self._tasks.get(question_id)
        key = (api_key or self._task_key.get(question_id) or "").strip()
        base = (base_url or self._default_base()).rstrip("/")
        sid = session_id or self._session_of.get(question_id) or ""
        user = f"{settings.dify_user_prefix}_{sid}" if sid else f"{settings.dify_user_prefix}_cancel"
        if not task_id or not key or not base:
            return
        try:
            await self._client.post(
                f"{base}/v1/chat-messages/{task_id}/stop",
                headers={"Authorization": f"Bearer {key}"},
                json={"user": user},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dify cancel failed: %s", exc)


dify_adapter = DifyAdapter()

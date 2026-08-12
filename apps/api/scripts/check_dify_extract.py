"""ponytail: Dify 流式字段抽取自检（对照 advanced-chat 实测 SSE）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.dify_adapter import _END_EVENTS, _extract_delta  # noqa: E402


def main() -> None:
    assert _extract_delta({"event": "message", "answer": "你好"}) == "你好"
    assert _extract_delta({"event": "agent_message", "answer": "啊"}) == "啊"
    assert _extract_delta({"event": "text_chunk", "data": {"text": "北"}}) == "北"
    assert "京" in _extract_delta(
        {"event": "workflow_finished", "data": {"outputs": {"answer": "北京"}}}
    )
    # message_end 无 answer（Chatflow 实测）
    assert _extract_delta({"event": "message_end", "metadata": {}}) == ""
    assert _extract_delta({"event": "ping"}) == ""
    assert "workflow_finished" in _END_EVENTS
    assert "message_end" not in _END_EVENTS
    print("ok")


if __name__ == "__main__":
    main()

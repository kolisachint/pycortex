"""Unit tests for github-copilot-headers helpers."""

from __future__ import annotations

import time

from cortex.ai.providers._common import (
    build_copilot_dynamic_headers,
    has_copilot_vision_input,
    infer_copilot_initiator,
)
from cortex.ai.types import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def _usage() -> Usage:
    return Usage(
        input=0,
        output=0,
        cache_read=0,
        cache_write=0,
        total_tokens=0,
        cost={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
    )


def _ts() -> int:
    return int(time.time() * 1000)


def test_infer_initiator_user_for_trailing_user_message() -> None:
    messages: list[Message] = [UserMessage(content="hi", timestamp=_ts())]
    assert infer_copilot_initiator(messages) == "user"


def test_infer_initiator_agent_for_trailing_non_user_message() -> None:
    messages: list[Message] = [
        UserMessage(content="hi", timestamp=_ts()),
        AssistantMessage(
            role="assistant",
            content=[TextContent(type="text", text="hello")],
            api="anthropic-messages",
            provider="anthropic",
            model="claude",
            usage=_usage(),
            stop_reason="stop",
            timestamp=_ts(),
        ),
    ]
    assert infer_copilot_initiator(messages) == "agent"


def test_infer_initiator_user_for_empty_messages() -> None:
    assert infer_copilot_initiator([]) == "user"


def test_has_vision_input_detects_user_image() -> None:
    messages: list[Message] = [
        UserMessage(
            content=[ImageContent(type="image", data="abc", mime_type="image/png")],
            timestamp=_ts(),
        )
    ]
    assert has_copilot_vision_input(messages) is True


def test_has_vision_input_detects_tool_result_image() -> None:
    messages: list[Message] = [
        ToolResultMessage(
            role="toolResult",
            tool_call_id="c1",
            tool_name="read",
            content=[ImageContent(type="image", data="abc", mime_type="image/png")],
            is_error=False,
            timestamp=_ts(),
        )
    ]
    assert has_copilot_vision_input(messages) is True


def test_has_vision_input_false_for_text_only() -> None:
    messages: list[Message] = [UserMessage(content="hi", timestamp=_ts())]
    assert has_copilot_vision_input(messages) is False


def test_build_headers_without_images() -> None:
    messages: list[Message] = [UserMessage(content="hi", timestamp=_ts())]
    headers = build_copilot_dynamic_headers(messages, False)
    assert headers == {
        "X-Initiator": "user",
        "Openai-Intent": "conversation-edits",
    }


def test_build_headers_with_images() -> None:
    messages: list[Message] = [UserMessage(content="hi", timestamp=_ts())]
    headers = build_copilot_dynamic_headers(messages, True)
    assert headers["Copilot-Vision-Request"] == "true"
    assert headers["X-Initiator"] == "user"

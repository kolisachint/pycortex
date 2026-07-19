"""Tests for `cortex.ai.types` (port of types.ts)."""

from __future__ import annotations

from cortex.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    Message,
    Model,
    StartEvent,
    StopReason,
    TextContent,
    TextDeltaEvent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def test_user_message() -> None:
    msg = UserMessage(content="hello", timestamp=1)
    assert msg.role == "user"


def test_assistant_message() -> None:
    usage = Usage(
        input=1,
        output=2,
        cache_read=0,
        cache_write=0,
        total_tokens=3,
        cost={"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0, "total": 0.0},
    )
    msg = AssistantMessage(
        content=[TextContent(text="hi")],
        api="openai-completions",
        provider="openai",
        model="gpt-4",
        usage=usage,
        stop_reason="stop",
        timestamp=1,
    )
    assert msg.role == "assistant"


def test_message_union() -> None:
    msg: Message = UserMessage(content="hello", timestamp=1)
    assert msg.role == "user"


def test_tool_result_message() -> None:
    msg = ToolResultMessage(
        tool_call_id="1",
        tool_name="read",
        content=[TextContent(text="ok")],
        is_error=False,
        timestamp=1,
    )
    assert msg.role == "toolResult"


def test_tool() -> None:
    tool = Tool(name="read", description="read file", parameters={"type": "object"})
    assert tool.name == "read"


def test_context() -> None:
    ctx = Context(messages=[UserMessage(content="hi", timestamp=1)])
    assert len(ctx.messages) == 1


def test_events() -> None:
    usage = Usage(
        input=1,
        output=1,
        cache_read=0,
        cache_write=0,
        total_tokens=2,
        cost={"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0, "total": 0.0},
    )
    assistant = AssistantMessage(
        content=[TextContent(text="hi")],
        api="openai-completions",
        provider="openai",
        model="gpt-4",
        usage=usage,
        stop_reason="stop",
        timestamp=1,
    )
    _ = DoneEvent(reason="stop", message=assistant)
    _ = ErrorEvent(reason="error", error=assistant)
    _ = TextDeltaEvent(content_index=0, delta="x", partial=assistant)
    _ = StartEvent(partial=assistant)


def test_model() -> None:
    model = Model(
        id="gpt-4",
        name="GPT-4",
        api="openai-completions",
        provider="openai",
        base_url="https://api.openai.com",
        reasoning=False,
        input=["text"],
        cost={"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        context_window=8192,
        max_tokens=4096,
    )
    assert model.id == "gpt-4"


def test_stop_reason_literals() -> None:
    reasons: list[StopReason] = ["stop", "length", "toolUse", "error", "aborted"]
    assert len(reasons) == 5


def test_image_content() -> None:
    img = ImageContent(data="abc", mime_type="image/png")
    assert img.type == "image"


def test_thinking_content() -> None:
    t = ThinkingContent(thinking="...")
    assert t.type == "thinking"


def test_tool_call() -> None:
    tc = ToolCall(id="1", name="read", arguments={"path": "x"})
    assert tc.type == "toolCall"

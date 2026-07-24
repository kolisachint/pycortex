"""Port of hoocode's transform-messages-copilot-openai-to-anthropic.test.ts."""

from __future__ import annotations

import re
import time

from cortex.ai.providers._common import transform_messages
from cortex.ai.types import (
    AssistantMessage,
    Message,
    Model,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def anthropic_normalize_tool_call_id(
    id: str,
    _model: Model,
    _source: AssistantMessage,
) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", id)[:64]


def make_copilot_claude_model() -> Model:
    return Model(
        id="claude-sonnet-4.5",
        name="Claude Sonnet 4",
        api="anthropic-messages",
        provider="github-copilot",
        base_url="https://api.individual.githubcopilot.com",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        context_window=128000,
        max_tokens=16000,
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


def make_assistant_message(
    content: list[TextContent | ThinkingContent | ToolCall],
) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=content,
        api="openai-responses",
        provider="github-copilot",
        model="gpt-5",
        usage=_usage(),
        stop_reason="toolUse",
        timestamp=int(time.time() * 1000),
    )


def test_converts_thinking_blocks_to_plain_text_when_source_model_differs() -> None:
    model = make_copilot_claude_model()
    messages: list[Message] = [
        UserMessage(content="hello", timestamp=int(time.time() * 1000)),
        AssistantMessage(
            role="assistant",
            content=[
                ThinkingContent(
                    type="thinking",
                    thinking="Let me think about this...",
                    thinking_signature="reasoning_content",
                ),
                TextContent(type="text", text="Hi there!"),
            ],
            api="openai-completions",
            provider="github-copilot",
            model="gpt-4o",
            usage=_usage(),
            stop_reason="stop",
            timestamp=int(time.time() * 1000),
        ),
    ]

    result = transform_messages(messages, model, anthropic_normalize_tool_call_id)
    assistant_msg = next(m for m in result if m.role == "assistant")

    text_blocks = [b for b in assistant_msg.content if b.type == "text"]
    thinking_blocks = [b for b in assistant_msg.content if b.type == "thinking"]
    assert len(thinking_blocks) == 0
    assert len(text_blocks) >= 2


def test_removes_thought_signature_from_tool_calls_when_migrating() -> None:
    model = make_copilot_claude_model()
    messages: list[Message] = [
        UserMessage(content="run a command", timestamp=int(time.time() * 1000)),
        AssistantMessage(
            role="assistant",
            content=[
                ToolCall(
                    type="toolCall",
                    id="call_123",
                    name="bash",
                    arguments={"command": "ls"},
                    thought_signature='{"type":"reasoning.encrypted","id":"call_123","data":"encrypted"}',
                ),
            ],
            api="openai-responses",
            provider="github-copilot",
            model="gpt-5",
            usage=_usage(),
            stop_reason="toolUse",
            timestamp=int(time.time() * 1000),
        ),
        ToolResultMessage(
            role="toolResult",
            tool_call_id="call_123",
            tool_name="bash",
            content=[TextContent(type="text", text="output")],
            is_error=False,
            timestamp=int(time.time() * 1000),
        ),
    ]

    result = transform_messages(messages, model, anthropic_normalize_tool_call_id)
    assistant_msg = next(m for m in result if m.role == "assistant")
    tool_call = next(b for b in assistant_msg.content if b.type == "toolCall")

    assert tool_call.thought_signature is None


def test_adds_synthetic_tool_results_for_trailing_orphaned_tool_calls() -> None:
    model = make_copilot_claude_model()
    messages: list[Message] = [
        UserMessage(content="read the file", timestamp=int(time.time() * 1000)),
        make_assistant_message(
            [
                ToolCall(
                    type="toolCall",
                    id="call_123|fc_123",
                    name="read",
                    arguments={"path": "README.md"},
                ),
            ]
        ),
    ]

    result = transform_messages(messages, model, anthropic_normalize_tool_call_id)
    last_message = result[-1]

    assert last_message.role == "toolResult"
    assert last_message.tool_call_id == "call_123_fc_123"
    assert last_message.tool_name == "read"
    assert last_message.is_error is True
    assert last_message.content == [TextContent(type="text", text="No result provided")]


def test_adds_synthetic_results_only_for_trailing_calls_still_missing_results() -> None:
    model = make_copilot_claude_model()
    messages: list[Message] = [
        UserMessage(content="run commands", timestamp=int(time.time() * 1000)),
        make_assistant_message(
            [
                ToolCall(
                    type="toolCall",
                    id="call_1|fc_1",
                    name="read",
                    arguments={"path": "README.md"},
                ),
                ToolCall(
                    type="toolCall",
                    id="call_2|fc_2",
                    name="bash",
                    arguments={"command": "pwd"},
                ),
            ]
        ),
        ToolResultMessage(
            role="toolResult",
            tool_call_id="call_1|fc_1",
            tool_name="read",
            content=[TextContent(type="text", text="done")],
            is_error=False,
            timestamp=int(time.time() * 1000),
        ),
    ]

    result = transform_messages(messages, model, anthropic_normalize_tool_call_id)
    synthetic_results = [m for m in result if m.role == "toolResult" and m.is_error]

    assert len(synthetic_results) == 1
    assert synthetic_results[0].tool_call_id == "call_2_fc_2"
    assert synthetic_results[0].tool_name == "bash"
    assert synthetic_results[0].content == [TextContent(type="text", text="No result provided")]

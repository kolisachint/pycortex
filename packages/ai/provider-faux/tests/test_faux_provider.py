"""Tests for the faux AI provider.

Mechanical port of hoocode's
`packages/ai/test/faux-provider.test.ts`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from cortex.ai.providers.faux import (
    faux_assistant_message,
    faux_text,
    faux_thinking,
    faux_tool_call,
    register_faux_provider,
)
from cortex.ai.stream import complete, stream
from cortex.ai.types import (
    Context,
    ImageContent,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_registrations: list[Any] = []


@pytest.fixture(autouse=True)
def _cleanup() -> Any:  # pyright: ignore[reportUnusedFunction]
    yield
    for r in _registrations:
        r.unregister()
    _registrations.clear()


async def collect_events(s: Any) -> list[Any]:
    return [e async for e in s]


class _Signal:
    """Minimal AbortSignal stub."""

    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


# ---------------------------------------------------------------------------
# Tests (one per TS `it(...)`, same order)
# ---------------------------------------------------------------------------


async def test_registers_a_custom_provider_and_estimates_usage() -> None:
    registration = register_faux_provider()
    _registrations.append(registration)
    registration.set_responses([faux_assistant_message("hello world")])

    context = Context(
        system_prompt="Be concise.",
        messages=[UserMessage(content="hi there", timestamp=0)],
    )

    response = await complete(registration.get_model(), context)
    assert [c.model_dump(exclude_none=True) for c in response.content] == [
        {"type": "text", "text": "hello world"}
    ]
    assert response.usage.input > 0
    assert response.usage.output > 0
    assert response.usage.total_tokens == response.usage.input + response.usage.output
    assert registration.state.call_count == 1


async def test_supports_helper_blocks_for_text_thinking_and_tool_calls() -> None:
    registration = register_faux_provider()
    _registrations.append(registration)
    registration.set_responses(
        [
            faux_assistant_message(
                [faux_thinking("think"), faux_tool_call("echo", {"text": "hi"}), faux_text("done")],
                stop_reason="toolUse",
            )
        ]
    )

    response = await complete(
        registration.get_model(),
        Context(messages=[UserMessage(content="hi", timestamp=0)]),
    )

    assert len(response.content) == 3

    thinking = response.content[0]
    assert thinking.type == "thinking"
    assert thinking.thinking == "think"  # type: ignore[union-attr]

    tool_call = response.content[1]
    assert tool_call.type == "toolCall"
    assert isinstance(tool_call.id, str) and tool_call.id  # type: ignore[union-attr]
    assert tool_call.name == "echo"  # type: ignore[union-attr]
    assert tool_call.arguments == {"text": "hi"}  # type: ignore[union-attr]

    text = response.content[2]
    assert text.type == "text"
    assert text.text == "done"  # type: ignore[union-attr]

    assert response.stop_reason == "toolUse"


async def test_supports_multiple_models_per_model_reasoning_and_factories() -> None:
    registration = register_faux_provider(
        models=[
            {"id": "faux-fast", "name": "Faux Fast", "reasoning": False},
            {"id": "faux-thinker", "name": "Faux Thinker", "reasoning": True},
        ]
    )
    _registrations.append(registration)
    registration.set_responses(
        [
            lambda context, options, state, model: faux_assistant_message(  # type: ignore[misc]
                f"{model.id}:{str(model.reasoning).lower()}"
            ),
            lambda context, options, state, model: faux_assistant_message(  # type: ignore[misc]
                f"{model.id}:{str(model.reasoning).lower()}"
            ),
        ]
    )

    assert [m.id for m in registration.models] == ["faux-fast", "faux-thinker"]
    assert registration.get_model() == registration.models[0]
    fast_model = registration.get_model("faux-fast")
    assert fast_model is not None
    assert fast_model.reasoning is False
    thinker_model = registration.get_model("faux-thinker")
    assert thinker_model is not None
    assert thinker_model.reasoning is True

    fast_response = await complete(
        registration.get_model("faux-fast"),  # type: ignore[arg-type]
        Context(messages=[UserMessage(content="hi", timestamp=0)]),
    )
    thinker_response = await complete(
        registration.get_model("faux-thinker"),  # type: ignore[arg-type]
        Context(messages=[UserMessage(content="hi", timestamp=0)]),
    )

    assert [c.model_dump(exclude_none=True) for c in fast_response.content] == [
        {"type": "text", "text": "faux-fast:false"}
    ]
    assert [c.model_dump(exclude_none=True) for c in thinker_response.content] == [
        {"type": "text", "text": "faux-thinker:true"}
    ]


async def test_rewrites_api_provider_and_model_on_returned_messages() -> None:
    registration = register_faux_provider(
        api="faux:test",
        provider="faux-provider",
        models=[{"id": "faux-model"}],
    )
    _registrations.append(registration)
    registration.set_responses([faux_assistant_message("hello")])

    response = await complete(
        registration.get_model(),  # type: ignore[arg-type]
        Context(messages=[UserMessage(content="hi", timestamp=0)]),
    )

    assert response.api == "faux:test"
    assert response.provider == "faux-provider"
    assert response.model == "faux-model"


async def test_consumes_queued_responses_in_order_and_errors_when_exhausted() -> None:
    registration = register_faux_provider()
    _registrations.append(registration)
    registration.set_responses([faux_assistant_message("first"), faux_assistant_message("second")])

    context = Context(messages=[UserMessage(content="hi", timestamp=0)])

    first = await complete(registration.get_model(), context)
    second = await complete(registration.get_model(), context)
    exhausted = await complete(registration.get_model(), context)

    first_content = [c.model_dump(exclude_none=True) for c in first.content]
    assert first_content == [{"type": "text", "text": "first"}]
    second_content = [c.model_dump(exclude_none=True) for c in second.content]
    assert second_content == [{"type": "text", "text": "second"}]
    assert exhausted.stop_reason == "error"
    assert exhausted.error_message == "No more faux responses queued"
    assert registration.get_pending_response_count() == 0
    assert registration.state.call_count == 3


async def test_can_replace_and_append_queued_responses() -> None:
    registration = register_faux_provider()
    _registrations.append(registration)
    registration.set_responses([faux_assistant_message("first")])

    context = Context(messages=[UserMessage(content="hi", timestamp=0)])

    r1 = await complete(registration.get_model(), context)
    r1_content = [c.model_dump(exclude_none=True) for c in r1.content]
    assert r1_content == [{"type": "text", "text": "first"}]
    assert registration.get_pending_response_count() == 0

    registration.set_responses([faux_assistant_message("second")])
    assert registration.get_pending_response_count() == 1
    r2 = await complete(registration.get_model(), context)
    r2_content = [c.model_dump(exclude_none=True) for c in r2.content]
    assert r2_content == [{"type": "text", "text": "second"}]

    registration.append_responses(
        [faux_assistant_message("third"), faux_assistant_message("fourth")]
    )
    assert registration.get_pending_response_count() == 2
    r3 = await complete(registration.get_model(), context)
    r3_content = [c.model_dump(exclude_none=True) for c in r3.content]
    assert r3_content == [{"type": "text", "text": "third"}]
    r4 = await complete(registration.get_model(), context)
    r4_content = [c.model_dump(exclude_none=True) for c in r4.content]
    assert r4_content == [{"type": "text", "text": "fourth"}]
    assert registration.get_pending_response_count() == 0


async def test_supports_async_response_factories() -> None:
    registration = register_faux_provider()
    _registrations.append(registration)

    async def factory(context: Any, options: Any, state: Any, model: Any = None) -> Any:
        return faux_assistant_message(f"{len(context.messages)}:{state.call_count}")

    registration.set_responses([factory])

    response = await complete(
        registration.get_model(),
        Context(messages=[UserMessage(content="hi", timestamp=0)]),
    )

    resp_content = [c.model_dump(exclude_none=True) for c in response.content]
    assert resp_content == [{"type": "text", "text": "1:1"}]


async def test_emits_an_error_when_a_response_factory_throws() -> None:
    registration = register_faux_provider()
    _registrations.append(registration)

    def throwing_factory(context: Any, options: Any, state: Any, model: Any) -> Any:
        raise RuntimeError("boom")

    registration.set_responses([throwing_factory])

    events = await collect_events(
        stream(
            registration.get_model(),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
        )
    )

    assert len(events) == 1
    assert events[0].type == "error"
    assert events[0].error.stop_reason == "error"
    assert events[0].error.error_message == "boom"


async def test_estimates_prompt_and_output_tokens_from_serialized_context() -> None:
    from cortex.ai.providers.faux import (
        _estimate_tokens,  # pyright: ignore[reportPrivateUsage]
        _serialize_context,  # pyright: ignore[reportPrivateUsage]
    )

    registration = register_faux_provider()
    _registrations.append(registration)
    registration.set_responses([faux_assistant_message("done")])

    tool = Tool(
        name="echo",
        description="Echo back text",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
    )
    context = Context(
        system_prompt="sys",
        messages=[
            UserMessage(
                content=[
                    TextContent(text="hello"),
                    ImageContent(mime_type="image/png", data="abcd"),
                ],
                timestamp=1,
            ),
            faux_assistant_message("prior"),
            ToolResultMessage(
                tool_call_id="tool-1",
                tool_name="echo",
                content=[TextContent(text="tool out")],
                is_error=False,
                timestamp=2,
            ),
        ],
        tools=[tool],
    )

    response = await complete(registration.get_model(), context)

    prompt_text = _serialize_context(context)
    expected_prompt_tokens = _estimate_tokens(prompt_text)
    expected_output_tokens = _estimate_tokens("done")

    assert response.usage.input == expected_prompt_tokens
    assert response.usage.output == expected_output_tokens
    assert response.usage.cache_read == 0
    assert response.usage.cache_write == 0
    assert response.usage.total_tokens == expected_prompt_tokens + expected_output_tokens


async def test_does_not_share_cache_across_sessions_or_requests_without_session_id() -> None:
    registration = register_faux_provider()
    _registrations.append(registration)
    registration.set_responses(
        [
            faux_assistant_message("first"),
            faux_assistant_message("second"),
            faux_assistant_message("third"),
        ]
    )

    context = Context(messages=[UserMessage(content="hello", timestamp=0)])

    first = await complete(
        registration.get_model(),
        context,
        {"session_id": "session-1", "cache_retention": "short"},
    )
    assert first.usage.cache_write > 0
    context = Context(
        messages=[
            *context.messages,
            first,
            UserMessage(content="follow up", timestamp=1),
        ]
    )

    second = await complete(
        registration.get_model(),
        context,
        {"session_id": "session-2", "cache_retention": "short"},
    )
    assert second.usage.cache_read == 0
    assert second.usage.cache_write > 0

    third = await complete(registration.get_model(), context)
    assert third.usage.cache_read == 0
    assert third.usage.cache_write == 0


async def test_simulates_prompt_caching_per_session_id() -> None:
    registration = register_faux_provider()
    _registrations.append(registration)
    registration.set_responses([faux_assistant_message("first"), faux_assistant_message("second")])

    context = Context(
        system_prompt="Be concise.",
        messages=[UserMessage(content="hello", timestamp=0)],
    )

    first = await complete(
        registration.get_model(),
        context,
        {"session_id": "session-1", "cache_retention": "short"},
    )
    assert first.usage.cache_read == 0
    assert first.usage.cache_write > 0

    context = Context(
        system_prompt=context.system_prompt,
        messages=[
            *context.messages,
            first,
            UserMessage(content="follow up", timestamp=1),
        ],
    )

    second = await complete(
        registration.get_model(),
        context,
        {"session_id": "session-1", "cache_retention": "short"},
    )
    assert second.usage.cache_read > 0
    assert second.usage.input + second.usage.cache_read > second.usage.input


async def test_does_not_simulate_caching_when_cache_retention_is_none() -> None:
    registration = register_faux_provider()
    _registrations.append(registration)
    registration.set_responses([faux_assistant_message("first"), faux_assistant_message("second")])

    context = Context(messages=[UserMessage(content="hello", timestamp=0)])

    await complete(
        registration.get_model(),
        context,
        {"session_id": "session-1", "cache_retention": "none"},
    )
    context = Context(
        messages=[
            *context.messages,
            faux_assistant_message("first"),
            UserMessage(content="follow up", timestamp=1),
        ]
    )
    second = await complete(
        registration.get_model(),
        context,
        {"session_id": "session-1", "cache_retention": "none"},
    )
    assert second.usage.cache_read == 0
    assert second.usage.cache_write == 0


async def test_streams_thinking_text_and_partial_tool_call_deltas() -> None:
    registration = register_faux_provider()
    _registrations.append(registration)
    registration.set_responses(
        [
            faux_assistant_message(
                [
                    faux_thinking("thinking text"),
                    faux_text("answer text"),
                    faux_tool_call("echo", {"text": "hi", "count": 12}, {"id": "tool-1"}),
                ],
                stop_reason="toolUse",
            )
        ]
    )

    events: list[str] = []
    tool_call_deltas: list[str] = []
    s = stream(
        registration.get_model(),
        Context(messages=[UserMessage(content="hi", timestamp=0)]),
    )
    async for event in s:
        events.append(event.type)
        if event.type == "toolcall_delta":
            tool_call_deltas.append(event.delta)

    assert "thinking_start" in events
    assert "thinking_delta" in events
    assert "text_start" in events
    assert "text_delta" in events
    assert "toolcall_start" in events
    assert "toolcall_delta" in events
    assert "toolcall_end" in events
    assert len(tool_call_deltas) > 1
    assert json.loads("".join(tool_call_deltas)) == {"text": "hi", "count": 12}


async def test_streams_an_exact_event_order_for_fixed_size_chunks() -> None:
    registration = register_faux_provider(token_size={"min": 1, "max": 1})
    _registrations.append(registration)
    registration.set_responses(
        [
            faux_assistant_message(
                [
                    faux_thinking("go"),
                    faux_text("ok"),
                    faux_tool_call("echo", {}, {"id": "tool-1"}),
                ],
                stop_reason="toolUse",
            )
        ]
    )

    events = await collect_events(
        stream(
            registration.get_model(),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
        )
    )

    assert [e.type for e in events] == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "text_start",
        "text_delta",
        "text_end",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_end",
        "done",
    ]


async def test_streams_multiple_tool_calls_in_one_message() -> None:
    registration = register_faux_provider()
    _registrations.append(registration)
    registration.set_responses(
        [
            faux_assistant_message(
                [
                    faux_tool_call("echo", {"text": "one"}, {"id": "tool-1"}),
                    faux_tool_call("echo", {"text": "two"}, {"id": "tool-2"}),
                ],
                stop_reason="toolUse",
            )
        ]
    )

    events = await collect_events(
        stream(
            registration.get_model(),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
        )
    )

    assert len([e for e in events if e.type == "toolcall_start"]) == 2
    assert len([e for e in events if e.type == "toolcall_end"]) == 2


async def test_streams_an_explicit_assistant_error_message_as_a_terminal_error() -> None:
    registration = register_faux_provider(token_size={"min": 2, "max": 2})
    _registrations.append(registration)
    msg = faux_assistant_message("partial")
    error_msg = msg.model_copy(update={"stop_reason": "error", "error_message": "upstream failed"})
    registration.set_responses([error_msg])

    events = await collect_events(
        stream(
            registration.get_model(),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
        )
    )

    assert [e.type for e in events] == ["start", "text_start", "text_delta", "text_end", "error"]
    terminal = events[-1]
    assert terminal.type == "error"
    assert terminal.reason == "error"
    assert terminal.error.stop_reason == "error"
    assert terminal.error.error_message == "upstream failed"


async def test_streams_an_explicit_assistant_aborted_message_as_a_terminal_error() -> None:
    registration = register_faux_provider(token_size={"min": 2, "max": 2})
    _registrations.append(registration)
    msg = faux_assistant_message("partial")
    aborted_msg = msg.model_copy(
        update={"stop_reason": "aborted", "error_message": "Request was aborted"}
    )
    registration.set_responses([aborted_msg])

    events = await collect_events(
        stream(
            registration.get_model(),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
        )
    )

    assert [e.type for e in events] == ["start", "text_start", "text_delta", "text_end", "error"]
    terminal = events[-1]
    assert terminal.type == "error"
    assert terminal.reason == "aborted"
    assert terminal.error.stop_reason == "aborted"
    assert terminal.error.error_message == "Request was aborted"


async def test_supports_aborting_before_the_first_chunk() -> None:
    registration = register_faux_provider(tokens_per_second=50, token_size={"min": 3, "max": 3})
    _registrations.append(registration)
    registration.set_responses([faux_assistant_message("abcdefghijklmnopqrstuvwxyz")])

    signal = _Signal()
    signal.abort()
    events = await collect_events(
        stream(
            registration.get_model(),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
            {"signal": signal},
        )
    )

    assert len(events) == 1
    assert events[0].type == "error"
    assert events[0].reason == "aborted"
    assert events[0].error.stop_reason == "aborted"


async def test_supports_aborting_mid_text_stream_when_paced() -> None:
    registration = register_faux_provider(tokens_per_second=100, token_size={"min": 3, "max": 3})
    _registrations.append(registration)
    registration.set_responses([faux_assistant_message("abcdefghijklmnopqrstuvwxyz")])

    signal = _Signal()
    events: list[str] = []
    text_delta_count = 0
    s = stream(
        registration.get_model(),
        Context(messages=[UserMessage(content="hi", timestamp=0)]),
        {"signal": signal},
    )
    async for event in s:
        events.append(event.type)
        if event.type == "text_delta":
            text_delta_count += 1
            signal.abort()

    assert text_delta_count == 1
    assert "text_start" in events
    assert "text_delta" in events
    assert "error" in events
    assert "text_end" not in events


async def test_supports_aborting_mid_thinking_stream_when_paced() -> None:
    registration = register_faux_provider(tokens_per_second=100, token_size={"min": 3, "max": 3})
    _registrations.append(registration)
    msg = faux_assistant_message("ignored")
    thinking_msg = msg.model_copy(
        update={"content": [ThinkingContent(thinking="abcdefghijklmnopqrstuvwxyz")]}
    )
    registration.set_responses([thinking_msg])

    signal = _Signal()
    events: list[str] = []
    thinking_delta_count = 0
    s = stream(
        registration.get_model(),
        Context(messages=[UserMessage(content="hi", timestamp=0)]),
        {"signal": signal},
    )
    async for event in s:
        events.append(event.type)
        if event.type == "thinking_delta":
            thinking_delta_count += 1
            signal.abort()

    assert thinking_delta_count == 1
    assert "thinking_start" in events
    assert "thinking_delta" in events
    assert "error" in events
    assert "thinking_end" not in events


async def test_supports_aborting_mid_toolcall_stream_when_paced() -> None:
    registration = register_faux_provider(tokens_per_second=100, token_size={"min": 3, "max": 3})
    _registrations.append(registration)
    msg = faux_assistant_message("done")
    toolcall_msg = msg.model_copy(
        update={
            "content": [
                ToolCall(
                    id="tool-1",
                    name="echo",
                    arguments={"text": "abcdefghijklmnopqrstuvwxyz", "count": 123456789},
                )
            ],
            "stop_reason": "toolUse",
        }
    )
    registration.set_responses([toolcall_msg])

    signal = _Signal()
    events: list[str] = []
    tool_call_delta_count = 0
    s = stream(
        registration.get_model(),
        Context(messages=[UserMessage(content="hi", timestamp=0)]),
        {"signal": signal},
    )
    async for event in s:
        events.append(event.type)
        if event.type == "toolcall_delta":
            tool_call_delta_count += 1
            signal.abort()

    assert tool_call_delta_count == 1
    assert "toolcall_start" in events
    assert "toolcall_delta" in events
    assert "error" in events
    assert "toolcall_end" not in events


async def test_unregisters_the_provider() -> None:
    registration = register_faux_provider()
    registration.set_responses([faux_assistant_message("hello")])
    registration.unregister()

    with pytest.raises(ValueError, match=f"No API provider registered for api: {registration.api}"):
        await complete(
            registration.get_model(),
            Context(messages=[UserMessage(content="hi", timestamp=0)]),
        )

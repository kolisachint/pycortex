"""Port of hoocode's `packages/ai/test/stream.test.ts` (wiring only).

The TS original exercises `stream`/`complete` end-to-end against live provider
APIs (Anthropic, OpenAI, Google, ...), gated behind API-key env vars. None of
that is portable to a unit-test suite. Instead, this covers the actual logic
that `stream.ts`/`stream.py` contain: resolving the registered API provider
and delegating to it, plus the `AssistantMessageEventStream` protocol that
`providers/faux.ts` (ported in a later step) and every real provider produce.
"""

from __future__ import annotations

from typing import Any, Literal

import pytest
from cortex.ai.models import (
    ApiProvider,
    clear_api_providers,
    register_api_provider,
)
from cortex.ai.stream import (
    AssistantMessageEventStream,
    EventStream,
    complete,
    complete_simple,
    create_assistant_message_event_stream,
    get_env_api_key,
    stream,
    stream_simple,
)
from cortex.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    Model,
    TextContent,
    Usage,
)


@pytest.fixture(autouse=True)
def _clear_registry() -> Any:  # pyright: ignore[reportUnusedFunction]
    clear_api_providers()
    yield
    clear_api_providers()


def _model(api: str = "stub-api") -> Model:
    return Model(
        id="stub-model",
        name="Stub Model",
        api=api,
        provider="stub",
        base_url="https://example.invalid",
        reasoning=False,
        input=["text"],
        cost={},
        context_window=1000,
        max_tokens=100,
    )


def _usage() -> Usage:
    return Usage(
        input=1,
        output=1,
        cache_read=0,
        cache_write=0,
        total_tokens=2,
        cost={"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0, "total": 0.0},
    )


def _first_text(message: AssistantMessage) -> str:
    block = message.content[0]
    assert isinstance(block, TextContent)
    return block.text


def _assistant_message(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="stub-api",
        provider="stub",
        model="stub-model",
        usage=_usage(),
        stop_reason="stop",
        timestamp=0,
    )


def _register_stub_provider(api: str = "stub-api") -> list[tuple[str, Any]]:
    """Register a minimal provider and return a log of (fn_name, args) calls."""
    calls: list[tuple[str, Any]] = []

    def make_stream(kind: str):
        def fn(model: Model, context: Context, options: dict[str, Any] | None = None):
            calls.append((kind, (model, context, options)))
            s = create_assistant_message_event_stream()
            message = _assistant_message(f"{kind}-response")
            s.push(_done_event(message))
            return s

        return fn

    register_api_provider(
        ApiProvider(
            api=api,
            stream=make_stream("stream"),  # type: ignore
            stream_simple=make_stream("stream_simple"),  # type: ignore
        )
    )
    return calls


def _done_event(
    message: AssistantMessage,
    reason: Literal["stop", "length", "toolUse"] = "stop",
) -> DoneEvent:
    return DoneEvent(reason=reason, message=message)


def _error_event(message: AssistantMessage) -> ErrorEvent:
    return ErrorEvent(reason="error", error=message)


class TestResolveAndDispatch:
    async def test_stream_raises_for_unregistered_api(self) -> None:
        with pytest.raises(ValueError, match="No API provider registered for api: missing"):
            stream(_model("missing"), Context(messages=[]))

    async def test_stream_simple_raises_for_unregistered_api(self) -> None:
        with pytest.raises(ValueError, match="No API provider registered for api: missing"):
            stream_simple(_model("missing"), Context(messages=[]))

    async def test_stream_delegates_to_registered_provider(self) -> None:
        calls = _register_stub_provider()
        model = _model()
        context = Context(messages=[])
        s = stream(model, context)

        assert isinstance(s, AssistantMessageEventStream)
        result = await s.result()
        assert _first_text(result) == "stream-response"
        assert calls == [("stream", (model, context, None))]

    async def test_complete_awaits_stream_result(self) -> None:
        _register_stub_provider()
        response = await complete(_model(), Context(messages=[]))
        assert _first_text(response) == "stream-response"

    async def test_stream_simple_delegates_to_registered_provider(self) -> None:
        calls = _register_stub_provider()
        model = _model()
        context = Context(messages=[])
        s = stream_simple(model, context)

        result = await s.result()
        assert _first_text(result) == "stream_simple-response"
        assert calls == [("stream_simple", (model, context, None))]

    async def test_complete_simple_awaits_stream_result(self) -> None:
        _register_stub_provider()
        response = await complete_simple(_model(), Context(messages=[]))
        assert _first_text(response) == "stream_simple-response"

    async def test_options_are_forwarded(self) -> None:
        calls = _register_stub_provider()
        options = {"temperature": 0.5}
        stream(_model(), Context(messages=[]), options)
        assert calls[0][1][2] == options

    def test_get_env_api_key_reexported(self) -> None:
        assert get_env_api_key is not None


class TestEventStream:
    async def test_push_and_iterate_yields_events_in_order(self) -> None:
        s: EventStream[int, int] = EventStream(
            is_complete=lambda e: e == -1,
            extract_result=lambda e: e,
        )
        s.push(1)
        s.push(2)
        s.push(-1)

        events = [event async for event in s]
        assert events == [1, 2, -1]
        assert await s.result() == -1

    async def test_end_without_result_terminates_iteration(self) -> None:
        s: EventStream[int, int] = EventStream(
            is_complete=lambda e: False,
            extract_result=lambda e: e,
        )
        s.push(1)
        s.end()

        events = [event async for event in s]
        assert events == [1]

    async def test_consumer_can_await_events_pushed_later(self) -> None:
        import asyncio

        s: EventStream[int, int] = EventStream(
            is_complete=lambda e: e == -1,
            extract_result=lambda e: e,
        )

        async def producer() -> None:
            await asyncio.sleep(0)
            s.push(1)
            s.push(-1)

        asyncio.create_task(producer())
        events = [event async for event in s]
        assert events == [1, -1]

    async def test_assistant_message_event_stream_resolves_from_done_event(self) -> None:
        s = create_assistant_message_event_stream()
        message = _assistant_message("hi")
        s.push(_done_event(message))
        assert await s.result() is message

    async def test_assistant_message_event_stream_resolves_from_error_event(self) -> None:
        s = create_assistant_message_event_stream()
        message = _assistant_message("boom")
        s.push(_error_event(message))
        assert await s.result() is message

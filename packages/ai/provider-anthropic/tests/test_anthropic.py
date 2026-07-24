"""Tests for the Anthropic provider.

Port of hoocode's fixture tests for ``providers/anthropic.ts``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cortex.ai.models import get_model
from cortex.ai.providers.anthropic import (
    AnthropicOptions,
    stream_anthropic,
    stream_simple_anthropic,
)
from cortex.ai.types import Context, SimpleStreamOptions, Tool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sse_response(
    events: list[dict[str, str]],
    status: int = 200,
) -> MagicMock:
    """Create a mock httpx.Response that streams SSE events."""
    body_lines: list[str] = []
    for evt in events:
        body_lines.append(f"event: {evt['event']}")
        body_lines.append(f"data: {evt['data']}")
        body_lines.append("")
    body = "\n".join(body_lines)
    body_bytes = body.encode("utf-8")

    mock_response = MagicMock()
    mock_response.status_code = status
    mock_response.headers = {"content-type": "text/event-stream"}

    async def _aiter_bytes() -> AsyncIterator[bytes]:
        chunk_size = 64
        for i in range(0, len(body_bytes), chunk_size):
            yield body_bytes[i : i + chunk_size]

    mock_response.aiter_bytes = _aiter_bytes
    return mock_response


def _minimal_anthropic_events(
    text: str = "Hello",
    *,
    stop_reason: str = "end_turn",
    input_tokens: int = 12,
    output_tokens: int = 5,
) -> list[dict[str, str]]:
    """Build a minimal set of Anthropic SSE events for a simple text response."""
    return [
        {
            "event": "message_start",
            "data": json.dumps(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_test",
                        "usage": {
                            "input_tokens": input_tokens,
                            "output_tokens": 0,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    },
                }
            ),
        },
        {
            "event": "content_block_start",
            "data": json.dumps(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                }
            ),
        },
        {
            "event": "content_block_delta",
            "data": json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": text},
                }
            ),
        },
        {
            "event": "content_block_stop",
            "data": json.dumps({"type": "content_block_stop", "index": 0}),
        },
        {
            "event": "message_delta",
            "data": json.dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason},
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                }
            ),
        },
        {
            "event": "message_stop",
            "data": json.dumps({"type": "message_stop"}),
        },
    ]


async def _make_mock_client(
    events: list[dict[str, str]],
    status: int = 200,
) -> MagicMock:
    """Create a mock AnthropicClient that returns a pre-built SSE response."""
    mock_response = _make_sse_response(events, status)
    client = MagicMock()
    client.create_message = AsyncMock(return_value=mock_response)
    return client


def _make_context(
    content: str = "Hello",
    *,
    system_prompt: str | None = None,
    tools: list[Tool] | None = None,
) -> Context:
    """Create a minimal Context for testing."""
    return Context(
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": content, "timestamp": 0}],  # type: ignore[arg-type]
        tools=tools,
    )


# ---------------------------------------------------------------------------
# Test: SSE parsing
# ---------------------------------------------------------------------------


class TestSseParsing:
    """Port of anthropic-sse-parsing.test.ts."""

    @pytest.mark.asyncio
    async def test_repairs_malformed_tool_json(self) -> None:
        """Malformed SSE JSON and malformed streamed tool JSON are repaired."""
        model = get_model("anthropic", "claude-haiku-4-5")
        assert model is not None
        context = _make_context(
            "Use the edit tool.",
            tools=[
                Tool(
                    name="edit",
                    description="Edit a file.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "text": {"type": "string"},
                        },
                    },
                ),
            ],
        )

        # Malformed tool JSON delta (backslash escape + tab)
        malformed_delta = (
            '{"type":"content_block_delta","index":0,'
            '"delta":{"type":"input_json_delta",'
            '"partial_json":"{\\"path\\":\\"A\\\\H\\",\\"text\\":\\"col1\\tcol2\\"}"}}'
        )

        events = [
            {
                "event": "message_start",
                "data": json.dumps(
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_test",
                            "usage": {
                                "input_tokens": 12,
                                "output_tokens": 0,
                                "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 0,
                            },
                        },
                    }
                ),
            },
            {
                "event": "content_block_start",
                "data": json.dumps(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": "toolu_test",
                            "name": "edit",
                            "input": {},
                        },
                    }
                ),
            },
            {"event": "content_block_delta", "data": malformed_delta},
            {
                "event": "content_block_stop",
                "data": json.dumps({"type": "content_block_stop", "index": 0}),
            },
            {
                "event": "message_delta",
                "data": json.dumps(
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use"},
                        "usage": {
                            "input_tokens": 12,
                            "output_tokens": 5,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    }
                ),
            },
            {
                "event": "message_stop",
                "data": json.dumps({"type": "message_stop"}),
            },
        ]

        client = await _make_mock_client(events)
        s = stream_anthropic(model, context, AnthropicOptions(client=client))
        result = await s.result()

        assert result.stop_reason == "toolUse"
        assert result.error_message is None

        tool_call = next((b for b in result.content if b.type == "toolCall"), None)
        assert tool_call is not None
        assert tool_call.arguments == {"path": "A\\H", "text": "col1\tcol2"}

    @pytest.mark.asyncio
    async def test_ignores_unknown_sse_events_after_message_stop(self) -> None:
        """Unknown SSE events after message_stop are ignored."""
        model = get_model("anthropic", "claude-haiku-4-5")
        assert model is not None
        context = _make_context("Say hello.")

        events = _minimal_anthropic_events("Hello")
        events.append({"event": "done", "data": "[DONE]"})
        events.append({"event": "proxy.stats", "data": "not json"})

        client = await _make_mock_client(events)
        s = stream_anthropic(model, context, AnthropicOptions(client=client))
        result = await s.result()

        assert result.stop_reason == "stop"
        assert result.error_message is None
        assert len(result.content) == 1
        assert result.content[0].type == "text"
        assert result.content[0].text == "Hello"


# ---------------------------------------------------------------------------
# Test: Thinking disable payload
# ---------------------------------------------------------------------------


class TestThinkingDisable:
    """Port of anthropic-thinking-disable.test.ts."""

    @pytest.mark.asyncio
    async def test_disabled_for_budget_model(self) -> None:
        """sends thinking.type=disabled for budget-based reasoning models."""
        model = get_model("anthropic", "claude-sonnet-4-5")
        assert model is not None

        captured_params: dict[str, Any] = {}

        async def _capture_create(params: Any, **kwargs: Any) -> MagicMock:
            if isinstance(params, dict):
                captured_params.update(params)
            return _make_sse_response(_minimal_anthropic_events("Hi"))

        client = MagicMock()
        client.create_message = AsyncMock(side_effect=_capture_create)

        with patch(
            "cortex.ai.providers.anthropic.create_client",
            return_value=(client, False),
        ):
            s = stream_simple_anthropic(
                model,
                _make_context(),
                SimpleStreamOptions(api_key="fake-key"),
            )
            await s.result()

        assert captured_params.get("thinking") == {"type": "disabled"}
        assert "output_config" not in captured_params

    @pytest.mark.asyncio
    async def test_disabled_for_adaptive_model(self) -> None:
        """sends thinking.type=disabled for adaptive reasoning models."""
        model = get_model("anthropic", "claude-opus-4-6")
        assert model is not None

        captured_params: dict[str, Any] = {}

        async def _capture_create(params: Any, **kwargs: Any) -> MagicMock:
            if isinstance(params, dict):
                captured_params.update(params)
            return _make_sse_response(_minimal_anthropic_events("Hi"))

        client = MagicMock()
        client.create_message = AsyncMock(side_effect=_capture_create)

        with patch(
            "cortex.ai.providers.anthropic.create_client",
            return_value=(client, False),
        ):
            s = stream_simple_anthropic(
                model,
                _make_context(),
                SimpleStreamOptions(api_key="fake-key"),
            )
            await s.result()

        assert captured_params.get("thinking") == {"type": "disabled"}
        assert "output_config" not in captured_params

    @pytest.mark.asyncio
    async def test_adaptive_thinking_for_opus_4_7(self) -> None:
        """uses adaptive thinking for Claude Opus 4.7 when reasoning is enabled."""
        model = get_model("anthropic", "claude-opus-4-7")
        assert model is not None

        captured_params: dict[str, Any] = {}

        async def _capture_create(params: Any, **kwargs: Any) -> MagicMock:
            if isinstance(params, dict):
                captured_params.update(params)
            return _make_sse_response(_minimal_anthropic_events("Hi"))

        client = MagicMock()
        client.create_message = AsyncMock(side_effect=_capture_create)

        with patch(
            "cortex.ai.providers.anthropic.create_client",
            return_value=(client, False),
        ):
            s = stream_simple_anthropic(
                model,
                _make_context(),
                SimpleStreamOptions(api_key="fake-key", reasoning="high"),
            )
            await s.result()

        assert captured_params.get("thinking") == {
            "type": "adaptive",
            "display": "summarized",
        }
        assert captured_params.get("output_config") == {"effort": "high"}

    @pytest.mark.asyncio
    async def test_opus_4_8_default_display_omitted(self) -> None:
        """Opus 4.8 defaults to display 'omitted'."""
        model = get_model("anthropic", "claude-opus-4-8")
        assert model is not None

        captured_params: dict[str, Any] = {}

        async def _capture_create(params: Any, **kwargs: Any) -> MagicMock:
            if isinstance(params, dict):
                captured_params.update(params)
            return _make_sse_response(_minimal_anthropic_events("Hi"))

        client = MagicMock()
        client.create_message = AsyncMock(side_effect=_capture_create)

        with patch(
            "cortex.ai.providers.anthropic.create_client",
            return_value=(client, False),
        ):
            s = stream_simple_anthropic(
                model,
                _make_context(),
                SimpleStreamOptions(api_key="fake-key", reasoning="high"),
            )
            await s.result()

        assert captured_params.get("thinking") == {
            "type": "adaptive",
            "display": "omitted",
        }
        assert captured_params.get("output_config") == {"effort": "high"}


# ---------------------------------------------------------------------------
# Test: Eager tool input streaming
# ---------------------------------------------------------------------------


class TestEagerToolInput:
    """Port of anthropic-eager-tool-input-compat.test.ts."""

    @pytest.mark.asyncio
    async def test_sends_eager_input_streaming_by_default(self) -> None:
        """Per-tool eager_input_streaming is sent by default."""
        model = get_model("anthropic", "claude-opus-4-7")
        assert model is not None

        captured_body: dict[str, Any] = {}

        async def _capture_create(params: Any, **kwargs: Any) -> MagicMock:
            if isinstance(params, dict):
                captured_body.update(params)
            return _make_sse_response(
                _minimal_anthropic_events("", stop_reason="end_turn", output_tokens=0)
            )

        client = MagicMock()
        client.create_message = AsyncMock(side_effect=_capture_create)

        context = _make_context(
            "Use the tool",
            tools=[
                Tool(
                    name="lookup",
                    description="Look up a value",
                    parameters={
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                    },
                ),
            ],
        )

        with patch(
            "cortex.ai.providers.anthropic.create_client",
            return_value=(client, False),
        ):
            s = stream_anthropic(
                model,
                context,
                AnthropicOptions(api_key="test-key", cache_retention="none"),
            )
            await s.result()

        tools = captured_body.get("tools", [])
        assert len(tools) > 0
        assert tools[0].get("eager_input_streaming") is True

    @pytest.mark.asyncio
    async def test_no_beta_header_when_eager_enabled(self) -> None:
        """No fine-grained-tool-streaming beta when eager tool input streaming is enabled."""
        model = get_model("anthropic", "claude-opus-4-7")
        assert model is not None

        async def _capture_create(params: Any, **kwargs: Any) -> MagicMock:
            return _make_sse_response(
                _minimal_anthropic_events("", stop_reason="end_turn", output_tokens=0)
            )

        def _mock_create_client(
            model: Any,
            api_key: str,
            interleaved_thinking: bool,
            use_fine_grained: bool,
            options_headers: Any = None,
            dynamic_headers: Any = None,
        ) -> tuple[MagicMock, bool]:
            client = MagicMock()
            client.create_message = AsyncMock(side_effect=_capture_create)
            return client, False

        context = _make_context(
            "Use the tool",
            tools=[
                Tool(
                    name="lookup",
                    description="Look up a value",
                    parameters={
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                    },
                ),
            ],
        )

        with patch(
            "cortex.ai.providers.anthropic.create_client",
            side_effect=_mock_create_client,
        ):
            s = stream_anthropic(
                model,
                context,
                AnthropicOptions(api_key="test-key", cache_retention="none"),
            )
            await s.result()

        # Default compat has supports_eager_tool_input_streaming=True,
        # so no beta header should be needed


# ---------------------------------------------------------------------------
# Test: Copilot via Anthropic Messages
# ---------------------------------------------------------------------------


class TestCopilotAnthropic:
    """Port of github-copilot-anthropic.test.ts."""

    @pytest.mark.asyncio
    async def test_copilot_uses_bearer_auth_and_headers(self) -> None:
        """Copilot uses Bearer auth, Copilot headers, and valid Anthropic payload."""
        model = get_model("github-copilot", "claude-sonnet-4.5")
        assert model is not None
        assert model.api == "anthropic-messages"

        context = _make_context("Hello", system_prompt="You are a helpful assistant.")

        captured_client_kwargs: dict[str, Any] = {}
        captured_params: dict[str, Any] = {}

        async def _capture_create(params: Any, **kwargs: Any) -> MagicMock:
            if isinstance(params, dict):
                captured_params.update(params)
            return _make_sse_response(_minimal_anthropic_events("Hi"))

        def _capture_client(**kwargs: Any) -> MagicMock:
            captured_client_kwargs.update(kwargs)
            client = MagicMock()
            client.create_message = AsyncMock(side_effect=_capture_create)
            return client

        with patch(
            "cortex.ai.providers.anthropic.AnthropicClient",
            side_effect=_capture_client,
        ):
            s = stream_anthropic(
                model,
                context,
                AnthropicOptions(api_key="tid_copilot_session_test_token"),
            )
            await s.result()

        # Verify Bearer auth
        assert captured_client_kwargs.get("auth_token") == "tid_copilot_session_test_token"
        assert captured_client_kwargs.get("api_key") is None

        # Verify headers
        headers = captured_client_kwargs.get("default_headers", {})
        ua = headers.get("User-Agent", "")
        assert "GitHubCopilotChat" in ua or "github-copilot" in ua.lower()
        assert headers.get("Copilot-Integration-Id") == "vscode-chat"

        # Verify payload
        assert captured_params.get("model") == "claude-sonnet-4.5"
        assert captured_params.get("stream") is True
        assert isinstance(captured_params.get("messages"), list)

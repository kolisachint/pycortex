"""Tests for `cortex.ai.providers.google.vertex`.

Ports the API key resolution tests from
``google-vertex-api-key-resolution.test.ts`` and adds request-shaping tests
against ``build_params`` (same pattern as ``test_google.py``).

The TS side uses the ``@google/genai`` SDK which is mocked in the tests.
Here the HTTP client is injectable, so we pass a ``FakeClient`` that records
the params it receives and yields a canned response — no network, no keys.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from cortex.ai.providers.google import (
    build_params,
    stream_google_vertex,
    stream_simple_google_vertex,
)
from cortex.ai.providers.google.vertex import (
    GoogleVertexClient,
    create_client,
)
from cortex.ai.types import Context, Model, UserMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def collect(stream: Any) -> tuple[list[Any], Any]:
    events = [event async for event in stream]
    return events, await stream.result()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_model(model_id: str = "gemini-3-flash-preview", reasoning: bool = True) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="google-vertex",
        provider="google-vertex",
        base_url="https://{location}-aiplatform.googleapis.com",
        reasoning=reasoning,
        input=["text", "image"],
        cost={"input": 1.0, "output": 2.0, "cacheRead": 0.5, "cacheWrite": 0.0},
        context_window=128000,
        max_tokens=8192,
    )


def make_context(system_prompt: str | None = None) -> Context:
    return Context(
        system_prompt=system_prompt,
        messages=[UserMessage(content="hello", timestamp=int(time.time() * 1000))],
    )


def make_context_with_tools(tools: list[Any]) -> Context:
    return Context(
        messages=[UserMessage(content="hi", timestamp=int(time.time() * 1000))],
        tools=tools,
    )


class FakeClient:
    """Yields a canned chunk sequence and records the params it was called with."""

    _DEFAULT_CHUNKS: list[dict[str, Any]] = [
        {
            "responseId": "vertex-response-id",
            "candidates": [
                {
                    "content": {"parts": [{"text": "ok"}]},
                    "finishReason": "STOP",
                },
            ],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 1,
                "totalTokenCount": 2,
            },
        }
    ]

    def __init__(self, chunks: list[dict[str, Any]] | None = None) -> None:
        # If chunks is explicitly passed (even if empty), use it; otherwise use default
        self.chunks = chunks if chunks is not None else self._DEFAULT_CHUNKS
        self.params: dict[str, Any] | None = None
        self.base_url: str | None = None
        self.api_key: str | None = None

    async def stream_generate_content(self, params: dict[str, Any]):  # noqa: ANN202
        self.params = params
        for chunk in self.chunks:
            yield chunk


# ---------------------------------------------------------------------------
# Client creation tests (port of google-vertex-api-key-resolution.test.ts)
# ---------------------------------------------------------------------------


class TestClientCreation:
    """Tests that verify the correct client is created based on credentials."""

    def test_uses_api_key_when_provided(self) -> None:
        model = make_model()
        client = create_client(
            model,
            options={
                "api_key": "AIzaSyRealKey123",
                "project": "p",
                "location": "l",
            },
        )
        assert isinstance(client, GoogleVertexClient)
        assert client.api_key == "AIzaSyRealKey123"

    def test_falls_back_to_adc_for_placeholder_key(self) -> None:
        model = make_model()
        client = create_client(
            model,
            options={
                "api_key": "<authenticated>",
                "project": "test-project",
                "location": "us-central1",
            },
        )
        # ADC client should not have an API key
        assert isinstance(client, GoogleVertexClient)
        assert client.api_key is None
        assert "us-central1" in client.base_url

    def test_falls_back_to_adc_for_gcp_credentials_marker(self) -> None:
        model = make_model()
        client = create_client(
            model,
            options={
                "api_key": "gcp-vertex-credentials",
                "project": "test-project",
                "location": "us-central1",
            },
        )
        assert client.api_key is None
        assert "us-central1" in client.base_url

    def test_falls_back_to_adc_for_env_placeholder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_CLOUD_API_KEY", "<authenticated>")
        model = make_model()
        client = create_client(
            model,
            options={
                "project": "test-project",
                "location": "us-central1",
            },
        )
        assert client.api_key is None
        assert "us-central1" in client.base_url

    def test_still_uses_api_key_for_real_keys(self) -> None:
        model = make_model()
        client = create_client(
            model,
            options={
                "api_key": "AIzaSyExampleRealisticLookingApiKey123456",
            },
        )
        assert client.api_key == "AIzaSyExampleRealisticLookingApiKey123456"

    def test_adc_client_uses_custom_base_url(self) -> None:
        model = make_model()
        model.base_url = "https://proxy.example.com"
        client = create_client(
            model,
            options={
                "project": "test-project",
                "location": "us-central1",
            },
        )
        assert client.base_url == "https://proxy.example.com"

    def test_api_key_client_uses_custom_base_url(self) -> None:
        model = make_model()
        model.base_url = "https://proxy.example.com"
        client = create_client(
            model,
            options={
                "api_key": "AIzaSyExampleRealisticLookingApiKey123456",
            },
        )
        assert client.base_url == "https://proxy.example.com"
        assert client.api_key == "AIzaSyExampleRealisticLookingApiKey123456"

    def test_does_not_forward_location_placeholder_in_base_url(self) -> None:
        model = make_model()
        # Default base_url has {location} placeholder
        client = create_client(
            model,
            options={
                "project": "test-project",
                "location": "us-central1",
            },
        )
        # Should use default endpoint with location resolved
        assert "us-central1" in client.base_url
        assert "{location}" not in client.base_url

    def test_forwards_model_headers(self) -> None:
        model = make_model()
        model.headers = {"X-Custom": "value"}
        client = create_client(
            model,
            options={"project": "p", "location": "l"},
        )
        assert client.headers.get("X-Custom") == "value"

    def test_forwards_options_headers(self) -> None:
        model = make_model()
        client = create_client(
            model,
            options={
                "project": "p",
                "location": "l",
                "headers": {"X-Option": "val"},
            },
        )
        assert client.headers.get("X-Option") == "val"

    def test_merges_model_and_options_headers(self) -> None:
        model = make_model()
        model.headers = {"X-Model": "m"}
        client = create_client(
            model,
            options={
                "project": "p",
                "location": "l",
                "headers": {"X-Option": "o"},
            },
        )
        assert client.headers == {"X-Model": "m", "X-Option": "o"}


# ---------------------------------------------------------------------------
# Build params tests (ported from TS, same pattern as test_google.py)
# ---------------------------------------------------------------------------


class TestBuildParams:
    def test_includes_model_and_contents(self) -> None:
        params = build_params(make_model(), make_context())
        assert params["model"] == "gemini-3-flash-preview"
        assert params["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]

    def test_generation_config_only_when_set(self) -> None:
        config = build_params(make_model(), make_context())["config"]
        assert "temperature" not in config
        assert "maxOutputTokens" not in config

        config = build_params(make_model(), make_context(), {"temperature": 0.5, "max_tokens": 64})[
            "config"
        ]
        assert config["temperature"] == 0.5
        assert config["maxOutputTokens"] == 64

    def test_system_instruction(self) -> None:
        config = build_params(make_model(), make_context(system_prompt="be terse"))["config"]
        assert config["systemInstruction"] == "be terse"

    def test_tool_config_requires_both_tools_and_choice(self) -> None:
        from cortex.ai.types import Tool

        tools = [Tool(name="t", description="d", parameters={"type": "object"})]

        config = build_params(make_model(), make_context_with_tools(tools))["config"]
        assert config["toolConfig"] is None

        config = build_params(make_model(), make_context_with_tools(tools), {"tool_choice": "any"})[
            "config"
        ]
        assert config["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}

    def test_raises_when_signal_aborted(self) -> None:
        class Aborted:
            aborted = True

        with pytest.raises(RuntimeError, match="aborted"):
            build_params(make_model(), make_context(), {"signal": Aborted()})


class TestThinkingConfig:
    def test_enabled_with_budget(self) -> None:
        config = build_params(
            make_model(), make_context(), {"thinking": {"enabled": True, "budget_tokens": 2048}}
        )["config"]
        assert config["thinkingConfig"] == {
            "includeThoughts": True,
            "thinkingBudget": 2048,
        }

    def test_level_wins_over_budget(self) -> None:
        config = build_params(
            make_model("gemini-3-pro-preview"),
            make_context(),
            {"thinking": {"enabled": True, "level": "HIGH", "budget_tokens": 2048}},
        )["config"]
        assert config["thinkingConfig"] == {
            "includeThoughts": True,
            "thinkingLevel": "HIGH",
        }

    def test_no_thinking_config_for_non_reasoning_models(self) -> None:
        config = build_params(
            make_model(reasoning=False), make_context(), {"thinking": {"enabled": True}}
        )["config"]
        assert "thinkingConfig" not in config

    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            # Gemini 2.x can genuinely turn thinking off.
            ("gemini-2.5-flash", {"thinkingBudget": 0}),
            # Gemini 3 cannot, so it gets the lowest level and no includeThoughts.
            ("gemini-3-pro-preview", {"thinkingLevel": "LOW"}),
            ("gemini-3-flash-preview", {"thinkingLevel": "MINIMAL"}),
            ("gemma-4-27b", {"thinkingLevel": "MINIMAL"}),
        ],
    )
    def test_disabled_thinking_per_family(self, model_id: str, expected: dict[str, Any]) -> None:
        config = build_params(
            make_model(model_id), make_context(), {"thinking": {"enabled": False}}
        )["config"]
        assert config["thinkingConfig"] == expected
        assert "includeThoughts" not in config["thinkingConfig"]


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestStreamVertex:
    async def test_basic_text_response(self) -> None:
        client = FakeClient()
        model = make_model()
        ctx = make_context()

        _, result = await collect(
            stream_google_vertex(model, ctx, {"client": client, "project": "p", "location": "l"})
        )

        assert result.stop_reason == "stop"
        assert len(result.content) == 1
        assert result.content[0].type == "text"
        assert result.content[0].text == "ok"
        assert client.params is not None
        assert client.params["model"] == "gemini-3-flash-preview"

    async def test_empty_response(self) -> None:
        # Empty chunks list means the stream will yield nothing and end
        client = FakeClient(chunks=[])
        model = make_model()
        ctx = make_context()

        _, result = await collect(
            stream_google_vertex(model, ctx, {"client": client, "project": "p", "location": "l"})
        )

        # Empty response still has stop_reason set
        assert result.stop_reason == "stop"
        assert len(result.content) == 0

    async def test_api_error_response(self) -> None:
        class FailingClient:
            async def stream_generate_content(self, params: dict[str, Any]):  # noqa: ANN202
                raise RuntimeError("API error 403: Forbidden")

        model = make_model()
        ctx = make_context()

        _, result = await collect(
            stream_google_vertex(
                model,
                ctx,
                {"client": FailingClient(), "project": "p", "location": "l"},
            )
        )

        assert result.stop_reason == "error"
        assert result.error_message is not None


# ---------------------------------------------------------------------------
# Simple stream tests
# ---------------------------------------------------------------------------


class TestStreamSimpleVertex:
    async def test_without_reasoning(self) -> None:
        client = FakeClient()
        model = make_model()
        ctx = make_context()

        _, result = await collect(
            stream_simple_google_vertex(
                model,
                ctx,
                {
                    "client": client,
                    "project": "p",
                    "location": "l",
                    "api_key": "AIzaSyRealKey123",
                },
            )
        )

        assert result.stop_reason == "stop"
        assert client.params is not None
        thinking_config = client.params["config"].get("thinkingConfig", {})
        # Without reasoning, thinking should be disabled or not includeThoughts
        assert thinking_config is None or thinking_config.get("includeThoughts") is not True

    async def test_with_reasoning_gemini3(self) -> None:
        client = FakeClient()
        model = make_model(model_id="gemini-3-flash-preview")
        ctx = make_context()

        _, result = await collect(
            stream_simple_google_vertex(
                model,
                ctx,
                {
                    "client": client,
                    "project": "p",
                    "location": "l",
                    "api_key": "AIzaSyRealKey123",
                    "reasoning": "medium",
                },
            )
        )

        assert result.stop_reason == "stop"
        assert client.params is not None
        thinking_config = client.params["config"].get("thinkingConfig", {})
        assert thinking_config.get("includeThoughts") is True
        # medium maps to MEDIUM for gemini-3-flash (HIGH only for gemini-3-pro)
        assert thinking_config.get("thinkingLevel") == "MEDIUM"

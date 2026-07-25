"""Tests for `cortex.ai.providers.google.google`.

The TS side covers this provider almost entirely through e2e suites that need a
real `GEMINI_API_KEY` (`google-thinking-disable.test.ts`). Those are not
portable, so the request-shaping half is asserted directly against
`build_params`, and the streaming half runs through an injected fake client —
no network, no keys.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from cortex.ai.providers.google import build_params, stream_google, stream_simple_google
from cortex.ai.types import Context, Model, Tool, UserMessage


def make_model(model_id: str = "gemini-2.5-flash", reasoning: bool = True) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="google-generative-ai",
        provider="google",
        base_url="",
        reasoning=reasoning,
        input=["text", "image"],
        cost={"input": 1.0, "output": 2.0, "cacheRead": 0.5, "cacheWrite": 0.0},
        context_window=128000,
        max_tokens=8192,
    )


def make_context(system_prompt: str | None = None, tools: list[Tool] | None = None) -> Context:
    return Context(
        system_prompt=system_prompt,
        messages=[UserMessage(content="hi", timestamp=int(time.time() * 1000))],
        tools=tools,
    )


class FakeClient:
    """Yields a canned chunk sequence and records the params it was called with."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks
        self.params: dict[str, Any] | None = None

    async def stream_generate_content(self, params: dict[str, Any]):  # noqa: ANN201
        self.params = params
        for chunk in self.chunks:
            yield chunk


async def collect(stream: Any) -> tuple[list[Any], Any]:
    events = [event async for event in stream]
    return events, await stream.result()


class TestBuildParams:
    def test_includes_model_and_contents(self) -> None:
        params = build_params(make_model(), make_context())
        assert params["model"] == "gemini-2.5-flash"
        assert params["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]

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
        tools = [Tool(name="t", description="d", parameters={"type": "object"})]

        config = build_params(make_model(), make_context(tools=tools))["config"]
        assert config["toolConfig"] is None

        config = build_params(make_model(), make_context(tools=tools), {"tool_choice": "any"})[
            "config"
        ]
        assert config["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}

        # A tool choice without tools is ignored.
        config = build_params(make_model(), make_context(), {"tool_choice": "any"})["config"]
        assert config["toolConfig"] is None

    def test_raises_when_the_signal_is_already_aborted(self) -> None:
        class Aborted:
            aborted = True

        with pytest.raises(RuntimeError, match="Request aborted"):
            build_params(make_model(), make_context(), {"signal": Aborted()})


class TestThinkingConfig:
    def test_enabled_with_budget(self) -> None:
        config = build_params(
            make_model(), make_context(), {"thinking": {"enabled": True, "budget_tokens": 2048}}
        )["config"]
        assert config["thinkingConfig"] == {"includeThoughts": True, "thinkingBudget": 2048}

    def test_level_wins_over_budget(self) -> None:
        config = build_params(
            make_model("gemini-3-pro-preview"),
            make_context(),
            {"thinking": {"enabled": True, "level": "HIGH", "budget_tokens": 2048}},
        )["config"]
        assert config["thinkingConfig"] == {"includeThoughts": True, "thinkingLevel": "HIGH"}

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


class TestStreamSimpleThinkingLevels:
    """`stream_simple_google` maps a reasoning level onto level-or-budget."""

    def _params_for(self, model_id: str, reasoning: str | None) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def on_payload(params: dict[str, Any], _model: Model) -> None:
            captured.update(params)

        client = FakeClient([{"candidates": [{"finishReason": "STOP"}]}])
        stream = stream_simple_google(
            make_model(model_id),
            make_context(),
            {
                "api_key": "test-key",
                "reasoning": reasoning,
                "client": client,
                "on_payload": on_payload,
            },
        )
        return {"stream": stream, "captured": captured}

    async def test_no_reasoning_disables_thinking(self) -> None:
        result = self._params_for("gemini-2.5-flash", None)
        await collect(result["stream"])
        assert result["captured"]["config"]["thinkingConfig"] == {"thinkingBudget": 0}

    @pytest.mark.parametrize(
        ("model_id", "reasoning", "expected_level"),
        [
            ("gemini-3-pro-preview", "low", "LOW"),
            ("gemini-3-pro-preview", "medium", "HIGH"),
            ("gemini-3-flash-preview", "minimal", "MINIMAL"),
            ("gemma-4-27b", "low", "MINIMAL"),
        ],
    )
    async def test_level_based_families(
        self, model_id: str, reasoning: str, expected_level: str
    ) -> None:
        result = self._params_for(model_id, reasoning)
        await collect(result["stream"])
        assert result["captured"]["config"]["thinkingConfig"] == {
            "includeThoughts": True,
            "thinkingLevel": expected_level,
        }

    @pytest.mark.parametrize(
        ("model_id", "reasoning", "expected_budget"),
        [
            ("gemini-2.5-pro", "high", 32768),
            ("gemini-2.5-flash", "medium", 8192),
            ("gemini-2.5-flash-lite", "minimal", 512),
            # Unknown 2.x model: -1 asks Gemini for a dynamic budget.
            ("gemini-2.0-flash", "high", -1),
        ],
    )
    async def test_budget_based_families(
        self, model_id: str, reasoning: str, expected_budget: int
    ) -> None:
        result = self._params_for(model_id, reasoning)
        await collect(result["stream"])
        assert result["captured"]["config"]["thinkingConfig"] == {
            "includeThoughts": True,
            "thinkingBudget": expected_budget,
        }

    def test_requires_an_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(ValueError, match="No API key"):
            stream_simple_google(make_model(), make_context(), {})


class TestStreaming:
    async def test_text_events(self) -> None:
        client = FakeClient(
            [
                {
                    "responseId": "resp-1",
                    "candidates": [{"content": {"parts": [{"text": "Hello"}]}}],
                },
                {
                    "candidates": [
                        {"content": {"parts": [{"text": " world"}]}, "finishReason": "STOP"}
                    ]
                },
            ]
        )
        events, result = await collect(
            stream_google(make_model(), make_context(), {"client": client, "api_key": "k"})
        )

        types = [event.type for event in events]
        assert types == ["start", "text_start", "text_delta", "text_delta", "text_end", "done"]
        assert result.content[0].text == "Hello world"
        assert result.stop_reason == "stop"
        assert result.response_id == "resp-1"

    async def test_thinking_and_text_are_separate_blocks(self) -> None:
        client = FakeClient(
            [
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": "pondering",
                                        "thought": True,
                                        "thoughtSignature": "s1",
                                    },
                                    {"text": "answer"},
                                ]
                            },
                            "finishReason": "STOP",
                        }
                    ]
                }
            ]
        )
        _, result = await collect(
            stream_google(make_model(), make_context(), {"client": client, "api_key": "k"})
        )

        assert [block.type for block in result.content] == ["thinking", "text"]
        assert result.content[0].thinking == "pondering"
        assert result.content[0].thinking_signature == "s1"
        assert result.content[1].text == "answer"

    async def test_signature_survives_a_delta_that_omits_it(self) -> None:
        client = FakeClient(
            [
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": "a", "thought": True, "thoughtSignature": "s1"}]
                            }
                        }
                    ]
                },
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "b", "thought": True}]},
                            "finishReason": "STOP",
                        }
                    ]
                },
            ]
        )
        _, result = await collect(
            stream_google(make_model(), make_context(), {"client": client, "api_key": "k"})
        )
        assert result.content[0].thinking == "ab"
        assert result.content[0].thinking_signature == "s1"

    async def test_tool_call_events_and_stop_reason(self) -> None:
        client = FakeClient(
            [
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "functionCall": {
                                            "id": "call_1",
                                            "name": "bash",
                                            "args": {"command": "ls"},
                                        }
                                    }
                                ]
                            },
                            "finishReason": "STOP",
                        }
                    ]
                }
            ]
        )
        events, result = await collect(
            stream_google(make_model(), make_context(), {"client": client, "api_key": "k"})
        )

        assert [event.type for event in events] == [
            "start",
            "toolcall_start",
            "toolcall_delta",
            "toolcall_end",
            "done",
        ]
        tool_call = result.content[0]
        assert tool_call.id == "call_1"
        assert tool_call.arguments == {"command": "ls"}
        # A finished tool call overrides STOP.
        assert result.stop_reason == "toolUse"

    async def test_duplicate_tool_call_ids_are_regenerated(self) -> None:
        call = {"functionCall": {"id": "same", "name": "bash", "args": {}}}
        client = FakeClient(
            [{"candidates": [{"content": {"parts": [call, call]}, "finishReason": "STOP"}]}]
        )
        _, result = await collect(
            stream_google(make_model(), make_context(), {"client": client, "api_key": "k"})
        )
        assert result.content[0].id == "same"
        assert result.content[1].id != "same"
        assert result.content[1].id.startswith("bash_")

    async def test_missing_tool_call_id_is_generated(self) -> None:
        client = FakeClient(
            [
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"functionCall": {"name": "bash", "args": {}}}]},
                            "finishReason": "STOP",
                        }
                    ]
                }
            ]
        )
        _, result = await collect(
            stream_google(make_model(), make_context(), {"client": client, "api_key": "k"})
        )
        assert result.content[0].id.startswith("bash_")

    async def test_usage_and_cost(self) -> None:
        client = FakeClient(
            [
                {
                    "candidates": [{"finishReason": "STOP"}],
                    "usageMetadata": {
                        "promptTokenCount": 1000,
                        "cachedContentTokenCount": 200,
                        "candidatesTokenCount": 50,
                        "thoughtsTokenCount": 25,
                        "totalTokenCount": 1075,
                    },
                }
            ]
        )
        _, result = await collect(
            stream_google(make_model(), make_context(), {"client": client, "api_key": "k"})
        )
        # Cached tokens are billed separately, so they come out of `input`.
        assert result.usage.input == 800
        assert result.usage.output == 75
        assert result.usage.cache_read == 200
        assert result.usage.total_tokens == 1075
        assert result.usage.cost["total"] > 0

    async def test_error_finish_reason_becomes_an_error_event(self) -> None:
        client = FakeClient([{"candidates": [{"finishReason": "SAFETY"}]}])
        events, result = await collect(
            stream_google(make_model(), make_context(), {"client": client, "api_key": "k"})
        )
        assert events[-1].type == "error"
        assert result.stop_reason == "error"
        assert result.error_message

    async def test_client_failure_becomes_an_error_event(self) -> None:
        class Boom:
            async def stream_generate_content(self, params: dict[str, Any]):  # noqa: ANN201
                raise RuntimeError("connection reset")
                yield  # pragma: no cover — makes this an async generator

        events, result = await collect(
            stream_google(make_model(), make_context(), {"client": Boom(), "api_key": "k"})
        )
        assert events[-1].type == "error"
        assert result.error_message == "connection reset"

    async def test_abort_is_reported_as_aborted(self) -> None:
        class Signal:
            aborted = False

        signal = Signal()

        class AbortingClient:
            async def stream_generate_content(self, params: dict[str, Any]):  # noqa: ANN201
                signal.aborted = True
                yield {"candidates": [{"content": {"parts": [{"text": "partial"}]}}]}

        _, result = await collect(
            stream_google(
                make_model(),
                make_context(),
                {"client": AbortingClient(), "api_key": "k", "signal": signal},
            )
        )
        assert result.stop_reason == "aborted"

    async def test_on_payload_can_replace_the_request(self) -> None:
        client = FakeClient([{"candidates": [{"finishReason": "STOP"}]}])

        def on_payload(params: dict[str, Any], _model: Model) -> dict[str, Any]:
            return {**params, "contents": [{"role": "user", "parts": [{"text": "replaced"}]}]}

        await collect(
            stream_google(
                make_model(),
                make_context(),
                {"client": client, "api_key": "k", "on_payload": on_payload},
            )
        )
        assert client.params is not None
        assert client.params["contents"][0]["parts"][0]["text"] == "replaced"

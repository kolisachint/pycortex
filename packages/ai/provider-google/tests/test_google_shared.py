"""Ported from hoocode's google-shared-*.test.ts.

Covers `google-shared-convert-tools.test.ts`,
`google-shared-image-tool-result-routing.test.ts`,
`google-shared-gemini3-unsigned-tool-call.test.ts` and
`google-thinking-signature.test.ts`. The two thinking-*disable* TS suites are
e2e (real API keys) and are not portable; the request-shaping half of what they
assert is covered by `test_google.py::TestThinkingConfig` instead.
"""

from __future__ import annotations

import json
import time
from typing import Any

from cortex.ai.providers.google import (
    convert_messages,
    convert_tools,
    is_thinking_part,
    map_stop_reason,
    map_stop_reason_string,
    map_tool_choice,
    requires_tool_call_id,
    retain_thought_signature,
)
from cortex.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def make_model(
    model_id: str = "gemini-2.5-flash",
    api: str = "google-generative-ai",
    provider: str = "google",
) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api=api,
        provider=provider,
        base_url="https://example.com",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=128000,
        max_tokens=8192,
    )


def zero_usage() -> Usage:
    return Usage(
        input=0,
        output=0,
        cache_read=0,
        cache_write=0,
        total_tokens=0,
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
    )


def make_tool(parameters: dict[str, Any]) -> Any:
    from cortex.ai.types import Tool

    return Tool(name="test_tool", description="A test tool", parameters=parameters)


class TestConvertTools:
    def test_strips_meta_keys_when_use_parameters(self) -> None:
        tools = [
            make_tool(
                {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "$id": "urn:bash-tool",
                    "$comment": "A bash tool for demonstration",
                    "$defs": {"commandDef": {"type": "string"}},
                    "definitions": {"legacyDef": {"type": "number"}},
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                }
            )
        ]
        result = convert_tools(tools, True)
        assert result is not None
        decl = result[0]["functionDeclarations"][0]
        assert decl["parameters"] == {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        }

    def test_strips_nested_meta_keys(self) -> None:
        tools = [
            make_tool(
                {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "type": "object",
                    "properties": {
                        "deep": {
                            "$schema": "http://json-schema.org/draft-07/schema#",
                            "$id": "urn:nested",
                            "type": "string",
                        }
                    },
                }
            )
        ]
        result = convert_tools(tools, True)
        assert result is not None
        decl = result[0]["functionDeclarations"][0]
        assert decl["parameters"] == {
            "type": "object",
            "properties": {"deep": {"type": "string"}},
        }

    def test_defaults_to_parameters_json_schema_unmodified(self) -> None:
        schema = {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"}
        result = convert_tools([make_tool(schema)])
        assert result is not None
        decl = result[0]["functionDeclarations"][0]
        # parametersJsonSchema accepts full JSON Schema, so nothing is stripped.
        assert decl["parametersJsonSchema"] == schema
        assert "parameters" not in decl

    def test_returns_none_for_no_tools(self) -> None:
        assert convert_tools([]) is None


class TestThoughtSignatures:
    def test_thought_true_is_thinking(self) -> None:
        assert is_thinking_part({"thought": True}) is True
        assert is_thinking_part({"thought": True, "thoughtSignature": "opaque"}) is True

    def test_signature_alone_is_not_thinking(self) -> None:
        # Per Google's docs a thoughtSignature is for context replay and can
        # appear on any part; only `thought: true` marks thinking content.
        assert is_thinking_part({"thoughtSignature": "opaque"}) is False
        assert is_thinking_part({"thought": False, "thoughtSignature": "opaque"}) is False

    def test_missing_signature_is_not_thinking(self) -> None:
        assert is_thinking_part({}) is False
        assert is_thinking_part({"thought": False, "thoughtSignature": ""}) is False

    def test_retains_signature_when_later_deltas_omit_it(self) -> None:
        first = retain_thought_signature(None, "sig-1")
        assert first == "sig-1"
        second = retain_thought_signature(first, None)
        assert second == "sig-1"
        third = retain_thought_signature(second, "")
        assert third == "sig-1"

    def test_updates_on_new_non_empty_signature(self) -> None:
        assert retain_thought_signature("sig-1", "sig-2") == "sig-2"


def tool_call_context(model: Model, thought_signature: str | None = None) -> Context:
    now = int(time.time() * 1000)
    first_call = ToolCall(
        id="call_1",
        name="bash",
        arguments={"command": "echo hi"},
        thought_signature=thought_signature,
    )
    return Context(
        messages=[
            UserMessage(content="run", timestamp=now),
            AssistantMessage(
                content=[
                    first_call,
                    ToolCall(id="call_2", name="bash", arguments={"command": "ls -la"}),
                ],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=zero_usage(),
                stop_reason="toolUse",
                timestamp=now,
            ),
        ]
    )


class TestGemini3UnsignedToolCalls:
    def test_no_signature_for_a_different_model(self) -> None:
        model = make_model("gemini-3-pro-preview")
        other = make_model("other-model")
        contents = convert_messages(model, tool_call_context(other, "AAAAAAAAAAAAAAAAAAAAAA=="))

        model_turn = next(c for c in contents if c["role"] == "model")
        calls = [p for p in model_turn["parts"] if "functionCall" in p]
        assert len(calls) == 2
        assert all("thoughtSignature" not in p for p in calls)
        assert "skip_thought_signature_validator" not in json.dumps(model_turn)

    def test_preserves_valid_signature_for_same_provider_and_model(self) -> None:
        model = make_model("gemini-3-pro-preview")
        valid = "AAAAAAAAAAAAAAAAAAAAAA=="
        contents = convert_messages(model, tool_call_context(model, valid))

        model_turn = next(c for c in contents if c["role"] == "model")
        calls = [p for p in model_turn["parts"] if "functionCall" in p]
        assert calls[0].get("thoughtSignature") == valid
        assert "thoughtSignature" not in calls[1]

    def test_drops_signature_that_is_not_valid_base64(self) -> None:
        model = make_model("gemini-3-pro-preview")
        contents = convert_messages(model, tool_call_context(model, "not-base64!"))
        model_turn = next(c for c in contents if c["role"] == "model")
        calls = [p for p in model_turn["parts"] if "functionCall" in p]
        assert all("thoughtSignature" not in p for p in calls)

    def test_no_signature_for_non_gemini_3_models(self) -> None:
        model = make_model("gemini-2.5-flash")
        other = make_model("other-model")
        contents = convert_messages(model, tool_call_context(other))
        model_turn = next(c for c in contents if c["role"] == "model")
        call = next(p for p in model_turn["parts"] if "functionCall" in p)
        assert "thoughtSignature" not in call


def image_routing_context(model: Model) -> Context:
    now = int(time.time() * 1000)
    return Context(
        messages=[
            UserMessage(content="read the files", timestamp=now),
            AssistantMessage(
                content=[
                    ToolCall(id="call_a", name="read", arguments={"path": "a.txt"}),
                    ToolCall(id="call_img", name="read", arguments={"path": "image.png"}),
                    ToolCall(id="call_b", name="read", arguments={"path": "b.txt"}),
                ],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=zero_usage(),
                stop_reason="toolUse",
                timestamp=now,
            ),
            ToolResultMessage(
                tool_call_id="call_a",
                tool_name="read",
                content=[TextContent(text="alpha text")],
                is_error=False,
                timestamp=now,
            ),
            ToolResultMessage(
                tool_call_id="call_img",
                tool_name="read",
                content=[ImageContent(data="abc", mime_type="image/png")],
                is_error=False,
                timestamp=now,
            ),
            ToolResultMessage(
                tool_call_id="call_b",
                tool_name="read",
                content=[TextContent(text="beta text")],
                is_error=False,
                timestamp=now,
            ),
        ]
    )


class TestImageToolResultRouting:
    def test_gemini_2x_gets_a_separate_synthetic_image_turn(self) -> None:
        model = make_model("gemini-2.5-flash")
        contents = convert_messages(model, image_routing_context(model))

        assert len(contents) == 5
        assert all("functionResponse" in part for part in contents[2]["parts"])
        assert contents[3]["parts"][0]["text"] == "Tool result image:"
        assert contents[3]["parts"][1]["inlineData"]
        assert contents[4]["parts"][0]["functionResponse"]

    def test_gemini_3_nests_images_in_the_function_response(self) -> None:
        model = make_model("gemini-3-pro-preview")
        contents = convert_messages(model, image_routing_context(model))

        assert len(contents) == 3
        tool_result_turn = contents[2]
        assert len(tool_result_turn["parts"]) == 3
        image_response = tool_result_turn["parts"][1]["functionResponse"]
        assert len(image_response["parts"]) == 1
        assert image_response["parts"][0]["inlineData"]

    def test_images_are_dropped_when_the_model_has_no_image_input(self) -> None:
        model = make_model("gemini-2.5-flash")
        model.input = ["text"]
        contents = convert_messages(model, image_routing_context(model))
        assert "inlineData" not in json.dumps(contents)


class TestMessageConversion:
    def test_error_tool_results_use_the_error_key(self) -> None:
        now = int(time.time() * 1000)
        model = make_model()
        context = Context(
            messages=[
                ToolResultMessage(
                    tool_call_id="c1",
                    tool_name="bash",
                    content=[TextContent(text="boom")],
                    is_error=True,
                    timestamp=now,
                )
            ]
        )
        contents = convert_messages(model, context)
        response = contents[0]["parts"][0]["functionResponse"]["response"]
        assert response == {"error": "boom"}

    def test_thinking_from_another_model_is_downgraded_to_text(self) -> None:
        from cortex.ai.types import ThinkingContent

        now = int(time.time() * 1000)
        model = make_model("gemini-2.5-flash")
        context = Context(
            messages=[
                AssistantMessage(
                    content=[ThinkingContent(thinking="secret reasoning")],
                    api=model.api,
                    provider=model.provider,
                    model="a-different-model",
                    usage=zero_usage(),
                    stop_reason="stop",
                    timestamp=now,
                )
            ]
        )
        part = convert_messages(model, context)[0]["parts"][0]
        assert part == {"text": "secret reasoning"}
        assert "thought" not in part

    def test_empty_text_and_thinking_blocks_are_skipped(self) -> None:
        from cortex.ai.types import ThinkingContent

        now = int(time.time() * 1000)
        model = make_model()
        context = Context(
            messages=[
                AssistantMessage(
                    content=[TextContent(text="   "), ThinkingContent(thinking="")],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                    usage=zero_usage(),
                    stop_reason="stop",
                    timestamp=now,
                )
            ]
        )
        # Every block was empty, so the turn itself is dropped.
        assert convert_messages(model, context) == []

    def test_tool_call_ids_are_included_only_for_models_that_need_them(self) -> None:
        claude = make_model("claude-sonnet-4")
        contents = convert_messages(claude, tool_call_context(claude))
        call = next(p for p in contents[1]["parts"] if "functionCall" in p)["functionCall"]
        assert call["id"] == "call_1"

        gemini = make_model("gemini-2.5-flash")
        contents = convert_messages(gemini, tool_call_context(gemini))
        call = next(p for p in contents[1]["parts"] if "functionCall" in p)["functionCall"]
        assert "id" not in call


class TestMappings:
    def test_requires_tool_call_id(self) -> None:
        assert requires_tool_call_id("claude-sonnet-4") is True
        assert requires_tool_call_id("gpt-oss-120b") is True
        assert requires_tool_call_id("gemini-2.5-flash") is False

    def test_map_tool_choice(self) -> None:
        assert map_tool_choice("auto") == "AUTO"
        assert map_tool_choice("none") == "NONE"
        assert map_tool_choice("any") == "ANY"
        assert map_tool_choice("nonsense") == "AUTO"

    def test_map_stop_reason(self) -> None:
        assert map_stop_reason("STOP") == "stop"
        assert map_stop_reason("MAX_TOKENS") == "length"
        assert map_stop_reason("SAFETY") == "error"
        assert map_stop_reason("MALFORMED_FUNCTION_CALL") == "error"

    def test_map_stop_reason_rejects_unknown_values(self) -> None:
        import pytest

        # The TS has a compile-time exhaustiveness check that throws; without one
        # an unknown reason must not quietly become "error".
        with pytest.raises(ValueError, match="Unhandled stop reason"):
            map_stop_reason("SOMETHING_NEW")

    def test_map_stop_reason_string_is_lenient(self) -> None:
        assert map_stop_reason_string("STOP") == "stop"
        assert map_stop_reason_string("MAX_TOKENS") == "length"
        assert map_stop_reason_string("SOMETHING_NEW") == "error"

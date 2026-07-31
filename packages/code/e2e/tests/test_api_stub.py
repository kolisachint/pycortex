"""The stand-in provider API is infrastructure, so it gets its own tests.

Same reasoning as `test_harness.py`: when `e2e/first-run` fails, the first
question is whether the product broke or the thing standing in for the provider
did. These answer the second half — and they answer it by driving the **real**
Anthropic provider against the stub, which is the only claim that matters: a
stub the provider cannot parse would fail the scenario for the wrong reason.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from cortex.code.e2e._api_stub import (
    AnthropicApiStub,
    OpenAICompletionsApiStub,
    anthropic_sse,
    openai_completions_sse,
)


@pytest.fixture
def stub() -> Any:
    with AnthropicApiStub(reply="stubbed answer") as running:
        yield running


class TestSseBody:
    def test_it_carries_the_events_the_parser_requires(self) -> None:
        body = anthropic_sse("hello").decode("utf-8")
        names = [
            line.removeprefix("event: ") for line in body.splitlines() if line.startswith("event: ")
        ]
        assert names == [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        ]

    def test_the_text_is_in_the_delta(self) -> None:
        body = anthropic_sse("hello").decode("utf-8")
        deltas = [
            json.loads(line.removeprefix("data: "))
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        assert any(event.get("delta", {}).get("text") == "hello" for event in deltas)


class TestServer:
    async def test_the_real_provider_reads_it(self, stub: Any) -> None:
        """The whole point: `cortex.ai.providers.anthropic` against this server."""
        from cortex.ai.models import get_models
        from cortex.ai.providers.anthropic import stream_simple_anthropic
        from cortex.ai.types import Context, SimpleStreamOptions, TextContent, UserMessage

        model = get_models("anthropic")[0].model_copy(update={"base_url": stub.base_url})
        stream = stream_simple_anthropic(
            model,
            Context(
                system_prompt="be brief",
                messages=[UserMessage(content=[TextContent(text="hi")], timestamp=0)],
            ),
            SimpleStreamOptions(api_key="sk-ant-test"),
        )

        message = await stream.result()

        assert message.stop_reason == "stop"
        block = message.content[0]
        assert isinstance(block, TextContent)
        assert block.text == "stubbed answer"
        # And the usage the stub reported came back through the parser, which is
        # what the footer's token counters are drawn from.
        assert message.usage.input == 11
        assert message.usage.output == 7

    async def test_it_records_what_it_was_asked(self, stub: Any) -> None:
        from cortex.ai.models import get_models
        from cortex.ai.providers.anthropic import stream_simple_anthropic
        from cortex.ai.types import Context, SimpleStreamOptions, TextContent, UserMessage

        model = get_models("anthropic")[0].model_copy(update={"base_url": stub.base_url})
        await stream_simple_anthropic(
            model,
            Context(
                system_prompt="",
                messages=[UserMessage(content=[TextContent(text="remember this")], timestamp=0)],
            ),
            SimpleStreamOptions(api_key="sk-ant-recorded"),
        ).result()

        assert len(stub.requests) == 1
        request = stub.requests[0]
        assert request.path == "/v1/messages"
        assert request.headers["x-api-key"] == "sk-ant-recorded"
        assert "remember this" in json.dumps(request.body)

    def test_stopping_it_twice_is_harmless(self) -> None:
        running = AnthropicApiStub().start()
        running.stop()
        running.stop()


@pytest.fixture
def openai_stub() -> Any:
    with OpenAICompletionsApiStub(reply="stubbed completion") as running:
        yield running


class TestOpenAICompletionsSseBody:
    def test_it_carries_the_chunks_the_sdk_requires(self) -> None:
        body = openai_completions_sse("hello").decode("utf-8")
        payloads = [
            line.removeprefix("data: ") for line in body.splitlines() if line.startswith("data: ")
        ]

        assert payloads[-1] == "[DONE]", "the SDK iterates until the sentinel"
        chunks = [json.loads(payload) for payload in payloads[:-1]]
        assert any(
            chunk["choices"] and chunk["choices"][0]["delta"].get("content") == "hello"
            for chunk in chunks
        )
        assert any(
            chunk["choices"] and chunk["choices"][0].get("finish_reason") == "stop"
            for chunk in chunks
        )
        assert any(not chunk["choices"] and "usage" in chunk for chunk in chunks)


class TestOpenAICompletionsServer:
    async def test_the_real_provider_reads_it(self, openai_stub: Any) -> None:
        """`cortex.ai.providers.openai` against this server, same claim as above."""
        from cortex.ai.providers.openai.openai_completions import stream_simple_openai_completions
        from cortex.ai.types import Context, Model, SimpleStreamOptions, TextContent, UserMessage

        model = Model(
            id="stub-model",
            name="Stub Model",
            api="openai-completions",
            provider="demo",
            base_url=openai_stub.base_url,
            reasoning=False,
            input=["text"],
            cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            context_window=100000,
            max_tokens=8000,
        )
        stream = stream_simple_openai_completions(
            model,
            Context(messages=[UserMessage(content=[TextContent(text="hi")], timestamp=0)]),
            SimpleStreamOptions(api_key="sk-openai-test"),
        )

        message = await stream.result()

        assert message.stop_reason == "stop"
        block = message.content[0]
        assert isinstance(block, TextContent)
        assert block.text == "stubbed completion"
        # Usage is deliberately not asserted here, unlike the Anthropic case.
        # The stub sends the trailing usage chunk where the real API does —
        # after the chunk carrying `finish_reason` — and this port's chunk loop
        # `break`s on `finish_reason`, so it never reads it. The TS it was
        # ported from has no such break (the `break` at openai-completions.ts:316
        # belongs to the inner reasoning-field loop), which makes the dropped
        # token counts a separate porting bug, not something this stub should
        # paper over by sending usage early.

    async def test_it_records_what_it_was_asked(self, openai_stub: Any) -> None:
        from cortex.ai.providers.openai.openai_completions import stream_simple_openai_completions
        from cortex.ai.types import Context, Model, SimpleStreamOptions, TextContent, UserMessage

        model = Model(
            id="stub-model",
            name="Stub Model",
            api="openai-completions",
            provider="demo",
            base_url=openai_stub.base_url,
            reasoning=False,
            input=["text"],
            cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            context_window=100000,
            max_tokens=8000,
        )
        await stream_simple_openai_completions(
            model,
            Context(
                messages=[UserMessage(content=[TextContent(text="remember this")], timestamp=0)]
            ),
            SimpleStreamOptions(api_key="sk-openai-recorded"),
        ).result()

        assert len(openai_stub.requests) == 1
        request = openai_stub.requests[0]
        assert request.path == "/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-openai-recorded"
        assert "remember this" in json.dumps(request.body)

    def test_stopping_it_twice_is_harmless(self) -> None:
        running = OpenAICompletionsApiStub().start()
        running.stop()
        running.stop()

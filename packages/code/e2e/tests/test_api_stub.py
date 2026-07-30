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
from cortex.code.e2e._api_stub import AnthropicApiStub, anthropic_sse


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

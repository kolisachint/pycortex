"""Tests for agent loop module.

The loop's contract is the sequence of events one turn produces and the message
it leaves behind, so these drive it against `ai/provider-faux` rather than
checking that a stub raises.
"""

from __future__ import annotations

from typing import Any

import pytest
from cortex.agent.loop import AgentLoop, run_agent_loop, run_agent_loop_continue
from cortex.agent.types import AgentContext, AgentLoopConfig
from cortex.ai.providers.faux import (
    faux_assistant_message,
    faux_text,
    faux_tool_call,
    register_faux_provider,
)
from cortex.ai.types import TextContent, UserMessage


@pytest.fixture
def faux():
    registration = register_faux_provider()
    yield registration
    registration.unregister()


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)], timestamp=0)


def _config(faux: Any) -> AgentLoopConfig:
    return AgentLoopConfig(model=faux.get_model())


class _Sink:
    """Records every event the loop emits, in order."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    @property
    def types(self) -> list[str]:
        return [event["type"] for event in self.events]


def _text_of(message: Any) -> str:
    """The text blocks of a message, joined.

    Written as a helper rather than reaching into `content[0]` because the
    transcript is a union of message types and pyright is right to say so.
    """
    return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")


def _stop_reason(message: Any) -> str:
    return str(getattr(message, "stop_reason", ""))


def _error_message(message: Any) -> str:
    return str(getattr(message, "error_message", ""))


# ---------------------------------------------------------------------------
# AgentLoop class tests
# ---------------------------------------------------------------------------


class TestAgentLoop:
    def test_agent_loop_creation(self) -> None:
        """AgentLoop can be created with config and context."""
        config = AgentLoopConfig()
        context = AgentContext(system_prompt="test", messages=[])
        loop = AgentLoop(config=config, context=context)
        assert loop.config is config
        assert loop.context is context

    def test_agent_loop_default_context(self) -> None:
        """AgentLoop creates default context if not provided."""
        config = AgentLoopConfig()
        loop = AgentLoop(config=config)
        assert loop.context is not None
        assert loop.context.system_prompt == ""

    async def test_agent_loop_run_continue_empty_context(self) -> None:
        """AgentLoop.run_continue raises on empty context."""
        config = AgentLoopConfig()
        context = AgentContext(system_prompt="test", messages=[])
        loop = AgentLoop(config=config, context=context)

        with pytest.raises(ValueError, match="Cannot continue: no messages"):
            await loop.run_continue()

    async def test_agent_loop_run_continue_rejects_assistant_last(self, faux: Any) -> None:
        context = AgentContext(system_prompt="", messages=[faux_assistant_message("done")])
        loop = AgentLoop(config=_config(faux), context=context)

        with pytest.raises(ValueError, match="Cannot continue from message role: assistant"):
            await loop.run_continue()


# ---------------------------------------------------------------------------
# One turn, end to end
# ---------------------------------------------------------------------------


class TestStreamAssistantResponse:
    async def test_a_turn_emits_the_ts_event_sequence(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("hello")])
        sink = _Sink()
        context = AgentContext(system_prompt="be brief", messages=[])

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=context,
            config=_config(faux),
            emit=sink,
        )

        assert sink.types[:4] == ["agent_start", "turn_start", "message_start", "message_end"]
        assert sink.types[-2:] == ["turn_end", "agent_end"]
        assert "message_update" in sink.types, "the deltas never reached the sink"
        assert [getattr(m, "role", "") for m in messages] == ["user", "assistant"]
        assert _text_of(messages[-1]) == "hello"

    async def test_the_prompt_and_the_answer_land_in_the_context(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("answered")])
        context = AgentContext(system_prompt="", messages=[])

        await run_agent_loop(
            prompts=[_user("question")], context=context, config=_config(faux), emit=_Sink()
        )

        roles = [getattr(m, "role", "") for m in context.messages]
        assert roles == ["user", "assistant"]
        # The partial that `start` pushed was replaced, not appended alongside.
        assert _text_of(context.messages[-1]) == "answered"

    async def test_an_error_turn_ends_the_run(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("", stop_reason="error", error_message="boom")])
        sink = _Sink()

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[]),
            config=_config(faux),
            emit=sink,
        )

        assert sink.types[-2:] == ["turn_end", "agent_end"]
        assert _stop_reason(messages[-1]) == "error"
        assert _error_message(messages[-1]) == "boom"

    async def test_an_errored_turn_ends_the_run_even_with_a_message_queued(self, faux: Any) -> None:
        """An error stops the loop where it stands — it does not steer on.

        With nothing queued, "return here" and "fall through to the end" produce
        the same events, so the queue is what makes the difference visible.
        """
        faux.set_responses(
            [
                faux_assistant_message("", stop_reason="error", error_message="boom"),
                faux_assistant_message("should never be asked for"),
            ]
        )
        queued: list[list[Any]] = [[], [_user("carry on")], []]

        async def get_steering_messages() -> list[Any]:
            return queued.pop(0) if queued else []

        config = AgentLoopConfig(
            model=faux.get_model(), get_steering_messages=get_steering_messages
        )
        sink = _Sink()

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[]),
            config=config,
            emit=sink,
        )

        assert sink.types.count("turn_start") == 1, "the loop ran another turn after an error"
        assert [getattr(m, "role", "") for m in messages] == ["user", "assistant"]
        assert faux.get_pending_response_count() == 1, "the provider was asked again"

    async def test_an_aborted_stream_ends_the_run(self, faux: Any) -> None:
        class _Signal:
            aborted = True

        faux.set_responses([faux_assistant_message("never sent")])

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[]),
            config=_config(faux),
            emit=_Sink(),
            signal=_Signal(),
        )

        assert _stop_reason(messages[-1]) == "aborted"

    async def test_convert_to_llm_filters_what_the_provider_sees(self, faux: Any) -> None:
        seen: list[Any] = []

        def convert(messages: list[Any]) -> list[Any]:
            seen.append(list(messages))
            return [m for m in messages if getattr(m, "role", "") == "user"]

        faux.set_responses([faux_assistant_message("ok")])
        config = AgentLoopConfig(model=faux.get_model(), convert_to_llm=convert)

        await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[]),
            config=config,
            emit=_Sink(),
        )

        assert seen, "convert_to_llm was never called"

    async def test_tools_are_projected_onto_the_provider_schema(self, faux: Any) -> None:
        from cortex.agent.types import AgentTool

        captured: list[Any] = []

        def stream_fn(model: Any, context: Any, options: Any):
            captured.append(context)
            from cortex.ai.stream import stream_simple

            return stream_simple(model, context, options)

        faux.set_responses([faux_assistant_message("ok")])
        tool = AgentTool(name="read", description="Read a file", parameters={"type": "object"})

        await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=_config(faux),
            emit=_Sink(),
            stream_fn=stream_fn,
        )

        assert captured, "the stream function was never called"
        assert captured[0].tools is not None
        assert captured[0].tools[0].name == "read"


class TestQueues:
    async def test_a_steering_message_starts_another_turn(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("first"), faux_assistant_message("second")])
        queued: list[list[Any]] = [[], [_user("and another thing")], []]

        async def get_steering_messages() -> list[Any]:
            return queued.pop(0) if queued else []

        config = AgentLoopConfig(
            model=faux.get_model(), get_steering_messages=get_steering_messages
        )
        sink = _Sink()

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[]),
            config=config,
            emit=sink,
        )

        texts = [_text_of(m) for m in messages if getattr(m, "role", "") == "assistant"]
        assert texts == ["first", "second"], texts
        assert sink.types.count("turn_start") == 2

    async def test_a_follow_up_message_starts_another_turn(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("first"), faux_assistant_message("second")])
        queued = [[_user("one more")], []]

        async def get_follow_up_messages() -> list[Any]:
            return queued.pop(0) if queued else []

        config = AgentLoopConfig(
            model=faux.get_model(), get_follow_up_messages=get_follow_up_messages
        )

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[]),
            config=config,
            emit=_Sink(),
        )

        texts = [_text_of(m) for m in messages if getattr(m, "role", "") == "assistant"]
        assert texts == ["first", "second"], texts


class TestToolCalls:
    async def test_a_tool_call_names_the_step_that_will_run_it(self, faux: Any) -> None:
        """Step 7.5 owns tool execution; until it lands this says so out loud.

        The alternative — an empty batch that does not terminate — sends the loop
        round again with the same assistant message, forever.
        """
        faux.set_responses(
            [faux_assistant_message([faux_text("using a tool"), faux_tool_call("read", {})])]
        )

        with pytest.raises(NotImplementedError, match="7.5"):
            await run_agent_loop(
                prompts=[_user("read a file")],
                context=AgentContext(system_prompt="", messages=[]),
                config=_config(faux),
                emit=_Sink(),
            )


# ---------------------------------------------------------------------------
# Public API tests
# ---------------------------------------------------------------------------


class TestPublicAPI:
    async def test_run_agent_loop(self, faux: Any) -> None:
        """run_agent_loop creates and runs an AgentLoop."""
        faux.set_responses([faux_assistant_message("ok")])

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="test", messages=[]),
            config=_config(faux),
            emit=_Sink(),
        )

        assert len(messages) == 2

    async def test_run_agent_loop_continue(self) -> None:
        """run_agent_loop_continue creates and runs an AgentLoop."""
        config = AgentLoopConfig()
        context = AgentContext(system_prompt="test", messages=[])

        with pytest.raises(ValueError, match="Cannot continue: no messages"):
            await run_agent_loop_continue(
                context=context,
                config=config,
            )

    async def test_run_agent_loop_continue_answers_a_pending_user_message(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("continued")])
        context = AgentContext(system_prompt="", messages=[_user("half a conversation")])

        messages = await run_agent_loop_continue(
            context=context, config=_config(faux), emit=_Sink()
        )

        assert [getattr(m, "role", "") for m in messages] == ["assistant"]
        assert _text_of(messages[0]) == "continued"

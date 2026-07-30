"""Tests for agent module.

The queue and state tests are the port's originals. Everything under "run
lifecycle" is step 7.4's: the abort signal, `is_streaming`, `wait_for_idle` and
the failure path did not exist before it, because nothing had ever run a turn.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from cortex.agent.agent import (
    AbortController,
    Agent,
    AgentOptions,
    PendingMessageQueue,
    default_convert_to_llm,
)
from cortex.agent.types import AgentState
from cortex.ai.providers.faux import faux_assistant_message, register_faux_provider
from cortex.ai.types import TextContent, ToolResultMessage, UserMessage


@pytest.fixture
def faux():
    registration = register_faux_provider()
    yield registration
    registration.unregister()


def _agent(faux: Any, **options: Any) -> Agent:
    return Agent(AgentOptions(initial_state={"model": faux.get_model()}, **options))


def _events(agent: Agent) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def listener(event: dict[str, Any], signal: Any) -> None:
        seen.append(event)

    agent.subscribe(listener)
    return seen


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
# PendingMessageQueue tests
# ---------------------------------------------------------------------------


class TestPendingMessageQueue:
    def test_queue_creation(self) -> None:
        """PendingMessageQueue can be created."""
        queue = PendingMessageQueue(mode="all")
        assert queue.mode == "all"
        assert queue.messages == []

    def test_queue_enqueue(self) -> None:
        """PendingMessageQueue can enqueue messages."""
        queue = PendingMessageQueue(mode="all")
        queue.enqueue({"role": "user", "content": "test"})
        assert len(queue.messages) == 1

    def test_queue_has_items(self) -> None:
        """PendingMessageQueue.has_items works correctly."""
        queue = PendingMessageQueue(mode="all")
        assert not queue.has_items()
        queue.enqueue({"role": "user", "content": "test"})
        assert queue.has_items()

    def test_queue_drain_all(self) -> None:
        """PendingMessageQueue.drain returns all messages in 'all' mode."""
        queue = PendingMessageQueue(mode="all")
        queue.enqueue({"role": "user", "content": "msg1"})
        queue.enqueue({"role": "user", "content": "msg2"})
        drained = queue.drain()
        assert len(drained) == 2
        assert not queue.has_items()

    def test_queue_drain_one_at_a_time(self) -> None:
        """PendingMessageQueue.drain returns one message in 'one-at-a-time' mode."""
        queue = PendingMessageQueue(mode="one-at-a-time")
        queue.enqueue({"role": "user", "content": "msg1"})
        queue.enqueue({"role": "user", "content": "msg2"})
        drained = queue.drain()
        assert len(drained) == 1
        assert queue.has_items()

    def test_queue_clear(self) -> None:
        queue = PendingMessageQueue(mode="all")
        queue.enqueue({"role": "user", "content": "msg"})
        queue.clear()
        assert not queue.has_items()


# ---------------------------------------------------------------------------
# Agent tests
# ---------------------------------------------------------------------------


class TestAgent:
    def test_agent_creation(self) -> None:
        """Agent can be created with default options."""
        agent = Agent()
        assert agent.state is not None
        assert agent.system_prompt == ""
        assert agent.tools == []
        assert agent.messages == []

    def test_agent_creation_with_options(self) -> None:
        """Agent can be created with custom options."""
        options = AgentOptions(
            initial_state={
                "system_prompt": "You are a helpful assistant.",
                "thinking_level": "medium",
            }
        )
        agent = Agent(options=options)
        assert agent.system_prompt == "You are a helpful assistant."
        assert agent.state.thinking_level == "medium"

    def test_an_agent_with_no_model_gets_the_sentinel(self) -> None:
        """`createMutableAgentState` defaults the model, and so does this.

        Its provider is `"unknown"`, which is the case the auth guidance words.
        """
        agent = Agent()
        assert agent.model is not None
        assert agent.model.provider == "unknown"

    def test_agent_subscribe(self) -> None:
        """Agent.subscribe registers listeners."""
        agent = Agent()

        def listener(event: Any, signal: Any) -> None:
            pass

        unsub = agent.subscribe(listener)
        assert len(agent._listeners) == 1  # pyright: ignore[reportPrivateUsage]

        unsub()
        assert len(agent._listeners) == 0  # pyright: ignore[reportPrivateUsage]

    def test_agent_steer(self) -> None:
        """Agent.steer adds to the steering queue."""
        agent = Agent()
        agent.steer({"role": "user", "content": "steer"})
        assert agent.has_queued_messages()

    def test_agent_follow_up(self) -> None:
        """Agent.follow_up adds to the follow-up queue."""
        agent = Agent()
        agent.follow_up({"role": "user", "content": "follow up"})
        assert agent.has_queued_messages()

    def test_clearing_the_queues_empties_both(self) -> None:
        agent = Agent()
        agent.steer({"role": "user", "content": "a"})
        agent.follow_up({"role": "user", "content": "b"})
        agent.clear_all_queues()
        assert not agent.has_queued_messages()

    def test_queue_modes_default_to_the_ts_default(self) -> None:
        agent = Agent()
        assert agent.steering_mode == "one-at-a-time"
        assert agent.follow_up_mode == "one-at-a-time"

    def test_agent_clear_messages(self) -> None:
        """Agent.clear_messages clears all messages."""
        agent = Agent()
        agent.state.messages = [{"role": "user", "content": "test"}]
        agent.clear_messages()
        assert agent.messages == []

    def test_agent_reset(self) -> None:
        """Agent.reset resets runtime state and queues."""
        agent = Agent()
        agent.state.messages = [{"role": "user", "content": "test"}]
        agent.steer({"role": "user", "content": "test"})
        agent.reset()
        assert agent.messages == []
        assert not agent.has_queued_messages()


class TestConvertToLlm:
    def test_only_provider_roles_survive(self) -> None:
        user = UserMessage(content=[TextContent(text="hi")], timestamp=0)
        assistant = faux_assistant_message("hello")
        tool_result = ToolResultMessage(
            tool_call_id="1",
            tool_name="read",
            content=[TextContent(text="file")],
            is_error=False,
            timestamp=0,
        )
        custom = {"role": "custom", "customType": "note", "content": "aside"}

        kept = default_convert_to_llm([user, assistant, tool_result, custom])

        assert kept == [user, assistant, tool_result]


class TestAbortController:
    def test_the_signal_follows_the_controller(self) -> None:
        controller = AbortController()
        assert controller.signal.aborted is False
        controller.abort()
        assert controller.signal.aborted is True


# ---------------------------------------------------------------------------
# Run lifecycle (step 7.4)
# ---------------------------------------------------------------------------


class TestRunLifecycle:
    async def test_a_prompt_runs_a_turn_and_records_it(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("hello")])
        agent = _agent(faux)
        seen = _events(agent)

        await agent.prompt("hi")

        assert [getattr(m, "role", "") for m in agent.messages] == ["user", "assistant"]
        assert [e["type"] for e in seen][:2] == ["agent_start", "turn_start"]
        assert seen[-1]["type"] == "agent_end"

    async def test_a_string_prompt_becomes_a_user_message(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("ok")])
        agent = _agent(faux)

        await agent.prompt("plain text")

        assert _text_of(agent.messages[0]) == "plain text"

    async def test_is_streaming_is_true_only_during_the_turn(self, faux: Any) -> None:
        released = asyncio.Event()
        during: list[bool] = []

        async def slow(*_args: Any) -> Any:
            during.append(True)
            await released.wait()
            return faux_assistant_message("late")

        faux.set_responses([slow])
        agent = _agent(faux)
        assert agent.is_streaming is False

        turn = asyncio.ensure_future(agent.prompt("hi"))
        for _ in range(50):
            if agent.is_streaming:
                break
            await asyncio.sleep(0)
        assert agent.is_streaming is True
        released.set()
        await turn
        assert agent.is_streaming is False

    async def test_a_second_prompt_during_a_turn_is_refused(self, faux: Any) -> None:
        released = asyncio.Event()

        async def slow(*_args: Any) -> Any:
            await released.wait()
            return faux_assistant_message("late")

        faux.set_responses([slow])
        agent = _agent(faux)
        turn = asyncio.ensure_future(agent.prompt("first"))
        for _ in range(50):
            if agent.is_streaming:
                break
            await asyncio.sleep(0)

        # The wording matters: `prompt()`'s guard tells the caller what to do
        # instead, which the lifecycle's own "already processing" does not.
        with pytest.raises(RuntimeError, match=r"Use steer\(\) or follow_up\(\)"):
            await agent.prompt("second")

        released.set()
        await turn

    async def test_abort_marks_the_turn_aborted(self, faux: Any) -> None:
        released = asyncio.Event()

        async def slow(*_args: Any) -> Any:
            await released.wait()
            return faux_assistant_message("never mind")

        faux.set_responses([slow])
        agent = _agent(faux)
        turn = asyncio.ensure_future(agent.prompt("hi"))
        for _ in range(50):
            if agent.is_streaming:
                break
            await asyncio.sleep(0)

        agent.abort()
        assert agent.signal is not None and agent.signal.aborted is True
        released.set()
        await turn

        assert _stop_reason(agent.messages[-1]) == "aborted"

    async def test_wait_for_idle_returns_when_the_turn_is_over(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("done")])
        agent = _agent(faux)
        turn = asyncio.ensure_future(agent.prompt("hi"))
        await asyncio.sleep(0)

        await agent.wait_for_idle()

        # "Idle" means the turn is *finished*, not that nobody asked: the
        # transcript is complete before this returns.
        assert [getattr(m, "role", "") for m in agent.messages] == ["user", "assistant"]
        assert agent.is_streaming is False
        await turn

    async def test_a_run_that_throws_while_aborting_is_recorded_as_aborted(self, faux: Any) -> None:
        """The TS reads the signal, not the exception, to label the failure."""
        agent = _agent(faux)

        def stream_fn(*_args: Any, **_kwargs: Any) -> Any:
            agent.abort()
            raise RuntimeError("connection reset")

        agent.stream_fn = stream_fn
        await agent.prompt("hi")

        assert _stop_reason(agent.messages[-1]) == "aborted"

    async def test_wait_for_idle_returns_at_once_when_nothing_runs(self) -> None:
        agent = Agent()
        await agent.wait_for_idle()

    async def test_a_thrown_run_becomes_an_error_message(self, faux: Any) -> None:
        """The provider contract says failures arrive as a message, so make it so.

        Anything that still escapes — here, a stream function that raises — would
        otherwise leave the UI with a turn that started and never ended.
        """

        def exploding_stream(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("transport is on fire")

        agent = _agent(faux, stream_fn=exploding_stream)
        seen = _events(agent)

        await agent.prompt("hi")

        assert seen[-1]["type"] == "agent_end"
        last = agent.messages[-1]
        assert _stop_reason(last) == "error"
        assert _error_message(last) == "transport is on fire"

    async def test_a_listener_sees_the_runs_abort_signal(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("ok")])
        agent = _agent(faux)
        signals: list[Any] = []

        def listener(event: dict[str, Any], signal: Any) -> None:
            signals.append(signal)

        agent.subscribe(listener)
        await agent.prompt("hi")

        assert signals and all(s is not None for s in signals)

    async def test_async_listeners_are_awaited(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("ok")])
        agent = _agent(faux)
        seen: list[str] = []

        async def listener(event: dict[str, Any], signal: Any) -> None:
            await asyncio.sleep(0)
            seen.append(event["type"])

        agent.subscribe(listener)
        await agent.prompt("hi")

        assert seen[-1] == "agent_end", "an async listener was not awaited to completion"

    async def test_continue_from_an_assistant_message_drains_the_queue(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("first"), faux_assistant_message("second")])
        agent = _agent(faux)
        await agent.prompt("hi")

        agent.follow_up(UserMessage(content=[TextContent(text="more")], timestamp=0))
        await agent.continue_conversation()

        texts = [_text_of(m) for m in agent.messages if getattr(m, "role", "") == "assistant"]
        assert texts == ["first", "second"]

    async def test_continue_with_nothing_queued_is_an_error(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("first")])
        agent = _agent(faux)
        await agent.prompt("hi")

        with pytest.raises(ValueError, match="Cannot continue from message role: assistant"):
            await agent.continue_conversation()


# ---------------------------------------------------------------------------
# Agent state tests
# ---------------------------------------------------------------------------


class TestAgentState:
    def test_agent_state_creation(self) -> None:
        """AgentState can be created."""
        state = AgentState()
        assert state.system_prompt == ""
        assert state.thinking_level == "off"
        assert state.is_streaming is False

    def test_agent_state_with_values(self) -> None:
        """AgentState can be created with values."""
        state = AgentState(
            system_prompt="test",
            thinking_level="high",
            is_streaming=True,
        )
        assert state.system_prompt == "test"
        assert state.thinking_level == "high"
        assert state.is_streaming is True


# ---------------------------------------------------------------------------
# What the agent tells the loop about the request (7.12)
# ---------------------------------------------------------------------------


class TestLoopConfigStreamOptions:
    """`createLoopConfig`'s eight request-shaping fields.

    `AgentOptions` accepted every one of them and `_create_loop_config` set
    none, so they were stored and never read: a session on thinking level
    `high` asked the provider for no thinking at all. These are asserted on the
    config the agent builds, because that is the object the loop spreads into
    the provider call.
    """

    def test_thinking_level_becomes_reasoning(self, faux: Any) -> None:
        agent = _agent(faux)
        agent.state.thinking_level = "high"
        assert agent._create_loop_config()  # pyright: ignore[reportPrivateUsage].reasoning == "high"

    def test_off_is_no_reasoning_rather_than_the_string(self, faux: Any) -> None:
        """`reasoning: thinkingLevel === "off" ? undefined : …` — the TS's ternary.

        Sending `reasoning="off"` would be a request for a thinking level the
        providers do not have, rather than a request for no thinking.
        """
        agent = _agent(faux)
        agent.state.thinking_level = "off"
        assert agent._create_loop_config()  # pyright: ignore[reportPrivateUsage].reasoning is None

    def test_the_session_id_and_callbacks_are_carried(self, faux: Any) -> None:
        async def on_payload(payload: Any) -> Any:
            return payload

        async def on_response(_response: Any) -> None:
            return None

        agent = _agent(
            faux,
            session_id="session-42",
            on_payload=on_payload,
            on_response=on_response,
            transport="sse",
            max_retry_delay_ms=250,
            thinking_display="summarized",
        )

        config = agent._create_loop_config()  # pyright: ignore[reportPrivateUsage]
        assert config.session_id == "session-42"
        assert config.on_payload is on_payload
        assert config.on_response is on_response
        assert config.transport == "sse"
        assert config.max_retry_delay_ms == 250
        assert config.thinking_display == "summarized"

    async def test_the_level_reaches_the_provider(self, faux: Any) -> None:
        """End to end through the loop, since the config alone proves half of it."""
        seen: list[Any] = []

        def stream_fn(model: Any, context: Any, options: Any) -> Any:
            from cortex.ai.stream import stream_simple

            seen.append(options)
            return stream_simple(model, context, options)

        faux.set_responses([faux_assistant_message("ok")])
        agent = _agent(faux, stream_fn=stream_fn)
        agent.state.thinking_level = "medium"

        await agent.prompt("think about it")

        assert seen and seen[0].reasoning == "medium"

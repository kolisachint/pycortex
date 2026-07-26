"""Tests for agent module.

Mechanical port of hoocode's agent tests (if any).
"""

from __future__ import annotations

from typing import Any

from cortex.agent.agent import Agent, AgentOptions, PendingMessageQueue
from cortex.agent.types import AgentState

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

    def test_agent_subscribe(self) -> None:
        """Agent.subscribe registers listeners."""
        agent = Agent()
        events = []

        async def listener(event: Any) -> None:
            events.append(event)

        unsub = agent.subscribe(listener)
        assert len(agent._subscribers) == 1  # type: ignore[attr-defined]

        unsub()
        assert len(agent._subscribers) == 0  # type: ignore[attr-defined]

    def test_agent_add_steering_message(self) -> None:
        """Agent.add_steering_message adds to queue."""
        agent = Agent()
        agent.add_steering_message({"role": "user", "content": "steer"})
        assert agent._steering_queue.has_items()  # type: ignore[attr-defined]

    def test_agent_add_follow_up_message(self) -> None:
        """Agent.add_follow_up_message adds to queue."""
        agent = Agent()
        agent.add_follow_up_message({"role": "user", "content": "follow up"})
        assert agent._follow_up_queue.has_items()  # type: ignore[attr-defined]

    def test_agent_clear_messages(self) -> None:
        """Agent.clear_messages clears all messages."""
        agent = Agent()
        agent.state.messages = [{"role": "user", "content": "test"}]
        agent.clear_messages()
        assert agent.messages == []

    def test_agent_reset(self) -> None:
        """Agent.reset resets all state."""
        agent = Agent()
        agent.system_prompt = "test"
        agent.add_steering_message({"role": "user", "content": "test"})
        agent.reset()
        assert agent.system_prompt == ""
        assert not agent._steering_queue.has_items()  # type: ignore[attr-defined]


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

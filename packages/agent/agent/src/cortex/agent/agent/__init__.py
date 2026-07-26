"""Agent module.

Mechanical port of hoocode's ``packages/agent/src/agent.ts``.

This module implements the main Agent class that manages the conversation,
tools, and agent loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cortex.agent.loop import AgentLoop, run_agent_loop, run_agent_loop_continue
from cortex.agent.types import (
    AgentContext,
    AgentEvent,
    AgentLoopConfig,
    AgentMessage,
    AgentState,
    ToolExecutionMode,
)
from cortex.ai.types import Message, Model

__all__ = [
    "Agent",
    "AgentOptions",
    "PendingMessageQueue",
    "QueueMode",
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

from typing import Literal

QueueMode = Literal["all", "one-at-a-time"]


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

EMPTY_USAGE = {
    "input": 0,
    "output": 0,
    "cacheRead": 0,
    "cacheWrite": 0,
    "totalTokens": 0,
    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
}


# ---------------------------------------------------------------------------
# Pending message queue
# ---------------------------------------------------------------------------


@dataclass
class PendingMessageQueue:
    """Queue for pending messages."""

    mode: QueueMode = "all"
    messages: list[AgentMessage] = field(default_factory=list)

    def enqueue(self, message: AgentMessage) -> None:
        """Add a message to the queue."""
        self.messages.append(message)

    def has_items(self) -> bool:
        """Check if queue has messages."""
        return len(self.messages) > 0

    def drain(self) -> list[AgentMessage]:
        """Drain messages from the queue."""
        if self.mode == "all":
            drained = self.messages.copy()
            self.messages = []
            return drained

        if not self.messages:
            return []

        first = self.messages[0]
        self.messages = self.messages[1:]
        return [first]


# ---------------------------------------------------------------------------
# Agent options
# ---------------------------------------------------------------------------


@dataclass
class AgentOptions:
    """Options for constructing an Agent."""

    initial_state: dict[str, Any] | None = None
    convert_to_llm: Callable[..., Any] | None = None
    transform_context: Callable[..., Any] | None = None
    stream_fn: Any = None
    get_api_key: Callable[..., Any] | None = None
    on_payload: Callable[..., Any] | None = None
    on_response: Callable[..., Any] | None = None
    before_tool_call: Callable[..., Any] | None = None
    after_tool_call: Callable[..., Any] | None = None
    prepare_next_turn: Callable[..., Any] | None = None
    create_background_result_message: Callable[..., Any] | None = None
    create_background_placeholder: Callable[..., Any] | None = None
    on_background_task_count_change: Callable[..., Any] | None = None
    steering_mode: QueueMode = "all"
    follow_up_mode: QueueMode = "all"
    session_id: str | None = None
    thinking_budgets: Any = None
    thinking_display: str | None = None
    transport: Any = None
    max_retry_delay_ms: int | None = None
    tool_execution: ToolExecutionMode = "parallel"


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------


@dataclass
class Agent:
    """Main Agent class that manages conversation and tool execution."""

    options: AgentOptions = field(default_factory=AgentOptions)
    state: AgentState = field(default_factory=AgentState)
    loop: AgentLoop | None = None
    _subscribers: list[Callable[..., Any]] = field(default_factory=list)
    _steering_queue: PendingMessageQueue = field(
        default_factory=lambda: PendingMessageQueue(mode="all")
    )
    _follow_up_queue: PendingMessageQueue = field(
        default_factory=lambda: PendingMessageQueue(mode="all")
    )

    def __post_init__(self) -> None:
        """Initialize agent state from options."""
        if self.options.initial_state:
            for key, value in self.options.initial_state.items():
                if hasattr(self.state, key):
                    setattr(self.state, key, value)

    @property
    def system_prompt(self) -> str:
        """Get system prompt."""
        return self.state.system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        """Set system prompt."""
        self.state.system_prompt = value

    @property
    def model(self) -> Model[Any]:
        """Get current model."""
        return self.state.model  # type: ignore[return-value]

    @model.setter
    def model(self, value: Model[Any]) -> None:
        """Set current model."""
        self.state.model = value

    @property
    def tools(self) -> Any:  # type: ignore[return-value]
        """Get available tools."""
        return self.state.tools

    @tools.setter
    def tools(self, value: Any) -> None:  # type: ignore[assignment]
        """Set available tools."""
        self.state.tools = value

    @property
    def messages(self) -> list[AgentMessage]:
        """Get conversation messages."""
        return self.state.messages

    @messages.setter
    def messages(self, value: list[AgentMessage]) -> None:
        """Set conversation messages."""
        self.state.messages = value

    @property
    def is_streaming(self) -> bool:
        """Check if agent is currently streaming."""
        return self.state.is_streaming

    @property
    def streaming_message(self) -> AgentMessage | None:
        """Get current streaming message."""
        return self.state.streaming_message

    @property
    def pending_tool_calls(self) -> frozenset[str]:
        """Get pending tool call IDs."""
        return self.state.pending_tool_calls

    @property
    def error_message(self) -> str | None:
        """Get error message if any."""
        return self.state.error_message

    def subscribe(self, listener: Callable[..., Any]) -> Callable[[], None]:
        """Subscribe to agent events.

        Args:
            listener: Callback function for events.

        Returns:
            Unsubscribe function.
        """
        self._subscribers.append(listener)

        def unsubscribe() -> None:
            if listener in self._subscribers:
                self._subscribers.remove(listener)

        return unsubscribe

    async def _emit(self, event: AgentEvent) -> None:
        """Emit an event to all subscribers."""
        for listener in self._subscribers:
            try:
                await listener(event)
            except Exception:
                # Subscriber errors should not break the agent
                pass

    async def prompt(self, message: str | Message) -> AgentMessage:
        """Send a prompt to the agent.

        Args:
            message: The message to send.

        Returns:
            The assistant's response.
        """
        # Convert string to Message if needed
        if isinstance(message, str):
            from cortex.ai.types import UserMessage

            msg = UserMessage(content=[{"type": "text", "text": message}], timestamp=0)  # type: ignore[arg-type]
        else:
            msg = message

        # Create context
        context = AgentContext(
            system_prompt=self.state.system_prompt,
            messages=self.state.messages.copy(),
            tools=self.state.tools,
        )

        # Create loop config
        config = self._create_loop_config()

        # Run the loop
        new_messages = await run_agent_loop(
            prompts=[msg],
            context=context,
            config=config,
        )

        # Update state
        self.state.messages.extend(new_messages)

        # Return the last assistant message
        for m in reversed(new_messages):
            if hasattr(m, "role") and m.role == "assistant":
                return m

        raise ValueError("No assistant message in response")

    async def continue_conversation(self) -> list[AgentMessage]:
        """Continue the conversation from the current state.

        Returns:
            List of new messages.
        """
        context = AgentContext(
            system_prompt=self.state.system_prompt,
            messages=self.state.messages.copy(),
            tools=self.state.tools,
        )

        config = self._create_loop_config()

        new_messages = await run_agent_loop_continue(
            context=context,
            config=config,
        )

        self.state.messages.extend(new_messages)
        return new_messages

    def _create_loop_config(self) -> AgentLoopConfig:
        """Create loop configuration from options."""
        return AgentLoopConfig(
            model=self.state.model,
            convert_to_llm=self.options.convert_to_llm,
            transform_context=self.options.transform_context,
            get_api_key=self.options.get_api_key,
            should_stop_after_turn=None,
            prepare_next_turn=self.options.prepare_next_turn,
            get_steering_messages=self._get_steering_messages,
            get_follow_up_messages=self._get_follow_up_messages,
            create_background_result_message=self.options.create_background_result_message,
            create_background_placeholder=self.options.create_background_placeholder,
            on_background_task_count_change=self.options.on_background_task_count_change,
            tool_execution=self.options.tool_execution,
            before_tool_call=self.options.before_tool_call,
            after_tool_call=self.options.after_tool_call,
        )

    async def _get_steering_messages(self) -> list[AgentMessage]:
        """Get pending steering messages."""
        return self._steering_queue.drain()

    async def _get_follow_up_messages(self) -> list[AgentMessage]:
        """Get pending follow-up messages."""
        return self._follow_up_queue.drain()

    def add_steering_message(self, message: AgentMessage) -> None:
        """Add a steering message to the queue."""
        self._steering_queue.enqueue(message)

    def add_follow_up_message(self, message: AgentMessage) -> None:
        """Add a follow-up message to the queue."""
        self._follow_up_queue.enqueue(message)

    def clear_messages(self) -> None:
        """Clear all messages."""
        self.state.messages = []

    def reset(self) -> None:
        """Reset agent state."""
        self.state = AgentState()
        self._steering_queue = PendingMessageQueue(mode="all")
        self._follow_up_queue = PendingMessageQueue(mode="all")

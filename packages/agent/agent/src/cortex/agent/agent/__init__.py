"""Agent module.

Mechanical port of hoocode's ``packages/agent/src/agent.ts`` — the stateful
wrapper around the low-level agent loop. It owns the transcript, emits lifecycle
events to subscribers, and exposes the steering/follow-up queues.

**What step 7.4 changed here, and why.** Phase 3 landed this leaf as a sketch:
``prompt()`` called the loop and returned the last assistant message, and there
was no run lifecycle at all — no abort, no ``is_streaming``, no way to wait for a
turn to finish. Nothing noticed, because nothing had ever run a turn. The
``AgentSession`` bridge is exactly the thing that needs all three
(``session.abort()`` is ``agent.abort()`` plus ``wait_for_idle()``, and
``session.prompt()`` refuses to start a second turn by asking ``is_streaming``),
so the lifecycle is ported from ``agent.ts`` here rather than faked one layer up.

Two names differ from the TS, both because Python reserves the word: ``continue``
is :meth:`Agent.continue_conversation`, and the platform ``AbortController`` is
the small pair defined below (the port's convention, from the provider leaves, is
that an abort signal is any object with a bool ``aborted``).
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from cortex.agent.loop import run_agent_loop, run_agent_loop_continue
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
    "AbortController",
    "AbortSignal",
    "Agent",
    "AgentOptions",
    "PendingMessageQueue",
    "QueueMode",
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

QueueMode = Literal["all", "one-at-a-time"]

#: An agent event listener. The TS hands listeners the run's abort signal
#: alongside the event; sync and async listeners are both allowed, and async
#: ones are awaited in subscription order.
AgentEventListener = Callable[..., Any]


class AbortSignal:
    """Read side of an :class:`AbortController`.

    There is no ``AbortSignal`` in Python and no event to listen for, so the
    convention the provider leaves already use applies: a signal is anything with
    a bool ``aborted``, polled at the points the TS would have been notified.
    """

    def __init__(self, controller: AbortController) -> None:
        self._controller = controller

    @property
    def aborted(self) -> bool:
        return self._controller.aborted


class AbortController:
    """Write side: ``abort()`` flips the signal its holder is polling."""

    def __init__(self) -> None:
        self.aborted = False
        self.signal = AbortSignal(self)

    def abort(self) -> None:
        self.aborted = True


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

EMPTY_USAGE = {
    "input": 0,
    "output": 0,
    "cache_read": 0,
    "cache_write": 0,
    "total_tokens": 0,
    "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
}

DEFAULT_MODEL = Model(
    id="unknown",
    name="unknown",
    api="unknown",
    provider="unknown",
    base_url="",
    reasoning=False,
    input=[],
    cost={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
    context_window=0,
    max_tokens=0,
)


def _message_role(message: AgentMessage) -> str:
    """Role of a message, whether it arrived as a model or as a plain dict."""
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "role", ""))


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Drop the message roles the provider does not accept. Port of ``defaultConvertToLlm``."""
    return [m for m in messages if _message_role(m) in ("user", "assistant", "toolResult")]


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

    def clear(self) -> None:
        """Discard every queued message."""
        self.messages = []


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
    steering_mode: QueueMode = "one-at-a-time"
    follow_up_mode: QueueMode = "one-at-a-time"
    session_id: str | None = None
    thinking_budgets: Any = None
    thinking_display: Literal["summarized", "omitted"] | None = None
    transport: Any = None
    max_retry_delay_ms: int | None = None
    tool_execution: ToolExecutionMode = "parallel"


# ---------------------------------------------------------------------------
# Active run
# ---------------------------------------------------------------------------


@dataclass
class _ActiveRun:
    """The in-flight turn: what to await, and what aborts it."""

    future: Any  # asyncio.Future[None]
    controller: AbortController


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------


class Agent:
    """Stateful wrapper around the low-level agent loop."""

    def __init__(self, options: AgentOptions | None = None) -> None:
        self.options = options if options is not None else AgentOptions()
        self.state = AgentState()
        if self.options.initial_state:
            for key, value in self.options.initial_state.items():
                if hasattr(self.state, key):
                    setattr(self.state, key, value)
        if self.state.model is None:
            self.state.model = DEFAULT_MODEL

        self.convert_to_llm = self.options.convert_to_llm or default_convert_to_llm
        self.transform_context = self.options.transform_context
        self.stream_fn = self.options.stream_fn
        self.get_api_key = self.options.get_api_key
        self.before_tool_call = self.options.before_tool_call
        self.after_tool_call = self.options.after_tool_call
        self.prepare_next_turn = self.options.prepare_next_turn
        self.session_id = self.options.session_id
        self.tool_execution: ToolExecutionMode = self.options.tool_execution

        self._listeners: list[AgentEventListener] = []
        self._steering_queue = PendingMessageQueue(mode=self.options.steering_mode)
        self._follow_up_queue = PendingMessageQueue(mode=self.options.follow_up_mode)
        self._active_run: _ActiveRun | None = None

    # ------------------------------------------------------------------
    # State access
    # ------------------------------------------------------------------

    @property
    def system_prompt(self) -> str:
        """Get system prompt."""
        return self.state.system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self.state.system_prompt = value

    @property
    def model(self) -> Model | None:
        """Get current model."""
        return self.state.model

    @model.setter
    def model(self, value: Model) -> None:
        self.state.model = value

    @property
    def tools(self) -> list[Any]:
        """Get available tools."""
        return self.state.tools

    @tools.setter
    def tools(self, value: list[Any]) -> None:
        self.state.tools = value

    @property
    def messages(self) -> list[AgentMessage]:
        """Get conversation messages."""
        return self.state.messages

    @messages.setter
    def messages(self, value: list[AgentMessage]) -> None:
        self.state.messages = value

    @property
    def is_streaming(self) -> bool:
        """True while a prompt or continuation is in flight."""
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

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def subscribe(self, listener: AgentEventListener) -> Callable[[], None]:
        """Subscribe to agent lifecycle events.

        Listeners are called in subscription order and awaited if they return an
        awaitable, so ``agent_end`` is not "done" until every listener has
        settled — which is what makes :meth:`wait_for_idle` mean anything to the
        session that persists messages from those events.
        """
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    # ------------------------------------------------------------------
    # Queues
    # ------------------------------------------------------------------

    @property
    def steering_mode(self) -> QueueMode:
        return self._steering_queue.mode

    @steering_mode.setter
    def steering_mode(self, mode: QueueMode) -> None:
        self._steering_queue.mode = mode

    @property
    def follow_up_mode(self) -> QueueMode:
        return self._follow_up_queue.mode

    @follow_up_mode.setter
    def follow_up_mode(self, mode: QueueMode) -> None:
        self._follow_up_queue.mode = mode

    def steer(self, message: AgentMessage) -> None:
        """Queue a message to be injected after the current assistant turn."""
        self._steering_queue.enqueue(message)

    def follow_up(self, message: AgentMessage) -> None:
        """Queue a message to run only after the agent would otherwise stop."""
        self._follow_up_queue.enqueue(message)

    def clear_steering_queue(self) -> None:
        self._steering_queue.clear()

    def clear_follow_up_queue(self) -> None:
        self._follow_up_queue.clear()

    def clear_all_queues(self) -> None:
        self.clear_steering_queue()
        self.clear_follow_up_queue()

    def has_queued_messages(self) -> bool:
        """True while either queue still holds a message."""
        return self._steering_queue.has_items() or self._follow_up_queue.has_items()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    @property
    def signal(self) -> AbortSignal | None:
        """Abort signal of the current run, if one is active."""
        return self._active_run.controller.signal if self._active_run else None

    def abort(self) -> None:
        """Abort the current run, if one is active."""
        if self._active_run:
            self._active_run.controller.abort()

    async def wait_for_idle(self) -> None:
        """Resolve once the current run and all its event listeners have finished."""
        if self._active_run is not None:
            await asyncio.shield(self._active_run.future)

    def reset(self) -> None:
        """Clear transcript state, runtime state, and queued messages."""
        self.state.messages = []
        self.state.is_streaming = False
        self.state.streaming_message = None
        self.state.pending_tool_calls = frozenset()
        self.state.error_message = None
        self.clear_all_queues()

    def clear_messages(self) -> None:
        """Clear all messages."""
        self.state.messages = []

    async def prompt(
        self,
        message: str | AgentMessage | list[AgentMessage],
        images: list[Any] | None = None,
    ) -> None:
        """Start a new turn from text, one message, or a batch of messages."""
        if self._active_run is not None:
            raise RuntimeError(
                "Agent is already processing a prompt. Use steer() or follow_up() "
                "to queue messages, or wait for completion."
            )
        messages = self._normalize_prompt_input(message, images)
        await self._run_prompt_messages(messages)

    async def continue_conversation(self) -> None:
        """Continue from the current transcript. Port of the TS's ``continue()``."""
        if self._active_run is not None:
            raise RuntimeError(
                "Agent is already processing. Wait for completion before continuing."
            )

        if not self.state.messages:
            raise ValueError("No messages to continue from")

        last_message = self.state.messages[-1]
        if _message_role(last_message) == "assistant":
            queued_steering = self._steering_queue.drain()
            if queued_steering:
                await self._run_prompt_messages(queued_steering, skip_initial_steering_poll=True)
                return

            queued_follow_ups = self._follow_up_queue.drain()
            if queued_follow_ups:
                await self._run_prompt_messages(queued_follow_ups)
                return

            raise ValueError("Cannot continue from message role: assistant")

        await self._run_continuation()

    def _normalize_prompt_input(
        self,
        message: str | AgentMessage | list[AgentMessage],
        images: list[Any] | None,
    ) -> list[AgentMessage]:
        if isinstance(message, list):
            return message
        if not isinstance(message, str):
            return [message]

        from cortex.ai.types import TextContent, UserMessage

        content: list[Any] = [TextContent(text=message)]
        if images:
            content.extend(images)
        return [UserMessage(content=content, timestamp=int(time.time() * 1000))]

    async def _run_prompt_messages(
        self,
        messages: list[AgentMessage],
        skip_initial_steering_poll: bool = False,
    ) -> None:
        async def executor(signal: AbortSignal) -> None:
            await run_agent_loop(
                prompts=messages,
                context=self._create_context_snapshot(),
                config=self._create_loop_config(skip_initial_steering_poll),
                emit=self._process_events,
                signal=signal,
                stream_fn=self.stream_fn,
            )

        await self._run_with_lifecycle(executor)

    async def _run_continuation(self) -> None:
        async def executor(signal: AbortSignal) -> None:
            await run_agent_loop_continue(
                context=self._create_context_snapshot(),
                config=self._create_loop_config(),
                emit=self._process_events,
                signal=signal,
                stream_fn=self.stream_fn,
            )

        await self._run_with_lifecycle(executor)

    def _create_context_snapshot(self) -> AgentContext:
        return AgentContext(
            system_prompt=self.state.system_prompt,
            messages=list(self.state.messages),
            tools=list(self.state.tools),
        )

    def _create_loop_config(self, skip_initial_steering_poll: bool = False) -> AgentLoopConfig:
        pending_skip = skip_initial_steering_poll

        async def get_steering_messages() -> list[AgentMessage]:
            nonlocal pending_skip
            if pending_skip:
                pending_skip = False
                return []
            return self._steering_queue.drain()

        async def get_follow_up_messages() -> list[AgentMessage]:
            return self._follow_up_queue.drain()

        async def prepare_next_turn(context: Any) -> Any:
            # Always provide the hook: a session may assign `prepare_next_turn`
            # after the run started, to refresh tools or the system prompt that a
            # tool changed mid-turn.
            if self.prepare_next_turn is None:
                return None
            result = self.prepare_next_turn(context, self.signal)
            if inspect.isawaitable(result):
                return await result
            return result

        return AgentLoopConfig(
            model=self.state.model,
            # The request-shaping half (`AgentLoopConfig extends SimpleStreamOptions`).
            # Every field here reaches the provider through the loop's spread, and
            # none of them did before 7.12: they were accepted by `AgentOptions`,
            # stored, and never read, so a session at thinking level `high` asked
            # for no thinking at all.
            reasoning=None if self.state.thinking_level == "off" else self.state.thinking_level,
            session_id=self.session_id,
            on_payload=self.options.on_payload,
            on_response=self.options.on_response,
            transport=self.options.transport,
            thinking_budgets=self.options.thinking_budgets,
            thinking_display=self.options.thinking_display,
            max_retry_delay_ms=self.options.max_retry_delay_ms,
            convert_to_llm=self.convert_to_llm,
            transform_context=self.transform_context,
            get_api_key=self.get_api_key,
            should_stop_after_turn=None,
            prepare_next_turn=prepare_next_turn,
            get_steering_messages=get_steering_messages,
            get_follow_up_messages=get_follow_up_messages,
            create_background_result_message=self.options.create_background_result_message,
            create_background_placeholder=self.options.create_background_placeholder,
            on_background_task_count_change=self.options.on_background_task_count_change,
            tool_execution=self.tool_execution,
            before_tool_call=self.before_tool_call,
            after_tool_call=self.after_tool_call,
        )

    async def _run_with_lifecycle(self, executor: Callable[[AbortSignal], Any]) -> None:
        if self._active_run is not None:
            raise RuntimeError("Agent is already processing.")

        controller = AbortController()
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._active_run = _ActiveRun(future=future, controller=controller)

        self.state.is_streaming = True
        self.state.streaming_message = None
        self.state.error_message = None

        try:
            await executor(controller.signal)
        except Exception as error:  # noqa: BLE001 - mirrors the TS catch-all
            await self._handle_run_failure(error, controller.aborted)
        finally:
            self._finish_run()

    async def _handle_run_failure(self, error: Exception, aborted: bool) -> None:
        """Turn a thrown run into the events a completed one would have emitted.

        The provider contract says failures arrive as a final message, not as an
        exception; anything that still escapes (a bug in a tool, a missing model)
        would otherwise leave the UI with a turn that started and never ended.
        """
        from cortex.ai.types import AssistantMessage, TextContent, Usage

        model = self.state.model or DEFAULT_MODEL
        failure_message = AssistantMessage(
            content=[TextContent(text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=Usage.model_validate(EMPTY_USAGE),
            stop_reason="aborted" if aborted else "error",
            error_message=str(error),
            timestamp=int(time.time() * 1000),
        )
        await self._process_events({"type": "message_start", "message": failure_message})
        await self._process_events({"type": "message_end", "message": failure_message})
        await self._process_events(
            {"type": "turn_end", "message": failure_message, "tool_results": []}
        )
        await self._process_events({"type": "agent_end", "messages": [failure_message]})

    def _finish_run(self) -> None:
        self.state.is_streaming = False
        self.state.streaming_message = None
        self.state.pending_tool_calls = frozenset()
        run = self._active_run
        self._active_run = None
        if run is not None and not run.future.done():
            run.future.set_result(None)

    async def _process_events(self, event: AgentEvent) -> None:
        """Reduce internal state for a loop event, then notify listeners."""
        event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", "")
        message = (
            event.get("message") if isinstance(event, dict) else getattr(event, "message", None)
        )

        if event_type == "message_start":
            self.state.streaming_message = message
        elif event_type == "message_update":
            self.state.streaming_message = message
        elif event_type == "message_end":
            self.state.streaming_message = None
            self.state.messages.append(message)
        elif event_type == "tool_execution_start":
            tool_call_id = _event_field(event, "tool_call_id")
            self.state.pending_tool_calls = self.state.pending_tool_calls | {tool_call_id}
        elif event_type == "tool_execution_end":
            tool_call_id = _event_field(event, "tool_call_id")
            self.state.pending_tool_calls = self.state.pending_tool_calls - {tool_call_id}
        elif event_type == "turn_end":
            turn_error = _field(message, "error_message")
            if _message_role(message) == "assistant" and turn_error:
                self.state.error_message = str(turn_error)
        elif event_type == "agent_end":
            self.state.streaming_message = None

        signal = self.signal
        if signal is None:
            raise RuntimeError("Agent listener invoked outside active run")
        for listener in list(self._listeners):
            result = listener(event, signal)
            if inspect.isawaitable(result):
                await result


def _event_field(event: AgentEvent, name: str) -> str:
    value = _field(event, name)
    return str(value) if value is not None else ""


def _field(value: Any, name: str) -> Any:
    """Read a field off an event or message, however the producer shaped it."""
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)

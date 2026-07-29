"""Agent loop module.

Mechanical port of hoocode's ``packages/agent/src/agent-loop.ts``.

This module implements the main agent loop that processes messages,
executes tools, and manages the conversation flow.

**Step 7.4 filled in the provider request.** ``_stream_assistant_response`` used
to raise, under a comment saying a real implementation would call the LLM, which
is why nothing above it had ever produced an assistant message. It is now the
port of the TS's ``streamAssistantResponse``: transform
the context, convert it to LLM messages, hand the request to the stream function
(``stream_simple`` by default) and turn the provider's event stream into
``message_start`` / ``message_update`` / ``message_end``.

**Step 7.5 filled in tool execution**, the other half the TS has and this had
not: ``_execute_tool_calls`` raised and named the step. It is now the port of
``executeToolCalls`` and everything under it — prepare (find the tool, run its
``prepare_arguments`` shim, validate against the schema, offer
``before_tool_call`` a veto), execute (sequential or parallel, streaming partial
results as ``tool_execution_update``), finalize (``after_tool_call``'s
field-by-field override) — plus the :class:`_BackgroundTaskManager` that lets a
``background`` tool answer its call with a placeholder and deliver the real
result as a follow-up message a turn later. With it the loop's inner condition
becomes the TS's three-way test: keep going while there are more tool calls,
queued messages, **or** background work still in flight.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from cortex.agent.types import (
    AfterToolCallContext,
    AgentTool,
    AgentToolCall,
    AgentToolResult,
    BeforeToolCallContext,
)
from cortex.ai.stream import stream_simple
from cortex.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Message,
    TextContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from cortex.ai.util import validate_tool_arguments

__all__ = [
    "AgentLoop",
    "AgentEventSink",
    "run_agent_loop",
    "run_agent_loop_continue",
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# Event sink callable
AgentEventSink = Callable[[Any], Any]


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` if it is awaitable, else return it. TS ``await`` on a union."""
    if inspect.isawaitable(value):
        return await value
    return value


def _partial_of(event: Any) -> AssistantMessage:
    """The partial message carried by a streaming event."""
    partial: AssistantMessage = event.partial
    return partial


def _snapshot_field(snapshot: Any, name: str) -> Any:
    """Read a field off an ``AgentLoopTurnUpdate``, however the caller shaped it."""
    if isinstance(snapshot, dict):
        return snapshot.get(name)
    return getattr(snapshot, name, None)


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read a field off a result or a tool, however the producer shaped it.

    A tool is app-supplied; the TS types it and reads the properties directly,
    and a Python one may just as reasonably return a dict.
    """
    if isinstance(value, dict):
        return value.get(name, default)
    found = getattr(value, name, default)
    return default if found is None else found


def _tool_calls_of(message: AssistantMessage) -> list[AgentToolCall]:
    """The ``toolCall`` blocks of an assistant message, in source order."""
    return [block for block in message.content if isinstance(block, ToolCall)]


def _to_llm_tools(tools: Any) -> list[Tool] | None:
    """Project the agent's tools onto the provider's tool schema.

    In TS the two types are structurally compatible and the context is passed
    straight through; here `Context.tools` is a pydantic model, so the extra
    fields an `AgentTool` carries (execute, execution mode, background) have to
    be dropped explicitly.
    """
    if not tools:
        return None
    projected: list[Tool] = []
    for tool in tools:
        if isinstance(tool, Tool):
            projected.append(tool)
            continue
        projected.append(
            Tool(
                name=str(getattr(tool, "name", "")),
                description=str(getattr(tool, "description", "")),
                parameters=getattr(tool, "parameters", None) or {},
            )
        )
    return projected


# ---------------------------------------------------------------------------
# Tool call outcomes
# ---------------------------------------------------------------------------


@dataclass
class _PreparedToolCall:
    """A tool call that passed preparation and is ready to run."""

    tool_call: AgentToolCall
    tool: AgentTool[Any, Any]
    args: Any


@dataclass
class _ImmediateToolCallOutcome:
    """A tool call resolved without running the tool (unknown, invalid, blocked)."""

    result: AgentToolResult[Any]
    is_error: bool


@dataclass
class _ExecutedToolCallOutcome:
    result: AgentToolResult[Any]
    is_error: bool


@dataclass
class _FinalizedToolCallOutcome:
    tool_call: AgentToolCall
    result: AgentToolResult[Any]
    is_error: bool


@dataclass
class _ExecutedToolCallBatch:
    messages: list[ToolResultMessage]
    terminate: bool


def _create_error_tool_result(message: str) -> AgentToolResult[Any]:
    return AgentToolResult(content=[TextContent(text=message)], details={})


def _create_tool_result_message(finalized: _FinalizedToolCallOutcome) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=finalized.tool_call.id,
        tool_name=finalized.tool_call.name,
        content=finalized.result.content,
        details=finalized.result.details,
        is_error=finalized.is_error,
        timestamp=int(time.time() * 1000),
    )


def _should_terminate_tool_batch(finalized_calls: list[_FinalizedToolCallOutcome]) -> bool:
    return len(finalized_calls) > 0 and all(
        finalized.result.terminate is True for finalized in finalized_calls
    )


def _order_tool_results_by_source(
    tool_calls: list[AgentToolCall],
    messages: list[ToolResultMessage],
) -> list[ToolResultMessage]:
    """Reorder finalized tool result messages to match the assistant's call order."""
    by_id = {message.tool_call_id: message for message in messages}
    ordered: list[ToolResultMessage] = []
    for tool_call in tool_calls:
        message = by_id.get(tool_call.id)
        if message is not None:
            ordered.append(message)
    return ordered


# ---------------------------------------------------------------------------
# Background tools
# ---------------------------------------------------------------------------


class _BackgroundTaskManager:
    """Tracks tool calls running detached from the main loop.

    The loop polls :meth:`pending_count` to stay alive while work is in flight,
    awaits :meth:`wait_for_next` when it has nothing else to do, and drains
    finished results with :meth:`drain_results`.
    """

    def __init__(self, on_count_change: Callable[[int], None] | None = None) -> None:
        self._results: list[Message] = []
        self._inflight: set[asyncio.Task[None]] = set()
        self._waiter: asyncio.Future[None] | None = None
        self._on_count_change = on_count_change

    def pending_count(self) -> int:
        return len(self._inflight)

    def drain_results(self) -> list[Message]:
        drained = self._results
        self._results = []
        return drained

    def wait_for_next(self) -> Any:
        """Resolve once another in-flight task settles, or at once if none are."""
        if self._results or not self._inflight:
            return _resolved()
        if self._waiter is None:
            self._waiter = asyncio.get_running_loop().create_future()
        return self._waiter

    def spawn(self, run: Callable[[], Any]) -> None:
        async def task() -> None:
            try:
                value = await _maybe_await(run())
                self._results.append(value)
            except Exception as error:  # noqa: BLE001 - the TS catch, one for one
                # `run` is expected to encode failures into its result message, so
                # a throw here is unexpected. Push a minimal error message rather
                # than silently losing the result.
                self._results.append(
                    UserMessage(
                        content=[
                            TextContent(
                                text=f"A background tool failed unexpectedly: {error}",
                            )
                        ],
                        timestamp=int(time.time() * 1000),
                    )
                )

        handle = asyncio.ensure_future(task())
        self._inflight.add(handle)
        self._notify()
        handle.add_done_callback(self._settled)

    def _settled(self, handle: asyncio.Task[None]) -> None:
        self._inflight.discard(handle)
        self._notify()
        waiter = self._waiter
        if waiter is not None:
            self._waiter = None
            if not waiter.done():
                waiter.set_result(None)

    def _notify(self) -> None:
        if self._on_count_change is None:
            return
        self._on_count_change(len(self._inflight))


async def _resolved() -> None:
    """An already-settled awaitable — the TS's ``Promise.resolve()``."""
    return None


async def _already(value: _FinalizedToolCallOutcome) -> _FinalizedToolCallOutcome:
    """``Promise.resolve(value)``, so a settled entry can join a ``gather``."""
    return value


async def _gather_all(handles: list[Any]) -> None:
    """Await every scheduled emit — the TS's ``await Promise.all(updateEvents)``."""
    if handles:
        await asyncio.gather(*handles)


def _prepare_tool_call_arguments(
    tool: AgentTool[Any, Any],
    tool_call: AgentToolCall,
) -> AgentToolCall:
    """Run a tool's compatibility shim over the raw arguments, if it has one."""
    prepare = getattr(tool, "prepare_arguments", None)
    if prepare is None:
        return tool_call
    prepared_arguments = prepare(tool_call.arguments)
    if prepared_arguments is tool_call.arguments:
        return tool_call
    return tool_call.model_copy(update={"arguments": prepared_arguments})


def _is_background_tool(context: Any, tool_call: AgentToolCall) -> bool:
    tool = _find_tool(context, tool_call.name)
    background = getattr(tool, "background", None) if tool is not None else None
    if callable(background):
        try:
            return background(tool_call) is True
        except Exception:  # noqa: BLE001 - a throwing predicate must not break dispatch
            return False
    return background is True


def _find_tool(context: Any, name: str) -> AgentTool[Any, Any] | None:
    for tool in getattr(context, "tools", None) or []:
        if getattr(tool, "name", None) == name:
            return tool
    return None


def _create_background_placeholder_outcome(
    tool_call: AgentToolCall,
    config: Any,
) -> _FinalizedToolCallOutcome:
    """The result returned immediately for a dispatched background tool call."""
    # Apps can supply a verbose, tool-specific explanation (which subagent / MCP
    # tool, an args summary). Fall back to a generic line when absent or on error.
    text: str | None = None
    if getattr(config, "create_background_placeholder", None):
        try:
            text = config.create_background_placeholder(tool_call)
        except Exception:  # noqa: BLE001 - contract says it must not throw; survive it anyway
            text = None
    if text is None:
        text = (
            f'Started "{tool_call.name}" in the background. Its result will arrive as a '
            "follow-up message once it finishes — keep working in the meantime."
        )
    return _FinalizedToolCallOutcome(
        tool_call=tool_call,
        result=AgentToolResult(
            content=[TextContent(text=text)],
            details={"background": True, "status": "running"},
        ),
        is_error=False,
    )


def _create_default_background_result_message(
    finalized: _FinalizedToolCallOutcome,
) -> UserMessage:
    """The default follow-up message carrying a finished background tool's result.

    The tool call itself was already answered by a placeholder, so the real result
    is delivered as a fresh user message (rather than a second tool result for the
    same id) and injected like a steering message on the next iteration.
    """
    verb = "failed" if finalized.is_error else "finished"
    header = f'Background tool "{finalized.tool_call.name}" (id {finalized.tool_call.id}) {verb}:'
    content: list[TextContent | ImageContent] = [TextContent(text=header)]
    content.extend(finalized.result.content)
    return UserMessage(content=content, timestamp=int(time.time() * 1000))


# ---------------------------------------------------------------------------
# Agent loop class
# ---------------------------------------------------------------------------


@dataclass
class AgentLoop:
    """Main agent loop that processes messages and executes tools."""

    config: Any  # AgentLoopConfig
    context: Any = None  # AgentContext
    emit: AgentEventSink | None = None
    signal: Any = None  # AbortSignal
    stream_fn: Any = None  # StreamFn

    def __post_init__(self) -> None:
        if self.context is None:
            from cortex.agent.types import AgentContext

            self.context = AgentContext(system_prompt="", messages=[])

    async def run(self, prompts: list[Message]) -> list[Message]:
        """Run the agent loop with initial prompts."""
        new_messages: list[Message] = list(prompts)
        current_context = self.context
        current_context.messages.extend(prompts)

        # Emit agent_start event
        await self._emit({"type": "agent_start"})
        await self._emit({"type": "turn_start"})

        # Emit message events for prompts
        for prompt in prompts:
            await self._emit({"type": "message_start", "message": prompt})
            await self._emit({"type": "message_end", "message": prompt})

        # Run the main loop
        await self._run_loop(current_context, new_messages)

        return new_messages

    async def run_continue(self) -> list[Message]:
        """Continue the agent loop from the current context."""
        if not self.context.messages:
            raise ValueError("Cannot continue: no messages in context")

        last_message = self.context.messages[-1]
        if hasattr(last_message, "role") and last_message.role == "assistant":
            raise ValueError("Cannot continue from message role: assistant")

        new_messages: list[Message] = []

        # Emit agent_start event
        await self._emit({"type": "agent_start"})
        await self._emit({"type": "turn_start"})

        # Run the main loop
        await self._run_loop(self.context, new_messages)

        return new_messages

    async def _emit(self, event: Any) -> None:
        """Emit an event to the event sink."""
        if self.emit is not None:
            await _maybe_await(self.emit(event))
            return
        if getattr(self.config, "on_event", None):
            await _maybe_await(self.config.on_event(event))

    async def _run_loop(
        self,
        context: Any,
        new_messages: list[Message],
    ) -> None:
        """Main loop logic. Port of ``runLoop``."""
        current_context = context
        config = self.config
        first_turn = True

        # Tracks tool calls that run detached. Their results are injected into a
        # later turn as follow-up messages instead of blocking the loop. `config`
        # is reassigned across turns, so the callback is read lazily.
        def on_background_count_change(count: int) -> None:
            callback = getattr(config, "on_background_task_count_change", None)
            if callback is None:
                return
            try:
                callback(count)
            except Exception:  # noqa: BLE001 - a throwing callback must not break dispatch
                pass

        background = _BackgroundTaskManager(on_background_count_change)

        # Check for steering messages at start (the user may have typed while waiting).
        pending_messages: list[Message] = await self._collect_pending_messages(config, background)

        while True:
            has_more_tool_calls = True

            # Inner loop: process tool calls, steering messages, and background
            # tool results. Background tools keep the loop alive: while one is
            # still running we stay here so the agent can react to its result.
            while has_more_tool_calls or pending_messages or background.pending_count() > 0:
                # Nothing new to act on yet, but background work is still in
                # flight: wait for the next task to settle rather than spinning an
                # empty turn, then inject whatever it produced.
                if not has_more_tool_calls and not pending_messages:
                    await background.wait_for_next()
                    pending_messages = await self._collect_pending_messages(config, background)
                    if not pending_messages:
                        continue

                if not first_turn:
                    await self._emit({"type": "turn_start"})
                else:
                    first_turn = False

                # Process pending messages (inject before the next assistant response)
                if pending_messages:
                    for queued in pending_messages:
                        await self._emit({"type": "message_start", "message": queued})
                        await self._emit({"type": "message_end", "message": queued})
                        current_context.messages.append(queued)
                        new_messages.append(queued)
                    pending_messages = []

                # Stream assistant response
                message = await self._stream_assistant_response(current_context, config)
                new_messages.append(message)

                # Check for error or abort
                if hasattr(message, "stop_reason") and message.stop_reason in ("error", "aborted"):
                    await self._emit({"type": "turn_end", "message": message, "tool_results": []})
                    await self._emit({"type": "agent_end", "messages": new_messages})
                    return

                # Check for tool calls
                tool_calls = _tool_calls_of(message)

                tool_results: list[ToolResultMessage] = []
                has_more_tool_calls = False

                if tool_calls:
                    executed_batch = await self._execute_tool_calls(
                        current_context, message, config, background
                    )
                    tool_results.extend(executed_batch.messages)
                    has_more_tool_calls = not executed_batch.terminate

                    for result in tool_results:
                        current_context.messages.append(result)
                        new_messages.append(result)

                await self._emit(
                    {
                        "type": "turn_end",
                        "message": message,
                        "tool_results": tool_results,
                    }
                )

                next_turn_context = {
                    "message": message,
                    "tool_results": tool_results,
                    "context": current_context,
                    "new_messages": new_messages,
                }

                # A session can swap the frozen context between turns (tools or a
                # system prompt that changed mid-run).
                if getattr(config, "prepare_next_turn", None):
                    snapshot = await _maybe_await(config.prepare_next_turn(next_turn_context))
                    if snapshot is not None:
                        current_context = _snapshot_field(snapshot, "context") or current_context
                        next_model = _snapshot_field(snapshot, "model")
                        if next_model is not None:
                            config = replace(config, model=next_model)

                # Check if we should stop
                if getattr(config, "should_stop_after_turn", None):
                    if await _maybe_await(config.should_stop_after_turn(next_turn_context)):
                        await self._emit({"type": "agent_end", "messages": new_messages})
                        return

                pending_messages = await self._collect_pending_messages(config, background)

            # The agent would stop here. Check for follow-up messages.
            follow_up_messages: list[Message] = []
            if getattr(config, "get_follow_up_messages", None):
                follow_up_messages = await _maybe_await(config.get_follow_up_messages()) or []

            if follow_up_messages:
                # Set as pending so the inner loop processes them.
                pending_messages = follow_up_messages
                continue

            # No more messages, exit
            break

        await self._emit({"type": "agent_end", "messages": new_messages})

    async def _collect_pending_messages(
        self,
        config: Any,
        background: _BackgroundTaskManager,
    ) -> list[Message]:
        """Messages to inject before the next assistant turn.

        Results from finished background tools take priority, then app-provided
        steering messages.
        """
        pending: list[Message] = background.drain_results()
        if getattr(config, "get_steering_messages", None):
            pending.extend(await _maybe_await(config.get_steering_messages()) or [])
        return pending

    async def _stream_assistant_response(
        self,
        context: Any,
        config: Any,
    ) -> AssistantMessage:
        """Stream one assistant response. Port of ``streamAssistantResponse``.

        This is where ``AgentMessage[]`` becomes ``Message[]``: the transcript the
        agent keeps includes roles the provider has never heard of, so it goes
        through ``convert_to_llm`` before the request is built.
        """
        messages = context.messages
        if getattr(config, "transform_context", None):
            messages = await _maybe_await(config.transform_context(messages, self.signal))

        convert_to_llm = getattr(config, "convert_to_llm", None)
        llm_messages = (
            await _maybe_await(convert_to_llm(messages)) if convert_to_llm else list(messages)
        )

        llm_context = Context(
            system_prompt=context.system_prompt,
            messages=llm_messages,
            tools=_to_llm_tools(getattr(context, "tools", None)),
        )

        stream_function = self.stream_fn or stream_simple

        # Resolve the API key per request: tokens expire, and a run can outlive one.
        resolved_api_key = None
        if getattr(config, "get_api_key", None):
            resolved_api_key = await _maybe_await(config.get_api_key(config.model.provider))

        options: dict[str, Any] = dict(vars(config))
        options["api_key"] = resolved_api_key
        options["signal"] = self.signal

        response = stream_function(config.model, llm_context, options)

        partial_message: AssistantMessage | None = None
        added_partial = False

        async for event in response:
            # `event.partial` exists on every event but the two terminal ones,
            # which is why the tag is read before the field is.
            if event.type == "start":
                partial_message = _partial_of(event)
                context.messages.append(partial_message)
                added_partial = True
                await self._emit({"type": "message_start", "message": partial_message})
            elif event.type in (
                "text_start",
                "text_delta",
                "text_end",
                "thinking_start",
                "thinking_delta",
                "thinking_end",
                "toolcall_start",
                "toolcall_delta",
                "toolcall_end",
            ):
                if partial_message is not None:
                    partial_message = _partial_of(event)
                    context.messages[-1] = partial_message
                    await self._emit(
                        {
                            "type": "message_update",
                            "assistant_message_event": event,
                            "message": partial_message,
                        }
                    )
            elif event.type in ("done", "error"):
                final_message = await response.result()
                if added_partial:
                    context.messages[-1] = final_message
                else:
                    context.messages.append(final_message)
                    await self._emit({"type": "message_start", "message": final_message})
                await self._emit({"type": "message_end", "message": final_message})
                return final_message

        final_message = await response.result()
        if added_partial:
            context.messages[-1] = final_message
        else:
            context.messages.append(final_message)
            await self._emit({"type": "message_start", "message": final_message})
        await self._emit({"type": "message_end", "message": final_message})
        return final_message

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool_calls(
        self,
        context: Any,
        message: AssistantMessage,
        config: Any,
        background: _BackgroundTaskManager,
    ) -> _ExecutedToolCallBatch:
        """Execute the tool calls in an assistant message. Port of ``executeToolCalls``.

        Calls are partitioned into foreground and background. Foreground calls are
        awaited (sequentially or in parallel). Background calls return a
        placeholder result immediately and run detached; the loop injects their
        real results into a later turn as follow-up messages.

        Result messages come back in assistant source order so every tool call is
        answered in place, whichever partition produced it.
        """
        tool_calls = _tool_calls_of(message)
        # Single pass so a per-call `background` predicate runs exactly once.
        background_calls: list[AgentToolCall] = []
        foreground_calls: list[AgentToolCall] = []
        for tool_call in tool_calls:
            is_background = _is_background_tool(context, tool_call)
            (background_calls if is_background else foreground_calls).append(tool_call)

        # Dispatch background calls first so their placeholder results are ready
        # before any (potentially slow) foreground tool starts.
        background_messages = await self._dispatch_background_tool_calls(
            context, message, background_calls, config, background
        )

        foreground_batch = _ExecutedToolCallBatch(messages=[], terminate=False)
        if foreground_calls:
            has_sequential_tool_call = any(
                getattr(_find_tool(context, call.name), "execution_mode", None) == "sequential"
                for call in foreground_calls
            )
            if getattr(config, "tool_execution", "parallel") == "sequential" or (
                has_sequential_tool_call
            ):
                foreground_batch = await self._execute_tool_calls_sequential(
                    context, message, foreground_calls, config
                )
            else:
                foreground_batch = await self._execute_tool_calls_parallel(
                    context, message, foreground_calls, config
                )

        return _ExecutedToolCallBatch(
            messages=_order_tool_results_by_source(
                tool_calls, [*background_messages, *foreground_batch.messages]
            ),
            # Only foreground results can request early termination; a batch made
            # up entirely of background dispatches never terminates the loop.
            terminate=foreground_batch.terminate,
        )

    async def _dispatch_background_tool_calls(
        self,
        context: Any,
        message: AssistantMessage,
        tool_calls: list[AgentToolCall],
        config: Any,
        background: _BackgroundTaskManager,
    ) -> list[ToolResultMessage]:
        """Start background tool calls without blocking the loop.

        Each one is answered with a placeholder result immediately (so the
        assistant's tool call is satisfied) and the real work runs detached.
        Preparation failures — unknown tool, invalid arguments, blocked by
        ``before_tool_call`` — resolve synchronously, exactly like foreground calls.
        """
        messages: list[ToolResultMessage] = []

        for tool_call in tool_calls:
            await self._emit_tool_execution_start(tool_call)

            preparation = await self._prepare_tool_call(context, message, tool_call, config)
            if isinstance(preparation, _ImmediateToolCallOutcome):
                finalized = _FinalizedToolCallOutcome(
                    tool_call=tool_call,
                    result=preparation.result,
                    is_error=preparation.is_error,
                )
                await self._emit_tool_execution_end(finalized)
                result_message = _create_tool_result_message(finalized)
                await self._emit_tool_result_message(result_message)
                messages.append(result_message)
                continue

            # Prepared: answer the call with a placeholder now, run the real work
            # detached, and surface its result on a later turn.
            placeholder = _create_background_placeholder_outcome(tool_call, config)
            await self._emit_tool_execution_end(placeholder)
            placeholder_message = _create_tool_result_message(placeholder)
            await self._emit_tool_result_message(placeholder_message)
            messages.append(placeholder_message)

            prepared = preparation

            async def run(prepared: _PreparedToolCall = prepared) -> Message:
                # Background tools own their lifecycle; suppress streaming update
                # events, since the execution already "ended" above.
                executed = await self._execute_prepared_tool_call(prepared, emit_updates=False)
                finalized = await self._finalize_executed_tool_call(
                    context, message, prepared, executed, config
                )
                if getattr(config, "create_background_result_message", None):
                    from cortex.agent.types import BackgroundToolResult

                    return await _maybe_await(
                        config.create_background_result_message(
                            BackgroundToolResult(
                                tool_call=finalized.tool_call,
                                result=finalized.result,
                                is_error=finalized.is_error,
                            )
                        )
                    )
                return _create_default_background_result_message(finalized)

            background.spawn(run)

        return messages

    async def _execute_tool_calls_sequential(
        self,
        context: Any,
        message: AssistantMessage,
        tool_calls: list[AgentToolCall],
        config: Any,
    ) -> _ExecutedToolCallBatch:
        finalized_calls: list[_FinalizedToolCallOutcome] = []
        messages: list[ToolResultMessage] = []

        for tool_call in tool_calls:
            await self._emit_tool_execution_start(tool_call)

            preparation = await self._prepare_tool_call(context, message, tool_call, config)
            if isinstance(preparation, _ImmediateToolCallOutcome):
                finalized = _FinalizedToolCallOutcome(
                    tool_call=tool_call,
                    result=preparation.result,
                    is_error=preparation.is_error,
                )
            else:
                executed = await self._execute_prepared_tool_call(preparation)
                finalized = await self._finalize_executed_tool_call(
                    context, message, preparation, executed, config
                )

            await self._emit_tool_execution_end(finalized)
            result_message = _create_tool_result_message(finalized)
            await self._emit_tool_result_message(result_message)
            finalized_calls.append(finalized)
            messages.append(result_message)

        return _ExecutedToolCallBatch(
            messages=messages,
            terminate=_should_terminate_tool_batch(finalized_calls),
        )

    async def _execute_tool_calls_parallel(
        self,
        context: Any,
        message: AssistantMessage,
        tool_calls: list[AgentToolCall],
        config: Any,
    ) -> _ExecutedToolCallBatch:
        """Prepare sequentially, execute concurrently.

        ``tool_execution_end`` is emitted in *completion* order, as each tool
        finalizes; the result messages are emitted afterwards in assistant source
        order. Both orders are load-bearing and the TS is explicit about them.
        """
        entries: list[Any] = []

        for tool_call in tool_calls:
            await self._emit_tool_execution_start(tool_call)

            preparation = await self._prepare_tool_call(context, message, tool_call, config)
            if isinstance(preparation, _ImmediateToolCallOutcome):
                finalized = _FinalizedToolCallOutcome(
                    tool_call=tool_call,
                    result=preparation.result,
                    is_error=preparation.is_error,
                )
                await self._emit_tool_execution_end(finalized)
                entries.append(finalized)
                continue

            async def run(
                prepared: _PreparedToolCall = preparation,
            ) -> _FinalizedToolCallOutcome:
                executed = await self._execute_prepared_tool_call(prepared)
                finalized = await self._finalize_executed_tool_call(
                    context, message, prepared, executed, config
                )
                await self._emit_tool_execution_end(finalized)
                return finalized

            entries.append(run())

        ordered_finalized_calls: list[_FinalizedToolCallOutcome] = list(
            await asyncio.gather(
                *[entry if inspect.isawaitable(entry) else _already(entry) for entry in entries]
            )
        )

        messages: list[ToolResultMessage] = []
        for finalized in ordered_finalized_calls:
            result_message = _create_tool_result_message(finalized)
            await self._emit_tool_result_message(result_message)
            messages.append(result_message)

        return _ExecutedToolCallBatch(
            messages=messages,
            terminate=_should_terminate_tool_batch(ordered_finalized_calls),
        )

    async def _prepare_tool_call(
        self,
        context: Any,
        message: AssistantMessage,
        tool_call: AgentToolCall,
        config: Any,
    ) -> _PreparedToolCall | _ImmediateToolCallOutcome:
        """Find the tool, validate the arguments, offer ``before_tool_call`` a veto."""
        tool = _find_tool(context, tool_call.name)
        if tool is None:
            return _ImmediateToolCallOutcome(
                result=_create_error_tool_result(f"Tool {tool_call.name} not found"),
                is_error=True,
            )

        try:
            prepared_tool_call = _prepare_tool_call_arguments(tool, tool_call)
            validated_args = validate_tool_arguments(
                Tool(
                    name=tool.name,
                    description=tool.description,
                    parameters=getattr(tool, "parameters", None) or {},
                ),
                prepared_tool_call,
            )
            if getattr(config, "before_tool_call", None):
                before_result = await _maybe_await(
                    config.before_tool_call(
                        BeforeToolCallContext(
                            assistant_message=message,
                            tool_call=tool_call,
                            args=validated_args,
                            context=context,
                        ),
                        self.signal,
                    )
                )
                if before_result is not None and _field(before_result, "block", False):
                    return _ImmediateToolCallOutcome(
                        result=_create_error_tool_result(
                            _field(before_result, "reason", None) or "Tool execution was blocked"
                        ),
                        is_error=True,
                    )
            return _PreparedToolCall(tool_call=tool_call, tool=tool, args=validated_args)
        except Exception as error:  # noqa: BLE001 - the TS catch, one for one
            return _ImmediateToolCallOutcome(
                result=_create_error_tool_result(str(error)),
                is_error=True,
            )

    async def _execute_prepared_tool_call(
        self,
        prepared: _PreparedToolCall,
        emit_updates: bool = True,
    ) -> _ExecutedToolCallOutcome:
        """Run the tool, streaming whatever partial results it reports.

        The TS pushes each `emit` **promise** onto a list and awaits them all
        afterwards, so an update reaches the UI as the tool produces it rather
        than in a batch at the end. `ensure_future` is that: the emit starts on
        the next loop pass, and the handles are awaited before returning.
        """
        update_events: list[Any] = []

        def on_update(partial_result: Any) -> None:
            if not emit_updates:
                return
            update_events.append(
                asyncio.ensure_future(
                    self._emit(
                        {
                            "type": "tool_execution_update",
                            "tool_call_id": prepared.tool_call.id,
                            "tool_name": prepared.tool_call.name,
                            "args": prepared.tool_call.arguments,
                            "partial_result": partial_result,
                        }
                    )
                )
            )

        execute = prepared.tool.execute
        if execute is None:
            return _ExecutedToolCallOutcome(
                result=_create_error_tool_result(f"Tool {prepared.tool_call.name} has no execute"),
                is_error=True,
            )

        try:
            result = await _maybe_await(
                execute(prepared.tool_call.id, prepared.args, self.signal, on_update)
            )
            await _gather_all(update_events)
            return _ExecutedToolCallOutcome(result=result, is_error=False)
        except Exception as error:  # noqa: BLE001 - the TS catch, one for one
            await _gather_all(update_events)
            return _ExecutedToolCallOutcome(
                result=_create_error_tool_result(str(error)),
                is_error=True,
            )

    async def _finalize_executed_tool_call(
        self,
        context: Any,
        message: AssistantMessage,
        prepared: _PreparedToolCall,
        executed: _ExecutedToolCallOutcome,
        config: Any,
    ) -> _FinalizedToolCallOutcome:
        """Apply ``after_tool_call``'s field-by-field override. No deep merge."""
        result = executed.result
        is_error = executed.is_error

        if getattr(config, "after_tool_call", None):
            try:
                after_result = await _maybe_await(
                    config.after_tool_call(
                        AfterToolCallContext(
                            assistant_message=message,
                            tool_call=prepared.tool_call,
                            args=prepared.args,
                            result=result,
                            is_error=is_error,
                            context=context,
                        ),
                        self.signal,
                    )
                )
                if after_result is not None:
                    override_content = _field(after_result, "content", None)
                    override_details = _field(after_result, "details", None)
                    override_terminate = _field(after_result, "terminate", None)
                    override_is_error = _field(after_result, "is_error", None)
                    result = AgentToolResult(
                        content=(
                            override_content if override_content is not None else result.content
                        ),
                        details=(
                            override_details if override_details is not None else result.details
                        ),
                        terminate=(
                            override_terminate
                            if override_terminate is not None
                            else result.terminate
                        ),
                    )
                    if override_is_error is not None:
                        is_error = bool(override_is_error)
            except Exception as error:  # noqa: BLE001 - the TS catch, one for one
                result = _create_error_tool_result(str(error))
                is_error = True

        return _FinalizedToolCallOutcome(
            tool_call=prepared.tool_call,
            result=result,
            is_error=is_error,
        )

    async def _emit_tool_execution_start(self, tool_call: AgentToolCall) -> None:
        await self._emit(
            {
                "type": "tool_execution_start",
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "args": tool_call.arguments,
            }
        )

    async def _emit_tool_execution_end(self, finalized: _FinalizedToolCallOutcome) -> None:
        await self._emit(
            {
                "type": "tool_execution_end",
                "tool_call_id": finalized.tool_call.id,
                "tool_name": finalized.tool_call.name,
                "result": finalized.result,
                "is_error": finalized.is_error,
            }
        )

    async def _emit_tool_result_message(self, tool_result_message: ToolResultMessage) -> None:
        await self._emit({"type": "message_start", "message": tool_result_message})
        await self._emit({"type": "message_end", "message": tool_result_message})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_agent_loop(
    prompts: list[Message],
    context: Any,
    config: Any,
    emit: AgentEventSink | None = None,
    signal: Any = None,
    stream_fn: Any = None,
) -> list[Message]:
    """Run the agent loop with initial prompts.

    Args:
        prompts: Initial messages to process.
        context: Agent context.
        config: Agent loop configuration.
        emit: Event sink for loop events.
        signal: Optional abort signal.
        stream_fn: Optional stream function.

    Returns:
        List of new messages generated during the loop.
    """
    loop = AgentLoop(
        config=config,
        context=context,
        emit=emit,
        signal=signal,
        stream_fn=stream_fn,
    )
    return await loop.run(prompts)


async def run_agent_loop_continue(
    context: Any,
    config: Any,
    emit: AgentEventSink | None = None,
    signal: Any = None,
    stream_fn: Any = None,
) -> list[Message]:
    """Continue the agent loop from the current context.

    Args:
        context: Agent context with existing messages.
        config: Agent loop configuration.
        emit: Event sink for loop events.
        signal: Optional abort signal.
        stream_fn: Optional stream function.

    Returns:
        List of new messages generated during the loop.
    """
    loop = AgentLoop(
        config=config,
        context=context,
        emit=emit,
        signal=signal,
        stream_fn=stream_fn,
    )
    return await loop.run_continue()

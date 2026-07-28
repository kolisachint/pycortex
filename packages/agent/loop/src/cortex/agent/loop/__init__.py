"""Agent loop module.

Mechanical port of hoocode's ``packages/agent/src/agent-loop.ts``.

This module implements the main agent loop that processes messages,
executes tools, and manages the conversation flow.

**Step 7.4 filled in the provider request.** ``_stream_assistant_response`` was a
``raise NotImplementedError`` with a comment saying a real implementation would
call the LLM, which is why nothing above it had ever produced an assistant
message. It is now the port of the TS's ``streamAssistantResponse``: transform
the context, convert it to LLM messages, hand the request to the stream function
(``stream_simple`` by default) and turn the provider's event stream into
``message_start`` / ``message_update`` / ``message_end``.

Tool execution is still step **7.5**'s: ``_execute_tool_calls`` says so, out
loud, rather than returning an empty batch — an empty batch with
``terminate=False`` sends the loop round again with the same assistant message
and spins forever, and a hang is a worse answer than an error the user can read.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from cortex.ai.stream import stream_simple
from cortex.ai.types import (
    AssistantMessage,
    Context,
    Message,
    Tool,
    ToolResultMessage,
)

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
        """Main loop logic. Port of ``runLoop``.

        Background tools are absent: they live inside ``executeToolCalls`` and its
        ``BackgroundTaskManager``, which arrive with step 7.5. The inner loop's
        condition therefore reads ``has_more_tool_calls or pending_messages``
        rather than the TS's three-way test.
        """
        current_context = context
        config = self.config
        first_turn = True

        # Check for steering messages at start (the user may have typed while waiting).
        pending_messages: list[Message] = await self._collect_pending_messages(config)

        while True:
            has_more_tool_calls = True

            # Inner loop: process tool calls and steering messages.
            while has_more_tool_calls or pending_messages:
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
                tool_calls = []
                if hasattr(message, "content"):
                    tool_calls = [
                        c for c in message.content if hasattr(c, "type") and c.type == "toolCall"
                    ]

                tool_results: list[ToolResultMessage] = []
                has_more_tool_calls = False

                if tool_calls:
                    executed_batch = await self._execute_tool_calls(
                        current_context, message, config
                    )
                    tool_results.extend(executed_batch.get("messages", []))
                    has_more_tool_calls = not executed_batch.get("terminate", False)

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

                pending_messages = await self._collect_pending_messages(config)

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

    async def _collect_pending_messages(self, config: Any) -> list[Message]:
        """Steering messages waiting to be injected before the next turn.

        The TS drains finished background tool results first and appends steering
        after them; with background tools unported (7.5) only the steering half
        exists.
        """
        if not getattr(config, "get_steering_messages", None):
            return []
        return await _maybe_await(config.get_steering_messages()) or []

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

    async def _execute_tool_calls(
        self,
        context: Any,
        message: AssistantMessage,
        config: Any,
    ) -> dict[str, Any]:
        """Execute the assistant's tool calls. **Step 7.5 delivers this.**"""
        raise NotImplementedError(
            "Tool execution arrives with migration step 7.5 (packages/agent/loop). "
            "The model asked for a tool this build cannot run."
        )


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

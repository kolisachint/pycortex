"""Agent loop module.

Mechanical port of hoocode's ``packages/agent/src/agent-loop.ts``.

This module implements the main agent loop that processes messages,
executes tools, and manages the conversation flow.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cortex.ai.types import (
    AssistantMessage,
    Message,
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


# ---------------------------------------------------------------------------
# Agent loop class
# ---------------------------------------------------------------------------


@dataclass
class AgentLoop:
    """Main agent loop that processes messages and executes tools."""

    config: Any  # AgentLoopConfig
    context: Any = None  # AgentContext
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
        if hasattr(self.config, "on_event") and self.config.on_event:
            await self.config.on_event(event)

    async def _run_loop(
        self,
        context: Any,
        new_messages: list[Message],
    ) -> None:
        """Main loop logic."""
        current_context = context
        config = self.config
        first_turn = True

        while True:
            has_more_tool_calls = True

            # Inner loop: process tool calls and messages
            while has_more_tool_calls:
                if not first_turn:
                    await self._emit({"type": "turn_start"})
                else:
                    first_turn = False

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

                # Check if we should stop
                if hasattr(config, "should_stop_after_turn") and config.should_stop_after_turn:
                    stop_context = {
                        "message": message,
                        "tool_results": tool_results,
                        "context": current_context,
                        "new_messages": new_messages,
                    }
                    if await config.should_stop_after_turn(stop_context):
                        await self._emit({"type": "agent_end", "messages": new_messages})
                        return

            # Check for follow-up messages
            follow_up_messages = []
            if hasattr(config, "get_follow_up_messages") and config.get_follow_up_messages:
                follow_up_messages = await config.get_follow_up_messages()

            if follow_up_messages:
                for msg in follow_up_messages:
                    await self._emit({"type": "message_start", "message": msg})
                    await self._emit({"type": "message_end", "message": msg})
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                continue

            # No more work to do
            break

        await self._emit({"type": "agent_end", "messages": new_messages})

    async def _stream_assistant_response(
        self,
        context: Any,
        config: Any,
    ) -> AssistantMessage:
        """Stream assistant response from the LLM."""
        # This is a simplified implementation
        # In the real implementation, this would call the LLM and stream the response
        raise NotImplementedError("Assistant response streaming not implemented yet")

    async def _execute_tool_calls(
        self,
        context: Any,
        message: AssistantMessage,
        config: Any,
    ) -> dict[str, Any]:
        """Execute tool calls from the assistant message."""
        # This is a simplified implementation
        # In the real implementation, this would execute each tool call
        return {"messages": [], "terminate": False}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_agent_loop(
    prompts: list[Message],
    context: Any,
    config: Any,
    signal: Any = None,
    stream_fn: Any = None,
) -> list[Message]:
    """Run the agent loop with initial prompts.

    Args:
        prompts: Initial messages to process.
        context: Agent context.
        config: Agent loop configuration.
        signal: Optional abort signal.
        stream_fn: Optional stream function.

    Returns:
        List of new messages generated during the loop.
    """
    loop = AgentLoop(
        config=config,
        context=context,
        signal=signal,
        stream_fn=stream_fn,
    )
    return await loop.run(prompts)


async def run_agent_loop_continue(
    context: Any,
    config: Any,
    signal: Any = None,
    stream_fn: Any = None,
) -> list[Message]:
    """Continue the agent loop from the current context.

    Args:
        context: Agent context with existing messages.
        config: Agent loop configuration.
        signal: Optional abort signal.
        stream_fn: Optional stream function.

    Returns:
        List of new messages generated during the loop.
    """
    loop = AgentLoop(
        config=config,
        context=context,
        signal=signal,
        stream_fn=stream_fn,
    )
    return await loop.run_continue()

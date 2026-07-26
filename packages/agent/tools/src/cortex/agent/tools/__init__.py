"""Tools module.

Mechanical port of hoocode's ``packages/agent/src/tools.ts``.

This module provides tool execution utilities for the agent runtime,
including tool preparation, execution, and result handling.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cortex.agent.types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentTool,
    AgentToolResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    ToolExecutionMode,
)
from cortex.ai.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
)

from .default_tools import DefaultToolsOptions, get_default_tools

__all__ = [
    "DefaultToolsOptions",
    "ToolExecutor",
    "execute_tool_calls",
    "get_default_tools",
    "prepare_tool_calls",
    "validate_tool_arguments",
]


# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------


@dataclass
class ToolExecutor:
    """Executes tool calls from assistant messages."""

    tools: list[AgentTool[Any, Any]] = field(default_factory=list)
    before_tool_call: Callable[..., Any] | None = None
    after_tool_call: Callable[..., Any] | None = None
    execution_mode: ToolExecutionMode = "parallel"

    def get_tool(self, name: str) -> AgentTool[Any, Any] | None:
        """Get a tool by name."""
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    async def prepare_tool_calls(
        self,
        message: AssistantMessage,
        context: AgentContext,
    ) -> list[dict[str, Any]]:
        """Prepare tool calls from an assistant message.

        Args:
            message: The assistant message containing tool calls.
            context: Current agent context.

        Returns:
            List of prepared tool call info dictionaries.
        """
        prepared = []

        for content in message.content:
            if not isinstance(content, ToolCall):
                continue

            tool = self.get_tool(content.name)
            if tool is None:
                continue

            # Validate arguments
            args = await self._validate_arguments(tool, content.arguments)

            prepared.append(
                {
                    "tool_call": content,
                    "tool": tool,
                    "args": args,
                    "context": context,
                }
            )

        return prepared

    async def execute_tool_calls(
        self,
        message: AssistantMessage,
        context: AgentContext,
        signal: Any = None,
    ) -> dict[str, Any]:
        """Execute tool calls from an assistant message.

        Args:
            message: The assistant message containing tool calls.
            context: Current agent context.
            signal: Optional abort signal.

        Returns:
            Dictionary with 'messages' and 'terminate' keys.
        """
        prepared = await self.prepare_tool_calls(message, context)
        messages: list[ToolResultMessage] = []
        terminate = False

        for call_info in prepared:
            tool_call = call_info["tool_call"]
            tool = call_info["tool"]
            args = call_info["args"]

            # Check if we should block this tool call
            if self.before_tool_call:
                block_result = await self._run_before_tool_call(
                    tool_call, tool, args, context, signal
                )
                if block_result and block_result.block:
                    # Tool is blocked, create error result
                    result = AgentToolResult(
                        content=[TextContent(text=block_result.reason or "Tool call blocked")],
                        details=None,
                    )
                else:
                    # Execute the tool
                    result = await self._execute_single_tool(tool, tool_call.id, args, signal)
            else:
                # Execute the tool directly
                result = await self._execute_single_tool(tool, tool_call.id, args, signal)

            # Run after_tool_call hook
            if self.after_tool_call:
                override = await self._run_after_tool_call(
                    tool_call, tool, args, result, context, signal
                )
                if override:
                    result = self._apply_override(result, override)

            # Check for early termination
            if result.terminate:
                terminate = True

            # Create tool result message
            tool_result = ToolResultMessage(
                role="toolResult",
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                content=result.content,
                details=result.details,
                is_error=False,
                timestamp=0,
            )  # type: ignore[call-arg]
            messages.append(tool_result)

        return {"messages": messages, "terminate": terminate}

    async def _validate_arguments(
        self,
        tool: AgentTool[Any, Any],
        arguments: str | dict[str, Any],
    ) -> Any:
        """Validate tool arguments."""
        if isinstance(arguments, str):
            import json

            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return {}

        if tool.prepare_arguments:
            return tool.prepare_arguments(arguments)

        return arguments

    async def _execute_single_tool(
        self,
        tool: AgentTool[Any, Any],
        tool_call_id: str,
        args: Any,
        signal: Any = None,
    ) -> AgentToolResult[Any]:
        """Execute a single tool."""
        if tool.execute is None:
            return AgentToolResult(
                content=[TextContent(text="Tool not implemented")],
                details=None,
            )

        try:
            return await tool.execute(tool_call_id, args, signal)
        except Exception as e:
            return AgentToolResult(
                content=[TextContent(text=f"Error: {str(e)}")],
                details={"error": str(e)},
            )

    async def _run_before_tool_call(
        self,
        tool_call: ToolCall,
        tool: AgentTool[Any, Any],
        args: Any,
        context: AgentContext,
        signal: Any = None,
    ) -> BeforeToolCallResult | None:
        """Run before_tool_call hook."""
        if self.before_tool_call is None:
            return None

        hook_context = BeforeToolCallContext(
            assistant_message=AssistantMessage(
                role="assistant",
                content=[],
                timestamp=0,
                api="",
                provider="",
                model="",
                usage={  # type: ignore[arg-type]
                    "input": 0,
                    "output": 0,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "totalTokens": 0,
                    "cache_read": 0,
                    "cache_write": 0,
                    "total_tokens": 0,
                    "cost": {
                        "input": 0,
                        "output": 0,
                        "cacheRead": 0,
                        "cacheWrite": 0,
                        "total": 0,
                    },
                },
                stop_reason="stop",
            ),  # type: ignore[call-arg]
            tool_call=tool_call,  # type: ignore[arg-type]
            args=args,
            context=context,
        )

        return await self.before_tool_call(hook_context, signal)

    async def _run_after_tool_call(
        self,
        tool_call: ToolCall,
        tool: AgentTool[Any, Any],
        args: Any,
        result: AgentToolResult[Any],
        context: AgentContext,
        signal: Any = None,
    ) -> AfterToolCallResult | None:
        """Run after_tool_call hook."""
        if self.after_tool_call is None:
            return None

        hook_context = AfterToolCallContext(
            assistant_message=AssistantMessage(
                role="assistant",
                content=[],
                timestamp=0,
                api="",
                provider="",
                model="",
                usage={  # type: ignore[arg-type]
                    "input": 0,
                    "output": 0,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "totalTokens": 0,
                    "cache_read": 0,
                    "cache_write": 0,
                    "total_tokens": 0,
                    "cost": {
                        "input": 0,
                        "output": 0,
                        "cacheRead": 0,
                        "cacheWrite": 0,
                        "total": 0,
                    },
                },
                stop_reason="stop",
            ),  # type: ignore[call-arg]
            tool_call=tool_call,  # type: ignore[arg-type]
            args=args,
            result=result,
            is_error=False,
            context=context,
        )

        return await self.after_tool_call(hook_context, signal)

    def _apply_override(
        self,
        result: AgentToolResult[Any],
        override: AfterToolCallResult,
    ) -> AgentToolResult[Any]:
        """Apply override from after_tool_call hook."""
        return AgentToolResult(
            content=override.content if override.content is not None else result.content,
            details=override.details if override.details is not None else result.details,
            terminate=override.terminate if override.terminate is not None else result.terminate,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def prepare_tool_calls(
    message: AssistantMessage,
    context: AgentContext,
    tools: list[AgentTool[Any, Any]],
) -> list[dict[str, Any]]:
    """Prepare tool calls from an assistant message.

    Args:
        message: The assistant message containing tool calls.
        context: Current agent context.
        tools: Available tools.

    Returns:
        List of prepared tool call info dictionaries.
    """
    executor = ToolExecutor(tools=tools)
    return await executor.prepare_tool_calls(message, context)


async def execute_tool_calls(
    message: AssistantMessage,
    context: AgentContext,
    tools: list[AgentTool[Any, Any]],
    signal: Any = None,
    before_tool_call: Callable[..., Any] | None = None,
    after_tool_call: Callable[..., Any] | None = None,
    execution_mode: ToolExecutionMode = "parallel",
) -> dict[str, Any]:
    """Execute tool calls from an assistant message.

    Args:
        message: The assistant message containing tool calls.
        context: Current agent context.
        tools: Available tools.
        signal: Optional abort signal.
        before_tool_call: Optional hook before tool execution.
        after_tool_call: Optional hook after tool execution.
        execution_mode: Tool execution mode.

    Returns:
        Dictionary with 'messages' and 'terminate' keys.
    """
    executor = ToolExecutor(
        tools=tools,
        before_tool_call=before_tool_call,
        after_tool_call=after_tool_call,
        execution_mode=execution_mode,
    )
    return await executor.execute_tool_calls(message, context, signal)


def validate_tool_arguments(
    tool: AgentTool[Any, Any],
    arguments: str | dict[str, Any],
) -> Any:
    """Validate tool arguments against the tool's schema.

    Args:
        tool: The tool to validate against.
        arguments: The arguments to validate.

    Returns:
        Validated arguments.
    """
    if isinstance(arguments, str):
        import json

        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return {}

    if tool.prepare_arguments:
        return tool.prepare_arguments(arguments)

    return arguments

"""Tests for tools module.

Mechanical port of hoocode's tools tests (if any).
"""

from __future__ import annotations

from typing import Any

from cortex.agent.tools import ToolExecutor, execute_tool_calls, prepare_tool_calls
from cortex.agent.types import AgentContext, AgentTool, AgentToolResult
from cortex.ai.types import AssistantMessage, TextContent, ToolCall

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def create_tool_call(
    tool_call_id: str, name: str, arguments: dict[str, Any] | None = None
) -> ToolCall:
    """Create a ToolCall for testing."""
    return ToolCall(
        id=tool_call_id,
        name=name,
        arguments=arguments or {},
    )


def create_assistant_message(tool_calls: list[ToolCall]) -> AssistantMessage:
    """Create an AssistantMessage with tool calls."""
    return AssistantMessage(
        role="assistant",
        content=tool_calls,  # type: ignore[arg-type]
        timestamp=0,
        api="test",
        provider="test",
        model="test",
        usage={  # type: ignore[arg-type]
            "input": 0,
            "output": 0,
            "cacheRead": 0,
            "cacheWrite": 0,
            "totalTokens": 0,
            "cache_read": 0,
            "cache_write": 0,
            "total_tokens": 0,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
        },
        stop_reason="stop",
    )


def create_test_tool(
    name: str = "test_tool",
    execute_fn: Any = None,
) -> AgentTool[Any, Any]:
    """Create a test AgentTool."""

    async def default_execute(
        tool_call_id: str, params: Any, signal: Any = None
    ) -> AgentToolResult[Any]:
        return AgentToolResult(
            content=[TextContent(text=f"Executed {name}")],
            details={"tool": name},
        )

    return AgentTool(
        name=name,
        description=f"Test tool: {name}",
        parameters={},
        label=name,
        execute=execute_fn or default_execute,
    )


# ---------------------------------------------------------------------------
# ToolExecutor tests
# ---------------------------------------------------------------------------


class TestToolExecutor:
    def test_executor_creation(self) -> None:
        """ToolExecutor can be created."""
        executor = ToolExecutor()
        assert executor.tools == []
        assert executor.execution_mode == "parallel"

    def test_executor_get_tool(self) -> None:
        """ToolExecutor.get_tool finds tools by name."""
        tool = create_test_tool("my_tool")
        executor = ToolExecutor(tools=[tool])

        found = executor.get_tool("my_tool")
        assert found is not None
        assert found.name == "my_tool"

        not_found = executor.get_tool("nonexistent")
        assert not_found is None

    async def test_executor_prepare_tool_calls(self) -> None:
        """ToolExecutor.prepare_tool_calls prepares tool calls."""
        tool = create_test_tool("test_tool")
        executor = ToolExecutor(tools=[tool])

        tool_call = create_tool_call("call-1", "test_tool", {"arg": "value"})
        message = create_assistant_message([tool_call])
        context = AgentContext(system_prompt="test", messages=[])

        prepared = await executor.prepare_tool_calls(message, context)
        assert len(prepared) == 1
        assert prepared[0]["tool"].name == "test_tool"
        assert prepared[0]["args"] == {"arg": "value"}

    async def test_executor_execute_tool_calls(self) -> None:
        """ToolExecutor.execute_tool_calls executes tools."""
        tool = create_test_tool("test_tool")
        executor = ToolExecutor(tools=[tool])

        tool_call = create_tool_call("call-1", "test_tool")
        message = create_assistant_message([tool_call])
        context = AgentContext(system_prompt="test", messages=[])

        result = await executor.execute_tool_calls(message, context)
        assert len(result["messages"]) == 1
        assert result["messages"][0].tool_call_id == "call-1"
        assert result["terminate"] is False

    async def test_executor_execute_unknown_tool(self) -> None:
        """ToolExecutor handles unknown tools gracefully."""
        executor = ToolExecutor(tools=[])

        tool_call = create_tool_call("call-1", "unknown_tool")
        message = create_assistant_message([tool_call])
        context = AgentContext(system_prompt="test", messages=[])

        result = await executor.execute_tool_calls(message, context)
        assert len(result["messages"]) == 0


# ---------------------------------------------------------------------------
# Public API tests
# ---------------------------------------------------------------------------


class TestPublicAPI:
    async def test_prepare_tool_calls(self) -> None:
        """prepare_tool_calls works correctly."""
        tool = create_test_tool("test_tool")
        tool_call = create_tool_call("call-1", "test_tool")
        message = create_assistant_message([tool_call])
        context = AgentContext(system_prompt="test", messages=[])

        prepared = await prepare_tool_calls(message, context, [tool])
        assert len(prepared) == 1

    async def test_execute_tool_calls(self) -> None:
        """execute_tool_calls works correctly."""
        tool = create_test_tool("test_tool")
        tool_call = create_tool_call("call-1", "test_tool")
        message = create_assistant_message([tool_call])
        context = AgentContext(system_prompt="test", messages=[])

        result = await execute_tool_calls(message, context, [tool])
        assert len(result["messages"]) == 1
        assert result["terminate"] is False

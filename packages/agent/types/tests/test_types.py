"""Tests for agent types module.

Mechanical port of hoocode's agent types tests (if any).
"""

from __future__ import annotations

from cortex.agent.types import (
    AfterToolCallResult,
    AgentContext,
    AgentState,
    AgentTool,
    AgentToolResult,
    BackgroundToolResult,
    BeforeToolCallResult,
    PrepareNextTurnContext,
    ShouldStopAfterTurnContext,
    ThinkingLevel,
    ToolExecutionMode,
)

# ---------------------------------------------------------------------------
# Type alias tests
# ---------------------------------------------------------------------------


class TestTypeAliases:
    def test_thinking_level_literal(self) -> None:
        levels: list[ThinkingLevel] = [
            "off",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
        ]
        assert len(levels) == 6

    def test_tool_execution_mode_literal(self) -> None:
        modes: list[ToolExecutionMode] = ["sequential", "parallel"]
        assert len(modes) == 2


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_before_tool_call_result(self) -> None:
        result = BeforeToolCallResult(block=True, reason="blocked")
        assert result.block is True
        assert result.reason == "blocked"

    def test_before_tool_call_result_defaults(self) -> None:
        result = BeforeToolCallResult()
        assert result.block is False
        assert result.reason is None

    def test_after_tool_call_result(self) -> None:
        result = AfterToolCallResult(
            is_error=True,
            terminate=True,
        )
        assert result.is_error is True
        assert result.terminate is True
        assert result.content is None
        assert result.details is None

    def test_agent_tool_result(self) -> None:
        result = AgentToolResult(
            content=[],
            details={"key": "value"},
            terminate=False,
        )
        assert result.content == []
        assert result.details == {"key": "value"}
        assert result.terminate is False

    def test_agent_context(self) -> None:
        context = AgentContext(
            system_prompt="You are a helpful assistant.",
            messages=[],
        )
        assert context.system_prompt == "You are a helpful assistant."
        assert context.messages == []
        assert context.tools is None

    def test_agent_state(self) -> None:
        state = AgentState(
            system_prompt="test",
            thinking_level="medium",
        )
        assert state.system_prompt == "test"
        assert state.thinking_level == "medium"
        assert state.is_streaming is False
        assert state.tools == []
        assert state.messages == []

    def test_agent_tool(self) -> None:
        tool = AgentTool(
            name="test_tool",
            description="A test tool",
            label="Test",
        )
        assert tool.name == "test_tool"
        assert tool.description == "A test tool"
        assert tool.label == "Test"
        assert tool.execute is None
        assert tool.background is None


# ---------------------------------------------------------------------------
# Context tests
# ---------------------------------------------------------------------------


class TestContexts:
    def test_should_stop_after_turn_context(self) -> None:
        context = ShouldStopAfterTurnContext(
            message=None,  # type: ignore[arg-type]
            tool_results=[],
            context=AgentContext(system_prompt="test", messages=[]),
            new_messages=[],
        )
        assert context.message is None
        assert context.tool_results == []
        assert context.new_messages == []

    def test_prepare_next_turn_context(self) -> None:
        context = PrepareNextTurnContext(
            message=None,  # type: ignore[arg-type]
            tool_results=[],
            context=AgentContext(system_prompt="test", messages=[]),
            new_messages=[],
        )
        assert context.message is None

    def test_background_tool_result(self) -> None:
        result = BackgroundToolResult(
            tool_call=None,  # type: ignore[arg-type]
            result=AgentToolResult(content=[], details=None),
            is_error=False,
        )
        assert result.is_error is False

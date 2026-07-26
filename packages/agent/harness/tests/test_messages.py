# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOperatorIssue=false, reportUnnecessaryIsInstance=false, reportUnusedFunction=false
"""Tests for message types and transformers.

Mechanical port of hoocode's ``packages/agent/test/harness/messages.test.ts``.
"""

from __future__ import annotations

from cortex.agent.harness.messages import (
    BRANCH_SUMMARY_PREFIX,
    BRANCH_SUMMARY_SUFFIX,
    COMPACTION_SUMMARY_PREFIX,
    COMPACTION_SUMMARY_SUFFIX,
    BackgroundToolCall,
    BashExecutionMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    bash_execution_to_text,
    convert_to_llm,
    create_background_placeholder_text,
    create_branch_summary_message,
    create_compaction_summary_message,
    create_custom_message,
    describe_background_tool,
    summarize_args,
)


class TestBashExecutionToText:
    """Tests for bash_execution_to_text."""

    def test_basic_command(self) -> None:
        """Basic command with output."""
        msg = BashExecutionMessage(
            command="ls -la",
            output="file1.txt\nfile2.txt",
            exit_code=0,
        )
        result = bash_execution_to_text(msg)
        assert "Ran `ls -la`" in result
        assert "file1.txt" in result
        assert "file2.txt" in result

    def test_command_with_no_output(self) -> None:
        """Command with no output."""
        msg = BashExecutionMessage(
            command="echo test",
            output="",
            exit_code=0,
        )
        result = bash_execution_to_text(msg)
        assert "(no output)" in result

    def test_command_with_nonzero_exit_code(self) -> None:
        """Command with non-zero exit code."""
        msg = BashExecutionMessage(
            command="false",
            exit_code=1,
        )
        result = bash_execution_to_text(msg)
        assert "Command exited with code 1" in result

    def test_cancelled_command(self) -> None:
        """Cancelled command."""
        msg = BashExecutionMessage(
            command="long_running",
            cancelled=True,
        )
        result = bash_execution_to_text(msg)
        assert "(command cancelled)" in result

    def test_truncated_output(self) -> None:
        """Truncated output with full path."""
        msg = BashExecutionMessage(
            command="cat large_file",
            output="partial output",
            truncated=True,
            full_output_path="/tmp/full_output.txt",
        )
        result = bash_execution_to_text(msg)
        assert "[Output truncated. Full output: /tmp/full_output.txt]" in result


class TestSummarizeArgs:
    """Tests for summarize_args."""

    def test_empty_args(self) -> None:
        """Empty args returns None."""
        result = summarize_args({})
        assert result is None

    def test_string_args(self) -> None:
        """String args are summarized."""
        result = summarize_args({"key": "value"})
        assert result == "key: value"

    def test_multiple_args(self) -> None:
        """Multiple args are summarized."""
        result = summarize_args({"a": "1", "b": "2", "c": "3"})
        assert "a: 1" in result
        assert "b: 2" in result
        assert "c: 3" in result

    def test_max_three_args(self) -> None:
        """Only first three args are summarized."""
        result = summarize_args({"a": "1", "b": "2", "c": "3", "d": "4"})
        assert "d" not in result

    def test_none_and_empty_values_skipped(self) -> None:
        """None and empty values are skipped."""
        result = summarize_args({"a": "1", "b": None, "c": ""})
        assert result == "a: 1"


class TestDescribeBackgroundTool:
    """Tests for describe_background_tool."""

    def test_mcp_tool(self) -> None:
        """MCP tool is described correctly."""
        tool_call = BackgroundToolCall(
            name="mcp_server_tool",
            arguments={"param": "value"},
        )
        info = describe_background_tool(tool_call)
        assert info.is_mcp_tool is True
        assert "MCP tool" in info.label
        assert "server_tool" in info.label

    def test_subagent_task(self) -> None:
        """Subagent Task call is described correctly."""
        tool_call = BackgroundToolCall(
            name="Task",
            arguments={"subagent_type": "explore", "description": "Test task"},
        )
        info = describe_background_tool(tool_call)
        assert info.is_mcp_tool is False
        assert info.subagent_type == "explore"
        assert "subagent" in info.label

    def test_tool_with_description(self) -> None:
        """Tool with description uses description as summary."""
        tool_call = BackgroundToolCall(
            name="Task",
            arguments={"description": "A test task"},
        )
        info = describe_background_tool(tool_call)
        assert info.summary == "A test task"

    def test_tool_with_prompt(self) -> None:
        """Tool with prompt uses first line of prompt as summary."""
        tool_call = BackgroundToolCall(
            name="Task",
            arguments={"prompt": "First line\nSecond line"},
        )
        info = describe_background_tool(tool_call)
        assert info.summary == "First line"


class TestCreateBackgroundPlaceholderText:
    """Tests for create_background_placeholder_text."""

    def test_mcp_tool_placeholder(self) -> None:
        """MCP tool placeholder includes 'Started'."""
        tool_call = BackgroundToolCall(
            name="mcp_server_tool",
            arguments={},
        )
        result = create_background_placeholder_text(tool_call)
        assert "Started" in result
        assert "MCP tool" in result

    def test_subagent_placeholder(self) -> None:
        """Subagent placeholder includes 'Delegated'."""
        tool_call = BackgroundToolCall(
            name="Task",
            arguments={"subagent_type": "explore"},
        )
        result = create_background_placeholder_text(tool_call)
        assert "Delegated" in result
        assert "subagent" in result


class TestCreateBranchSummaryMessage:
    """Tests for create_branch_summary_message."""

    def test_creates_message(self) -> None:
        """Creates a branch summary message."""
        msg = create_branch_summary_message(
            summary="Test summary",
            from_id="entry-123",
            timestamp="2024-01-01T00:00:00Z",
        )
        assert msg.role == "branchSummary"
        assert msg.summary == "Test summary"
        assert msg.from_id == "entry-123"
        assert msg.timestamp > 0


class TestCreateCompactionSummaryMessage:
    """Tests for create_compaction_summary_message."""

    def test_creates_message(self) -> None:
        """Creates a compaction summary message."""
        msg = create_compaction_summary_message(
            summary="Compacted summary",
            tokens_before=1000,
            timestamp="2024-01-01T00:00:00Z",
            tokens_after=500,
        )
        assert msg.role == "compactionSummary"
        assert msg.summary == "Compacted summary"
        assert msg.tokens_before == 1000
        assert msg.tokens_after == 500

    def test_creates_message_without_tokens_after(self) -> None:
        """Creates a compaction summary message without tokens_after."""
        msg = create_compaction_summary_message(
            summary="Compacted summary",
            tokens_before=1000,
            timestamp="2024-01-01T00:00:00Z",
        )
        assert msg.tokens_after is None


class TestCreateCustomMessage:
    """Tests for create_custom_message."""

    def test_creates_message(self) -> None:
        """Creates a custom message."""
        msg = create_custom_message(
            custom_type="test",
            content="Test content",
            display=True,
            details={"key": "value"},
            timestamp="2024-01-01T00:00:00Z",
        )
        assert msg.role == "custom"
        assert msg.custom_type == "test"
        assert msg.content == "Test content"
        assert msg.display is True
        assert msg.details == {"key": "value"}


class TestConvertToLlm:
    """Tests for convert_to_llm."""

    def test_bash_execution_message(self) -> None:
        """Bash execution messages are converted to user messages."""
        msg = BashExecutionMessage(
            command="ls",
            output="file.txt",
            exit_code=0,
            timestamp=1234567890,
        )
        result = convert_to_llm([msg])
        assert len(result) == 1
        assert result[0].role == "user"

    def test_bash_execution_excluded_from_context(self) -> None:
        """Bash execution messages excluded from context are skipped."""
        msg = BashExecutionMessage(
            command="ls",
            exclude_from_context=True,
        )
        result = convert_to_llm([msg])
        assert len(result) == 0

    def test_custom_message(self) -> None:
        """Custom messages are converted to user messages."""
        msg = CustomMessage(
            custom_type="test",
            content="Test content",
            timestamp=1234567890,
        )
        result = convert_to_llm([msg])
        assert len(result) == 1
        assert result[0].role == "user"

    def test_branch_summary_message(self) -> None:
        """Branch summary messages are wrapped in summary tags."""
        msg = BranchSummaryMessage(
            summary="Test summary",
            timestamp=1234567890,
        )
        result = convert_to_llm([msg])
        assert len(result) == 1
        assert BRANCH_SUMMARY_PREFIX in result[0].content[0].text
        assert "Test summary" in result[0].content[0].text
        assert BRANCH_SUMMARY_SUFFIX in result[0].content[0].text

    def test_compaction_summary_message(self) -> None:
        """Compaction summary messages are wrapped in summary tags."""
        msg = CompactionSummaryMessage(
            summary="Compacted summary",
            timestamp=1234567890,
        )
        result = convert_to_llm([msg])
        assert len(result) == 1
        assert COMPACTION_SUMMARY_PREFIX in result[0].content[0].text
        assert "Compacted summary" in result[0].content[0].text
        assert COMPACTION_SUMMARY_SUFFIX in result[0].content[0].text

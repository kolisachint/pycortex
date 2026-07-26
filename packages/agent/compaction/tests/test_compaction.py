# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false
"""Tests for the compaction module."""

from __future__ import annotations

from cortex.agent.compaction import (
    CompactionDetails,
    CompactionResult,
    CompactionSettings,
    FileOperations,
    collect_messages_for_compaction,
    compute_file_lists,
    create_file_ops,
    estimate_message_tokens,
    estimate_tokens,
    extract_file_ops_from_message,
    format_file_operations,
    generate_summary_prompt,
    should_compact,
)
from cortex.ai.types import AssistantMessage, TextContent, Usage, UserMessage


def _create_user_message(content: str = "Hello") -> UserMessage:
    """Create a user message for testing."""
    return UserMessage(role="user", content=[TextContent(text=content)], timestamp=1704067200000)


def _create_assistant_message(
    content: str = "Hi there",
    provider: str = "test-provider",
    model: str = "test-model",
) -> AssistantMessage:
    """Create an assistant message for testing."""
    return AssistantMessage(
        role="assistant",
        content=[TextContent(text=content)],
        api="test-api",
        provider=provider,
        model=model,
        usage=Usage(
            input=10,
            output=5,
            cache_read=0,
            cache_write=0,
            total_tokens=15,
            cost={
                "input": 0.001,
                "output": 0.002,
                "cache_read": 0.0,
                "cache_write": 0.0,
                "total": 0.003,
            },
        ),
        stop_reason="stop",
        timestamp=1704067201000,
    )


class TestEstimateTokens:
    """Tests for token estimation functions."""

    def test_estimate_tokens_empty(self) -> None:
        """Test token estimation for empty string."""
        assert estimate_tokens("") == 0

    def test_estimate_tokens_short(self) -> None:
        """Test token estimation for short text."""
        # "Hello" = 5 chars, ceil(5/4) = 2
        assert estimate_tokens("Hello") == 2

    def test_estimate_tokens_longer(self) -> None:
        """Test token estimation for longer text."""
        # "Hello, world!" = 13 chars, ceil(13/4) = 4
        assert estimate_tokens("Hello, world!") == 4

    def test_estimate_message_tokens_user(self) -> None:
        """Test token estimation for user message."""
        msg = _create_user_message("Hello")
        # "Hello" = 5 chars, ceil(5/4) = 2
        assert estimate_message_tokens(msg) == 2

    def test_estimate_message_tokens_assistant(self) -> None:
        """Test token estimation for assistant message."""
        msg = _create_assistant_message("Hi there")
        # "Hi there" = 8 chars, ceil(8/4) = 2
        assert estimate_message_tokens(msg) == 2


class TestFileOperations:
    """Tests for file operation tracking."""

    def test_create_file_ops(self) -> None:
        """Test creating FileOperations."""
        ops = create_file_ops()
        assert ops.read == set()
        assert ops.written == set()
        assert ops.edited == set()

    def test_extract_file_ops_read(self) -> None:
        """Test extracting read file operations."""
        from cortex.ai.types import ToolCall

        msg = AssistantMessage(
            role="assistant",
            content=[
                ToolCall(
                    name="read",
                    arguments={"path": "/path/to/file.txt"},
                    id="call-1",
                )
            ],
            api="test-api",
            provider="test-provider",
            model="test-model",
            usage=Usage(
                input=10,
                output=5,
                cache_read=0,
                cache_write=0,
                total_tokens=15,
                cost={
                    "input": 0.001,
                    "output": 0.002,
                    "cache_read": 0.0,
                    "cache_write": 0.0,
                    "total": 0.003,
                },
            ),
            stop_reason="stop",
            timestamp=1704067200000,
        )
        ops = create_file_ops()
        extract_file_ops_from_message(msg, ops)
        assert "/path/to/file.txt" in ops.read

    def test_extract_file_ops_write(self) -> None:
        """Test extracting write file operations."""
        from cortex.ai.types import ToolCall

        msg = AssistantMessage(
            role="assistant",
            content=[
                ToolCall(
                    name="write",
                    arguments={"path": "/path/to/output.txt"},
                    id="call-1",
                )
            ],
            api="test-api",
            provider="test-provider",
            model="test-model",
            usage=Usage(
                input=10,
                output=5,
                cache_read=0,
                cache_write=0,
                total_tokens=15,
                cost={
                    "input": 0.001,
                    "output": 0.002,
                    "cache_read": 0.0,
                    "cache_write": 0.0,
                    "total": 0.003,
                },
            ),
            stop_reason="stop",
            timestamp=1704067200000,
        )
        ops = create_file_ops()
        extract_file_ops_from_message(msg, ops)
        assert "/path/to/output.txt" in ops.written

    def test_extract_file_ops_edit(self) -> None:
        """Test extracting edit file operations."""
        from cortex.ai.types import ToolCall

        msg = AssistantMessage(
            role="assistant",
            content=[
                ToolCall(
                    name="edit",
                    arguments={"path": "/path/to/edited.txt"},
                    id="call-1",
                )
            ],
            api="test-api",
            provider="test-provider",
            model="test-model",
            usage=Usage(
                input=10,
                output=5,
                cache_read=0,
                cache_write=0,
                total_tokens=15,
                cost={
                    "input": 0.001,
                    "output": 0.002,
                    "cache_read": 0.0,
                    "cache_write": 0.0,
                    "total": 0.003,
                },
            ),
            stop_reason="stop",
            timestamp=1704067200000,
        )
        ops = create_file_ops()
        extract_file_ops_from_message(msg, ops)
        assert "/path/to/edited.txt" in ops.edited

    def test_compute_file_lists(self) -> None:
        """Test computing file lists from operations."""
        ops = FileOperations(
            read={"/read1.txt", "/read2.txt", "/modified.txt"},
            written={"/modified.txt"},
            edited={"/edited.txt"},
        )
        read_files, modified_files = compute_file_lists(ops)
        assert read_files == ["/read1.txt", "/read2.txt"]
        assert modified_files == ["/edited.txt", "/modified.txt"]

    def test_format_file_operations_empty(self) -> None:
        """Test formatting empty file operations."""
        result = format_file_operations([], [])
        assert result == ""

    def test_format_file_operations_read_only(self) -> None:
        """Test formatting read-only file operations."""
        result = format_file_operations(["/file1.txt", "/file2.txt"], [])
        assert "<read-files>" in result
        assert "/file1.txt" in result
        assert "/file2.txt" in result
        assert "<modified-files>" not in result

    def test_format_file_operations_modified_only(self) -> None:
        """Test formatting modified-only file operations."""
        result = format_file_operations([], ["/output.txt"])
        assert "<modified-files>" in result
        assert "/output.txt" in result
        assert "<read-files>" not in result

    def test_format_file_operations_both(self) -> None:
        """Test formatting both read and modified file operations."""
        result = format_file_operations(["/input.txt"], ["/output.txt"])
        assert "<read-files>" in result
        assert "<modified-files>" in result


class TestCompactionSettings:
    """Tests for CompactionSettings."""

    def test_default_settings(self) -> None:
        """Test default compaction settings."""
        settings = CompactionSettings()
        assert settings.reserve_tokens == 16384
        assert settings.max_messages == 100
        assert settings.min_messages_to_compact == 10

    def test_custom_settings(self) -> None:
        """Test custom compaction settings."""
        settings = CompactionSettings(
            reserve_tokens=8192,
            max_messages=50,
            min_messages_to_compact=5,
        )
        assert settings.reserve_tokens == 8192
        assert settings.max_messages == 50
        assert settings.min_messages_to_compact == 5


class TestCollectMessagesForCompaction:
    """Tests for collecting messages for compaction."""

    def test_empty_messages(self) -> None:
        """Test collecting from empty messages."""
        compact, keep = collect_messages_for_compaction([], 1000)
        assert compact == []
        assert keep == []

    def test_messages_within_budget(self) -> None:
        """Test messages within token budget."""
        messages = [_create_user_message("Hi"), _create_assistant_message("Hello")]
        compact, keep = collect_messages_for_compaction(messages, 1000)
        assert compact == []
        assert keep == messages

    def test_messages_exceed_budget(self) -> None:
        """Test messages exceeding token budget."""
        messages = [_create_user_message(f"Message {i}") for i in range(20)]
        compact, keep = collect_messages_for_compaction(messages, 20)
        assert len(compact) > 0
        assert len(keep) > 0
        assert len(compact) + len(keep) == len(messages)

    def test_messages_zero_budget(self) -> None:
        """Test with zero token budget (keep all)."""
        messages = [_create_user_message("Hi")]
        compact, keep = collect_messages_for_compaction(messages, 0)
        assert compact == []
        assert keep == messages


class TestShouldCompact:
    """Tests for checking if compaction should be triggered."""

    def test_few_messages(self) -> None:
        """Test with few messages (no compaction)."""
        messages = [_create_user_message("Hi")]
        settings = CompactionSettings(min_messages_to_compact=10)
        assert should_compact(messages, settings) is False

    def test_many_messages(self) -> None:
        """Test with many messages (compaction needed)."""
        messages = [_create_user_message(f"Message {i}") for i in range(20)]
        settings = CompactionSettings(min_messages_to_compact=10, reserve_tokens=50)
        assert should_compact(messages, settings) is True

    def test_exact_threshold(self) -> None:
        """Test at exact message threshold."""
        messages = [_create_user_message("Hi") for _ in range(10)]
        settings = CompactionSettings(min_messages_to_compact=10, reserve_tokens=10000)
        assert should_compact(messages, settings) is False


class TestCompactionResult:
    """Tests for CompactionResult."""

    def test_compaction_result_creation(self) -> None:
        """Test creating CompactionResult."""
        result = CompactionResult(
            summary="Test summary",
            first_kept_entry_id="entry-1",
            tokens_before=1000,
            tokens_after=500,
        )
        assert result.summary == "Test summary"
        assert result.first_kept_entry_id == "entry-1"
        assert result.tokens_before == 1000
        assert result.tokens_after == 500
        assert result.details is None

    def test_compaction_result_with_details(self) -> None:
        """Test creating CompactionResult with details."""
        details = CompactionDetails(read_files=["/read.txt"], modified_files=["/write.txt"])
        result = CompactionResult(
            summary="Test summary",
            first_kept_entry_id="entry-1",
            tokens_before=1000,
            tokens_after=500,
            details=details,
        )
        assert result.details is not None
        assert result.details.read_files == ["/read.txt"]
        assert result.details.modified_files == ["/write.txt"]


class TestGenerateSummaryPrompt:
    """Tests for generating summary prompts."""

    def test_basic_prompt(self) -> None:
        """Test generating a basic summary prompt."""
        messages = [_create_user_message("Hello"), _create_assistant_message("Hi")]
        prompt = generate_summary_prompt(messages)
        assert "<conversation>" in prompt
        assert "</conversation>" in prompt
        assert "[User]: Hello" in prompt
        assert "[Assistant]: Hi" in prompt

    def test_prompt_with_previous_summary(self) -> None:
        """Test generating prompt with previous summary."""
        messages = [_create_user_message("Hello")]
        prompt = generate_summary_prompt(messages, previous_summary="Old summary")
        assert "Previous summary to update:" in prompt
        assert "Old summary" in prompt

    def test_prompt_with_custom_instructions(self) -> None:
        """Test generating prompt with custom instructions."""
        messages = [_create_user_message("Hello")]
        prompt = generate_summary_prompt(messages, custom_instructions="Focus on files")
        assert "Additional instructions:" in prompt
        assert "Focus on files" in prompt

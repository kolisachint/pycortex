# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false
"""Tests for the compaction module.

Mechanical port of hoocode's ``packages/agent/test/harness/compaction.test.ts``.
"""

from __future__ import annotations

from typing import Any

import pytest
from cortex.agent.compaction import (
    DEFAULT_COMPACTION_SETTINGS,
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
    find_cut_point,
    format_file_operations,
    generate_summary_prompt,
    should_compact,
)
from cortex.agent.harness.types import (
    CompactionEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionTreeEntry,
    ThinkingLevelChangeEntry,
    build_session_context,
)
from cortex.ai.types import AssistantMessage, TextContent, ToolCall, Usage, UserMessage

# ============================================================================
# Helper functions
# ============================================================================

_next_id = 0


def _create_id() -> str:
    global _next_id
    _next_id += 1
    return f"entry-{_next_id}"


def _create_mock_usage(
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_write: int = 0,
) -> Usage:
    return Usage(
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        total_tokens=input_tokens + output_tokens + cache_read + cache_write,
        cost={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
    )


def _create_user_message(text: str = "Hello") -> UserMessage:
    """Create a user message for testing."""
    return UserMessage(role="user", content=[TextContent(text=text)], timestamp=1704067200000)


def _create_assistant_message(
    text: str = "Hi there",
    usage: Usage | None = None,
) -> AssistantMessage:
    """Create an assistant message for testing."""
    if usage is None:
        usage = _create_mock_usage(100, 50)

    return AssistantMessage(
        role="assistant",
        content=[TextContent(text=text)],
        api="anthropic-messages",
        provider="anthropic",
        model="claude-sonnet-4-5",
        usage=usage,
        stop_reason="stop",
        timestamp=1704067201000,
    )


def _create_message_entry(
    message: object,
    parent_id: str | None = None,
) -> MessageEntry:
    return MessageEntry(
        id=_create_id(),
        parent_id=parent_id,
        timestamp="2024-01-01T00:00:00.000Z",
        message=message,
    )


def _create_compaction_entry(
    summary: str,
    first_kept_entry_id: str,
    parent_id: str | None = None,
) -> CompactionEntry:
    return CompactionEntry(
        id=_create_id(),
        parent_id=parent_id,
        timestamp="2024-01-01T00:00:00.000Z",
        summary=summary,
        first_kept_entry_id=first_kept_entry_id,
        tokens_before=1234,
    )


def _create_thinking_level_entry(
    level: str,
    parent_id: str | None = None,
) -> ThinkingLevelChangeEntry:
    return ThinkingLevelChangeEntry(
        id=_create_id(),
        parent_id=parent_id,
        timestamp="2024-01-01T00:00:00.000Z",
        thinking_level=level,
    )


def _create_model_change_entry(
    provider: str,
    model_id: str,
    parent_id: str | None = None,
) -> ModelChangeEntry:
    return ModelChangeEntry(
        id=_create_id(),
        parent_id=parent_id,
        timestamp="2024-01-01T00:00:00.000Z",
        provider=provider,
        model_id=model_id,
    )


# ============================================================================
# Tests
# ============================================================================


class TestEstimateTokens:
    """Tests for token estimation functions."""

    def test_estimate_tokens_empty(self) -> None:
        """Test token estimation for empty string."""
        msg = _create_user_message("")
        assert estimate_tokens(msg) == 0

    def test_estimate_tokens_short(self) -> None:
        """Test token estimation for short text."""
        msg = _create_user_message("Hello")
        # "Hello" = 5 chars, ceil(5/4) = 2
        assert estimate_tokens(msg) == 2

    def test_estimate_tokens_longer(self) -> None:
        """Test token estimation for longer text."""
        msg = _create_user_message("Hello, world!")
        # "Hello, world!" = 13 chars, ceil(13/4) = 4
        assert estimate_tokens(msg) == 4

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
            usage=_create_mock_usage(10, 5),
            stop_reason="stop",
            timestamp=1704067200000,
        )
        ops = create_file_ops()
        extract_file_ops_from_message(msg, ops)
        assert "/path/to/file.txt" in ops.read

    def test_extract_file_ops_write(self) -> None:
        """Test extracting write file operations."""
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
            usage=_create_mock_usage(10, 5),
            stop_reason="stop",
            timestamp=1704067200000,
        )
        ops = create_file_ops()
        extract_file_ops_from_message(msg, ops)
        assert "/path/to/output.txt" in ops.written

    def test_extract_file_ops_edit(self) -> None:
        """Test extracting edit file operations."""
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
            usage=_create_mock_usage(10, 5),
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
        assert settings.keep_recent_tokens == 20000
        assert settings.enabled is True

    def test_custom_settings(self) -> None:
        """Test custom compaction settings."""
        settings = CompactionSettings(
            reserve_tokens=8192,
            keep_recent_tokens=10000,
        )
        assert settings.reserve_tokens == 8192
        assert settings.keep_recent_tokens == 10000


class TestShouldCompact:
    """Tests for checking if compaction should be triggered."""

    def test_few_messages(self) -> None:
        """Test with few messages (no compaction)."""
        settings = CompactionSettings(enabled=True, reserve_tokens=10000)
        assert should_compact(5000, 100000, settings) is False

    def test_many_messages(self) -> None:
        """Test with many messages (compaction needed)."""
        settings = CompactionSettings(enabled=True, reserve_tokens=10000)
        assert should_compact(95000, 100000, settings) is True

    def test_exact_threshold(self) -> None:
        """Test at exact message threshold."""
        settings = CompactionSettings(enabled=True, reserve_tokens=10000)
        # context_window - reserve_tokens = 90000
        assert should_compact(89000, 100000, settings) is False

    def test_disabled(self) -> None:
        """Test with compaction disabled."""
        settings = CompactionSettings(enabled=False, reserve_tokens=10000)
        assert should_compact(95000, 100000, settings) is False

    def test_soft_ratio_trigger(self) -> None:
        """Test that the soft ratio trigger applies before the reserve rule fires."""
        settings = CompactionSettings(enabled=True, reserve_tokens=10000, max_context_ratio=0.75)
        # Window 100000: reserve rule fires at 90000, ratio at 75000 → ratio wins
        assert should_compact(80000, 100000, settings) is True
        assert should_compact(70000, 100000, settings) is False
        # Unset ratio keeps the reserve-only behavior (89000 stays below 90000)
        settings_no_ratio = CompactionSettings(
            enabled=True, reserve_tokens=10000, max_context_ratio=None
        )
        assert should_compact(89000, 100000, settings_no_ratio) is False
        # Out-of-range ratios are ignored (reserve rule only)
        settings_bad_ratio = CompactionSettings(
            enabled=True, reserve_tokens=10000, max_context_ratio=1.5
        )
        assert should_compact(80000, 100000, settings_bad_ratio) is False


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


class TestContextTokens:
    """Tests for context token calculation."""

    def test_calculate_context_tokens(self) -> None:
        """Test calculating total context tokens from usage."""
        usage = _create_mock_usage(1000, 500, 200, 100)
        from cortex.agent.compaction import calculate_context_tokens

        assert calculate_context_tokens(usage) == 1800

    def test_calculate_context_tokens_empty(self) -> None:
        """Test calculating context tokens with zero usage."""
        usage = _create_mock_usage(0, 0, 0, 0)
        from cortex.agent.compaction import calculate_context_tokens

        assert calculate_context_tokens(usage) == 0


class TestCutPointDetection:
    """Tests for cut point detection."""

    def test_find_cut_point_basic(self) -> None:
        """Test finding a basic cut point."""
        entries: list[SessionTreeEntry] = []
        parent_id: str | None = None
        for i in range(10):
            user = _create_message_entry(_create_user_message(f"User {i}"), parent_id)
            entries.append(user)
            assistant = _create_message_entry(
                _create_assistant_message(
                    f"Assistant {i}", _create_mock_usage(0, 100, (i + 1) * 1000, 0)
                ),
                user.id,
            )
            entries.append(assistant)
            parent_id = assistant.id

        result = find_cut_point(entries, 0, len(entries), 2500)
        assert result.first_kept_entry_index >= 0
        entry = entries[result.first_kept_entry_index]
        assert getattr(entry, "type", "") == "message"


class TestSessionContext:
    """Tests for session context building."""

    def test_build_session_context_with_compaction(self) -> None:
        """Test building session context with a compaction entry."""
        u1 = _create_message_entry(_create_user_message("1"))
        a1 = _create_message_entry(_create_assistant_message("a"), u1.id)
        u2 = _create_message_entry(_create_user_message("2"), a1.id)
        a2 = _create_message_entry(_create_assistant_message("b"), u2.id)
        compaction = _create_compaction_entry("Summary of 1,a,2,b", u2.id, a2.id)
        u3 = _create_message_entry(_create_user_message("3"), compaction.id)
        a3 = _create_message_entry(_create_assistant_message("c"), u3.id)

        loaded = build_session_context([u1, a1, u2, a2, compaction, u3, a3])
        assert len(loaded.messages) == 5
        assert loaded.messages[0].role == "compactionSummary"

    def test_build_session_context_tracks_model_and_thinking(self) -> None:
        """Test that model and thinking level changes are tracked."""
        user = _create_message_entry(_create_user_message("1"))
        model_change = _create_model_change_entry("openai", "gpt-4", user.id)
        assistant = _create_message_entry(_create_assistant_message("a"), model_change.id)
        thinking_change = _create_thinking_level_entry("high", assistant.id)

        loaded = build_session_context([user, model_change, assistant, thinking_change])
        assert loaded.model == {"provider": "anthropic", "modelId": "claude-sonnet-4-5"}
        assert loaded.thinking_level == "high"


class TestPrepareCompaction:
    """Tests for compaction preparation."""

    def test_prepare_compaction_basic(self) -> None:
        """Test preparing compaction from entries."""
        u1 = _create_message_entry(_create_user_message("user msg 1"))
        a1 = _create_message_entry(_create_assistant_message("assistant msg 1"), u1.id)
        u2 = _create_message_entry(_create_user_message("user msg 2"), a1.id)
        a2 = _create_message_entry(
            _create_assistant_message("assistant msg 2", _create_mock_usage(5000, 1000)),
            u2.id,
        )
        u3 = _create_message_entry(_create_user_message("user msg 3"), a2.id)
        a3 = _create_message_entry(
            _create_assistant_message("assistant msg 3", _create_mock_usage(8000, 2000)),
            u3.id,
        )

        from cortex.agent.compaction import prepare_compaction

        preparation = prepare_compaction([u1, a1, u2, a2, u3, a3], DEFAULT_COMPACTION_SETTINGS)
        # Should return None or a preparation depending on token thresholds
        assert preparation is None or preparation.first_kept_entry_id is not None

    def test_prepare_compaction_with_previous_compaction(self) -> None:
        """Test preparing compaction when there's a previous compaction."""
        u1 = _create_message_entry(_create_user_message("user msg 1"))
        a1 = _create_message_entry(_create_assistant_message("assistant msg 1"), u1.id)
        u2 = _create_message_entry(_create_user_message("user msg 2"), a1.id)
        a2 = _create_message_entry(
            _create_assistant_message("assistant msg 2", _create_mock_usage(5000, 1000)),
            u2.id,
        )
        compaction1 = _create_compaction_entry("First summary", u2.id, a2.id)
        u3 = _create_message_entry(_create_user_message("user msg 3"), compaction1.id)
        a3 = _create_message_entry(
            _create_assistant_message("assistant msg 3", _create_mock_usage(8000, 2000)),
            u3.id,
        )

        from cortex.agent.compaction import prepare_compaction

        preparation = prepare_compaction(
            [u1, a1, u2, a2, compaction1, u3, a3],
            DEFAULT_COMPACTION_SETTINGS,
        )
        if preparation is not None:
            assert preparation.previous_summary == "First summary"
            assert preparation.first_kept_entry_id is not None

    def test_prepare_compaction_skips_when_last_is_compaction(self) -> None:
        """Test that prepare_compaction returns None when last entry is compaction."""
        u1 = _create_message_entry(_create_user_message("msg"))
        compaction = _create_compaction_entry("Summary", u1.id)

        from cortex.agent.compaction import prepare_compaction

        preparation = prepare_compaction([u1, compaction], DEFAULT_COMPACTION_SETTINGS)
        assert preparation is None


class TestSummarizationRequest:
    """What a summarization asks the provider for (7.12).

    Both call sites handed `complete_simple` dicts — a context keyed
    `systemPrompt` and options keyed `maxTokens`/`apiKey` — which every real
    provider reads as attributes. `/compact` could only ever have worked against
    a stand-in that accepts both shapes, and against Anthropic it raised.
    """

    async def test_the_request_is_built_from_real_types(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import cortex.ai.stream as stream_module
        from cortex.agent.compaction.compaction import generate_summary
        from cortex.ai.types import (
            AssistantMessage,
            Context,
            SimpleStreamOptions,
            TextContent,
            Usage,
            UserMessage,
        )

        seen: dict[str, object] = {}

        async def complete_simple(model: Any, context: Any, options: Any = None) -> Any:
            seen["context"] = context
            seen["options"] = options
            return AssistantMessage(
                content=[TextContent(text="## Goal\nported")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=Usage(
                    input=1,
                    output=1,
                    cache_read=0,
                    cache_write=0,
                    total_tokens=2,
                    cost={
                        "input": 0,
                        "output": 0,
                        "cacheRead": 0,
                        "cacheWrite": 0,
                        "total": 0,
                    },
                ),
                stop_reason="stop",
                timestamp=0,
            )

        monkeypatch.setattr(stream_module, "complete_simple", complete_simple)

        from cortex.ai.providers.faux import register_faux_provider

        registration = register_faux_provider()
        try:
            summary = await generate_summary(
                [UserMessage(content=[TextContent(text="do the thing")], timestamp=0)],
                registration.get_model(),
                reserve_tokens=1000,
                api_key="the-key",
                thinking_level="high",
            )
        finally:
            registration.unregister()

        assert summary.startswith("## Goal")

        context = seen["context"]
        assert isinstance(context, Context)
        assert context.system_prompt, "the summarization system prompt went missing"
        assert isinstance(context.messages[0], UserMessage)
        assert "do the thing" in context.messages[0].content[0].text

        options = seen["options"]
        assert isinstance(options, SimpleStreamOptions)
        assert options.api_key == "the-key"
        assert options.max_tokens == 800
        # The faux model does not advertise reasoning, so the TS's ternary drops
        # the level rather than asking a non-reasoning model to think.
        assert options.reasoning is None

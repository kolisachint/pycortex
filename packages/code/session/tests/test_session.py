# pyright: reportMissingParameterType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportUnknownLambdaType=false
"""Tests for code session management."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from cortex.code.session import (
    SessionManager,
    Skill,
    assert_session_cwd_exists,
    compute_session_stats,
    expand_skill_command,
    extract_user_message_text,
    format_missing_session_cwd_error,
    get_last_assistant_text,
    get_missing_session_cwd_issue,
    parse_skill_block,
)
from cortex.code.session.cwd import MissingSessionCwdError, SessionCwdIssue


class TestSessionCwd:
    """Tests for session cwd utilities."""

    def test_get_missing_session_cwd_issue_exists(self, tmp_path: Path) -> None:
        """No issue when cwd exists."""
        issue = get_missing_session_cwd_issue(
            session_cwd=str(tmp_path),
            session_file="test.jsonl",
            fallback_cwd="/other",
        )
        assert issue is None

    def test_get_missing_session_cwd_issue_missing(self) -> None:
        """Issue when cwd doesn't exist."""
        issue = get_missing_session_cwd_issue(
            session_cwd="/nonexistent/path",
            session_file="test.jsonl",
            fallback_cwd="/other",
        )
        assert issue is not None
        assert issue.session_cwd == "/nonexistent/path"
        assert issue.fallback_cwd == "/other"

    def test_get_missing_session_cwd_issue_no_session_file(self) -> None:
        """No issue when no session file."""
        issue = get_missing_session_cwd_issue(
            session_cwd="/nonexistent/path",
            session_file=None,
            fallback_cwd="/other",
        )
        assert issue is None

    def test_format_missing_session_cwd_error(self) -> None:
        """Test error message formatting."""
        issue = SessionCwdIssue(
            session_cwd="/missing/cwd",
            fallback_cwd="/current/cwd",
            session_file="/path/to/session.jsonl",
        )
        error_msg = format_missing_session_cwd_error(issue)
        assert "/missing/cwd" in error_msg
        assert "/current/cwd" in error_msg
        assert "/path/to/session.jsonl" in error_msg

    def test_assert_session_cwd_exists_passes(self, tmp_path: Path) -> None:
        """No error when cwd exists."""
        assert_session_cwd_exists(
            session_cwd=str(tmp_path),
            session_file="test.jsonl",
            fallback_cwd="/other",
        )

    def test_assert_session_cwd_exists_fails(self) -> None:
        """Error when cwd doesn't exist."""
        with pytest.raises(MissingSessionCwdError):
            assert_session_cwd_exists(
                session_cwd="/nonexistent/path",
                session_file="test.jsonl",
                fallback_cwd="/other",
            )


class TestSessionStats:
    """Tests for session statistics utilities."""

    def test_extract_user_message_text_string(self) -> None:
        """Extract text from string content."""
        result = extract_user_message_text("Hello, World!")
        assert result == "Hello, World!"

    def test_extract_user_message_text_array(self) -> None:
        """Extract text from array content."""
        content = [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "World!"},
        ]
        result = extract_user_message_text(content)
        assert result == "Hello World!"

    def test_extract_user_message_text_mixed(self) -> None:
        """Extract text from mixed content array."""
        content = [
            {"type": "text", "text": "Hello"},
            {"type": "image", "url": "image.png"},
            {"type": "text", "text": "World"},
        ]
        result = extract_user_message_text(content)
        assert result == "HelloWorld"

    def test_compute_session_stats(self) -> None:
        """Test session stats computation."""
        from dataclasses import dataclass, field

        @dataclass
        class MockUsage:
            input: int = 10
            output: int = 5
            cache_read: int = 2
            cache_write: int = 1
            cost: dict = field(default_factory=lambda: {"total": 0.01})

        @dataclass
        class MockContent:
            type: str = "text"
            text: str = ""

        @dataclass
        class MockMessage:
            role: str = "user"
            content: list = field(default_factory=list)
            usage: MockUsage = field(default_factory=MockUsage)
            stop_reason: str = "stop"

        messages = [
            MockMessage(role="user", content=[MockContent(type="text", text="Hello")]),
            MockMessage(
                role="assistant",
                content=[MockContent(type="text", text="Hi!")],
                usage=MockUsage(),
            ),
            MockMessage(role="toolResult", content=[MockContent(type="text", text="result")]),
        ]
        stats = compute_session_stats(
            messages=messages,
            session_file="test.jsonl",
            session_id="test-id",
        )
        assert stats.user_messages == 1
        assert stats.assistant_messages == 1
        assert stats.tool_results == 1
        assert stats.total_messages == 3
        assert stats.tokens.input == 10
        assert stats.tokens.output == 5

    def test_get_last_assistant_text(self) -> None:
        """Get text from last assistant message."""
        from dataclasses import dataclass, field

        @dataclass
        class MockUsage:
            input: int = 10
            output: int = 5
            cache_read: int = 0
            cache_write: int = 0
            cost: dict = field(default_factory=lambda: {"total": 0.01})

        @dataclass
        class MockContent:
            type: str = "text"
            text: str = ""

        @dataclass
        class MockMessage:
            role: str = "user"
            content: list = field(default_factory=list)
            usage: MockUsage = field(default_factory=MockUsage)
            stop_reason: str = "stop"

        messages = [
            MockMessage(role="user", content=[MockContent(type="text", text="Hello")]),
            MockMessage(
                role="assistant",
                content=[MockContent(type="text", text="Hi there!")],
                usage=MockUsage(),
            ),
        ]
        result = get_last_assistant_text(messages)
        assert result == "Hi there!"


class TestSessionSkills:
    """Tests for skill parsing utilities."""

    def test_parse_skill_block(self) -> None:
        """Parse a skill block from text."""
        text = '<skill name="test" location="/path/to/skill.md">\nContent here\n</skill>'
        result = parse_skill_block(text)
        assert result is not None
        assert result.name == "test"
        assert result.location == "/path/to/skill.md"
        assert result.content == "Content here"

    def test_parse_skill_block_with_user_message(self) -> None:
        """Parse skill block with user message."""
        text = '<skill name="test" location="/path/to/skill.md">\nContent\n</skill>\n\nUser message'
        result = parse_skill_block(text)
        assert result is not None
        assert result.user_message == "User message"

    def test_parse_skill_block_no_match(self) -> None:
        """Return None for non-skill text."""
        result = parse_skill_block("Not a skill block")
        assert result is None

    def test_expand_skill_command(self, tmp_path: Path) -> None:
        """Expand a skill command."""
        skill_file = tmp_path / "test.md"
        skill_file.write_text("# Test Skill\n\nThis is a test skill.")

        skills = [
            Skill(
                name="test",
                file_path=str(skill_file),
                base_dir=str(tmp_path),
                description="Test skill",
            )
        ]
        result = expand_skill_command("/skill:test", skills)
        assert '<skill name="test"' in result
        assert "This is a test skill" in result

    def test_expand_skill_command_with_args(self, tmp_path: Path) -> None:
        """Expand skill command with arguments."""
        skill_file = tmp_path / "test.md"
        skill_file.write_text("# Test Skill")

        skills = [
            Skill(
                name="test",
                file_path=str(skill_file),
                base_dir=str(tmp_path),
                description="Test skill",
            )
        ]
        result = expand_skill_command("/skill:test some args", skills)
        assert "some args" in result

    def test_expand_skill_command_unknown(self) -> None:
        """Pass through unknown skill commands."""
        result = expand_skill_command("/skill:unknown", [])
        assert result == "/skill:unknown"

    def test_expand_skill_command_not_skill(self) -> None:
        """Pass through non-skill commands."""
        result = expand_skill_command("Not a skill", [])
        assert result == "Not a skill"


class TestSessionManager:
    """Tests for session manager."""

    def test_create_in_memory(self) -> None:
        """Create an in-memory session."""
        sm = SessionManager.in_memory("/test/cwd")
        assert sm.get_cwd() == "/test/cwd"
        assert not sm.is_persisted()
        assert sm.get_session_id()

    def test_append_message(self) -> None:
        """Append a message to session."""
        sm = SessionManager.in_memory()
        msg = {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
        entry_id = sm.append_message(msg)
        assert entry_id
        assert sm.get_leaf_id() == entry_id

    def test_append_thinking_level_change(self) -> None:
        """Append thinking level change."""
        sm = SessionManager.in_memory()
        entry_id = sm.append_thinking_level_change("high")
        assert entry_id
        assert sm.get_leaf_id() == entry_id

    def test_append_model_change(self) -> None:
        """Append model change."""
        sm = SessionManager.in_memory()
        entry_id = sm.append_model_change("anthropic", "claude-3-opus")
        assert entry_id

    def test_append_compaction(self) -> None:
        """Append compaction entry."""
        sm = SessionManager.in_memory()
        # Add some messages first
        for i in range(5):
            sm.append_message(
                {"role": "user", "content": [{"type": "text", "text": f"Message {i}"}]}
            )

        entry_id = sm.append_compaction(
            summary="Compacted 5 messages",
            first_kept_entry_id=sm.get_leaf_id() or "",
            tokens_before=1000,
            tokens_after=500,
        )
        assert entry_id

    def test_get_entries(self) -> None:
        """Get all entries."""
        sm = SessionManager.in_memory()
        sm.append_message({"role": "user", "content": [{"type": "text", "text": "Hello"}]})
        sm.append_message({"role": "assistant", "content": [{"type": "text", "text": "Hi!"}]})

        entries = sm.get_entries()
        assert len(entries) == 2

    def test_branch(self) -> None:
        """Branch from an earlier entry."""
        sm = SessionManager.in_memory()
        id1 = sm.append_message({"role": "user", "content": [{"type": "text", "text": "First"}]})
        sm.append_message({"role": "user", "content": [{"type": "text", "text": "Second"}]})

        # Branch back to first message
        sm.branch(id1)
        assert sm.get_leaf_id() == id1

        # Add new message on branch
        id3 = sm.append_message({"role": "user", "content": [{"type": "text", "text": "Branched"}]})
        assert sm.get_leaf_id() == id3

    def test_reset_leaf(self) -> None:
        """Reset leaf pointer."""
        sm = SessionManager.in_memory()
        sm.append_message({"role": "user", "content": [{"type": "text", "text": "Hello"}]})
        assert sm.get_leaf_id() is not None

        sm.reset_leaf()
        assert sm.get_leaf_id() is None

    def test_get_tree(self) -> None:
        """Get session as tree."""
        sm = SessionManager.in_memory()
        id1 = sm.append_message({"role": "user", "content": [{"type": "text", "text": "First"}]})
        sm.append_message({"role": "user", "content": [{"type": "text", "text": "Second"}]})

        # Branch and add
        sm.branch(id1)
        sm.append_message({"role": "user", "content": [{"type": "text", "text": "Branched"}]})

        tree = sm.get_tree()
        assert len(tree) >= 1

    def test_create_persisted(self, tmp_path: Path) -> None:
        """Create a persisted session."""
        sm = SessionManager.create("/test/cwd", str(tmp_path))
        assert sm.is_persisted()
        assert sm.get_session_file()
        # File is created when first assistant message is appended
        sm.append_message({"role": "user", "content": [{"type": "text", "text": "Hello"}]})
        sm.append_message({"role": "assistant", "content": [{"type": "text", "text": "Hi"}]})
        assert os.path.exists(sm.get_session_file() or "")

    def test_open_session(self, tmp_path: Path) -> None:
        """Open an existing session."""
        # Create a session first
        sm1 = SessionManager.create("/test/cwd", str(tmp_path))
        sm1.append_message({"role": "user", "content": [{"type": "text", "text": "Hello"}]})
        session_file = sm1.get_session_file()

        assert session_file
        # Open it
        sm2 = SessionManager.open(session_file)
        # Session ID should be read from the file header
        assert sm2.get_session_id()

    def test_fork_session(self, tmp_path: Path) -> None:
        """Fork a session."""
        # Create source session
        sm1 = SessionManager.create("/source/cwd", str(tmp_path / "source"))
        sm1.append_message({"role": "user", "content": [{"type": "text", "text": "Hello"}]})
        # Ensure entries are persisted by adding an assistant message
        sm1.append_message({"role": "assistant", "content": [{"type": "text", "text": "Hi"}]})
        source_file = sm1.get_session_file()

        assert source_file
        # Fork
        sm2 = SessionManager.fork_from(source_file, "/target/cwd", str(tmp_path / "target"))
        assert sm2.get_cwd() == "/target/cwd"
        assert sm2.get_session_id() != sm1.get_session_id()

    def test_branch_with_summary(self) -> None:
        """Branch with summary."""
        sm = SessionManager.in_memory()
        id1 = sm.append_message({"role": "user", "content": [{"type": "text", "text": "First"}]})
        sm.append_message({"role": "user", "content": [{"type": "text", "text": "Second"}]})

        summary_id = sm.branch_with_summary(id1, "Abandoned branch summary")
        assert summary_id
        assert sm.get_leaf_id() == summary_id

    def test_append_label_change(self) -> None:
        """Append label change."""
        sm = SessionManager.in_memory()
        id1 = sm.append_message({"role": "user", "content": [{"type": "text", "text": "Hello"}]})

        label_id = sm.append_label_change(id1, "Important")
        assert label_id
        assert sm.get_label(id1) == "Important"

        # Clear label
        sm.append_label_change(id1, None)
        assert sm.get_label(id1) is None


class TestSessionIntegration:
    """Integration tests for session management."""

    def test_full_session_lifecycle(self, tmp_path: Path) -> None:
        """Test a complete session lifecycle."""
        sm = SessionManager.create("/test/cwd", str(tmp_path))

        # Add messages
        id1 = sm.append_message({"role": "user", "content": [{"type": "text", "text": "Hello"}]})
        sm.append_message({"role": "assistant", "content": [{"type": "text", "text": "Hi!"}]})

        # Check entries
        entries = sm.get_entries()
        assert len(entries) == 2

        # Branch
        sm.branch(id1)
        sm.append_message({"role": "user", "content": [{"type": "text", "text": "New branch"}]})

        # Check tree
        tree = sm.get_tree()
        assert len(tree) >= 1

        # Verify persistence
        assert sm.is_persisted()
        assert os.path.exists(sm.get_session_file() or "")

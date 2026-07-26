# pyright: reportMissingParameterType=false, reportUnknownParameterType=false, reportArgumentType=false
"""Tests for interactive mode.

Tests verify:
- Interactive configuration
- Interactive session
- User input handling
- Message formatting
"""

from __future__ import annotations

from cortex.ai.types import TextContent, ToolCall, ToolResultMessage
from cortex.code.interactive import (
    InteractiveConfig,
    InteractiveSession,
    create_interactive_session,
    format_assistant_message,
    format_tool_execution,
    handle_user_input,
    run_interactive_mode,
)


class TestInteractiveConfig:
    def test_default_values(self):
        config = InteractiveConfig()
        assert config.cwd == ""
        assert config.model is None
        assert config.thinking_level is None
        assert config.session_id is None
        assert config.session_file is None
        assert config.print_mode is False

    def test_with_values(self):
        config = InteractiveConfig(
            cwd="/test",
            model="claude-sonnet-4-5-20250514",
            thinking_level="high",
            session_id="123",
            print_mode=True,
        )
        assert config.cwd == "/test"
        assert config.model == "claude-sonnet-4-5-20250514"
        assert config.thinking_level == "high"
        assert config.session_id == "123"
        assert config.print_mode is True


class TestInteractiveSession:
    def test_default_values(self):
        session = InteractiveSession()
        assert session.messages == []
        assert session.current_tool_calls == []
        assert session.is_processing is False
        assert session.should_exit is False
        assert session.exit_code == 0

    def test_with_messages(self):
        message = {"role": "user", "content": "hello"}
        session = InteractiveSession(messages=[message])
        assert len(session.messages) == 1


class TestRunInteractiveMode:
    def test_basic_run(self):
        config = InteractiveConfig(cwd="/tmp")
        exit_code = run_interactive_mode(config)
        assert exit_code == 0

    def test_with_model(self):
        config = InteractiveConfig(model="claude-sonnet-4-5-20250514")
        exit_code = run_interactive_mode(config)
        assert exit_code == 0


class TestCreateInteractiveSession:
    def test_create_session(self):
        config = InteractiveConfig(cwd="/tmp")
        session = create_interactive_session(config)
        assert isinstance(session, InteractiveSession)
        assert session.messages == []


class TestHandleUserInput:
    def test_empty_input(self):
        session = InteractiveSession()
        event = handle_user_input(session, "")
        assert event is None

    def test_whitespace_input(self):
        session = InteractiveSession()
        event = handle_user_input(session, "   ")
        assert event is None

    def test_regular_message(self):
        session = InteractiveSession()
        event = handle_user_input(session, "hello")
        assert event is not None
        assert event["role"] == "user"
        assert event["content"] == "hello"

    def test_slash_command(self):
        session = InteractiveSession()
        event = handle_user_input(session, "/help")
        assert event is not None
        assert event["content"] == "/help"

    def test_multiline_input(self):
        session = InteractiveSession()
        text = "line1\nline2\nline3"
        event = handle_user_input(session, text)
        assert event is not None
        assert event["content"] == text


class TestFormatAssistantMessage:
    def test_text_only(self):
        # Use a mock object since AssistantMessage requires many fields
        class MockMessage:
            content = [TextContent(type="text", text="Hello world")]

        result = format_assistant_message(MockMessage())
        assert result == "Hello world"

    def test_multiple_text_parts(self):
        class MockMessage:
            content = [
                TextContent(type="text", text="Part 1"),
                TextContent(type="text", text="Part 2"),
            ]

        result = format_assistant_message(MockMessage())
        assert "Part 1" in result
        assert "Part 2" in result

    def test_with_tool_use(self):
        class MockMessage:
            content = [
                TextContent(type="text", text="Let me check:"),
                ToolCall(id="tool1", type="toolCall", name="read", arguments={}),
            ]

        result = format_assistant_message(MockMessage())
        assert "Let me check:" in result
        assert "[Tool: read]" in result

    def test_with_tool_result(self):
        class MockMessage:
            content = [
                ToolResultMessage(
                    tool_call_id="tool12345678",
                    tool_name="read",
                    content=[TextContent(type="text", text="result")],
                    is_error=False,
                    timestamp=1234567890,
                ),
            ]

        result = format_assistant_message(MockMessage())
        assert "[Result: tool1234...]" in result


class TestFormatToolExecution:
    def test_simple_tool(self):
        result = format_tool_execution("read", {"file_path": "/tmp/test.py"})
        assert "Executing read(" in result
        assert "/tmp/test.py" in result

    def test_long_args_truncated(self):
        long_args = {"content": "x" * 200}
        result = format_tool_execution("write", long_args)
        assert "..." in result

    def test_short_args_not_truncated(self):
        short_args = {"path": "/tmp"}
        result = format_tool_execution("ls", short_args)
        assert "..." not in result

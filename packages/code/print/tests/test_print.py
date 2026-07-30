# pyright: reportMissingParameterType=false, reportUnknownParameterType=false, reportFunctionMemberAccess=false, reportUnknownLambdaType=false
"""Tests for print mode.

Tests verify:
- Text mode outputs final assistant message
- JSON mode outputs events
- Error handling
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cortex.code.print import (
    PrintModeOptions,
    RuntimeHostProtocol,
    SessionProtocol,
    run_print_mode,
)
from cortex.code.session import PromptOptions


@dataclass
class MockAssistantMessage:
    """Mock assistant message for testing."""

    role: str = "assistant"
    content: list[Any] = field(default_factory=list)
    stop_reason: str = "stop"
    error_message: str | None = None


@dataclass
class MockTextContent:
    """Mock text content block."""

    type: str = "text"
    text: str = ""


@dataclass
class MockState:
    """Mock session state."""

    messages: list[Any] = field(default_factory=list)


def create_mock_session(
    messages: list[Any] | None = None,
    prompt_side_effect: Any = None,
) -> Any:
    """Create a mock session for testing.

    Typed `Any` rather than `SessionProtocol`: what comes back is a `MagicMock`
    *shaped* like one, and the tests read `call_args` off its methods.
    """
    session = MagicMock(spec=SessionProtocol)
    session.session_manager = MagicMock()
    session.session_manager.get_header = MagicMock(return_value=None)
    session.agent = MagicMock()
    session.agent.wait_for_idle = AsyncMock()
    session.state = MockState(messages=messages or [])
    session.subscribe = MagicMock(return_value=lambda: None)
    session.prompt = AsyncMock(side_effect=prompt_side_effect)
    session.abort = MagicMock()
    session.steer = MagicMock()
    return session


def create_mock_runtime_host(
    session: Any = None,
    dispose_side_effect: Any = None,
) -> Any:
    """Create a mock runtime host for testing."""
    host = MagicMock(spec=RuntimeHostProtocol)
    host.session = session or create_mock_session()
    host.new_session = AsyncMock()
    host.fork = AsyncMock(return_value=MagicMock(cancelled=False))
    host.switch_session = AsyncMock()
    host.dispose = MagicMock(side_effect=dispose_side_effect)
    host.set_rebind_session = MagicMock()
    return host


@pytest.mark.asyncio
async def test_text_mode_outputs_final_message():
    """Test that text mode outputs the final assistant message."""
    mock_message = MockAssistantMessage(
        content=[MockTextContent(text="Hello, world!")],
    )
    session = create_mock_session(messages=[mock_message])
    runtime_host = create_mock_runtime_host(session=session)

    with patch("cortex.code.print.write_raw_stdout") as mock_write:
        exit_code = await run_print_mode(
            runtime_host,
            PrintModeOptions(
                mode="text",
                initial_message="Say hello",
            ),
        )

    assert exit_code == 0
    # The prompt goes through `PromptOptions`, not a bare list: `AgentSession.prompt`
    # reads `options.preflight_result` first thing, so passing the images list
    # itself (which is what this did until 7.12) killed every `-p` run.
    prompt_args = session.prompt.call_args
    assert prompt_args.args[0] == "Say hello"
    assert isinstance(prompt_args.args[1], PromptOptions)
    assert prompt_args.args[1].images == []
    mock_write.assert_called_with("Hello, world!\n")


@pytest.mark.asyncio
async def test_text_mode_outputs_with_images():
    """Test that text mode passes images to prompt."""
    mock_message = MockAssistantMessage(
        content=[MockTextContent(text="Done")],
    )
    session = create_mock_session(messages=[mock_message])
    runtime_host = create_mock_runtime_host(session=session)

    from cortex.ai.types import ImageContent

    images = [ImageContent(type="image", mime_type="image/png", data="abc")]

    exit_code = await run_print_mode(
        runtime_host,
        PrintModeOptions(
            mode="text",
            initial_message="Analyze image",
            initial_images=images,
        ),
    )

    assert exit_code == 0
    prompt_args = session.prompt.call_args
    assert prompt_args.args[0] == "Analyze image"
    assert prompt_args.args[1].images == images


@pytest.mark.asyncio
async def test_text_mode_returns_nonzero_on_error():
    """Test that text mode returns non-zero on assistant error."""
    mock_message = MockAssistantMessage(
        stop_reason="error",
        error_message="provider failure",
    )
    session = create_mock_session(messages=[mock_message])
    runtime_host = create_mock_runtime_host(session=session)

    with patch("builtins.print") as mock_print:
        exit_code = await run_print_mode(
            runtime_host,
            PrintModeOptions(
                mode="text",
            ),
        )

    assert exit_code == 1
    mock_print.assert_called_once()
    assert mock_print.call_args[0][0] == "provider failure"
    assert mock_print.call_args[1]["file"] is not None


@pytest.mark.asyncio
async def test_json_mode_outputs_events():
    """Test that JSON mode outputs events."""
    session = create_mock_session()
    runtime_host = create_mock_runtime_host(session=session)

    with patch("cortex.code.print.write_raw_stdout"):
        exit_code = await run_print_mode(
            runtime_host,
            PrintModeOptions(
                mode="json",
                messages=["hello"],
            ),
        )

    assert exit_code == 0
    session.prompt.assert_called_once_with("hello")
    session.subscribe.assert_called_once()


@pytest.mark.asyncio
async def test_multiple_messages():
    """Test that multiple messages are sent in order."""
    mock_message = MockAssistantMessage(
        content=[MockTextContent(text="Done")],
    )
    session = create_mock_session(messages=[mock_message])
    runtime_host = create_mock_runtime_host(session=session)

    exit_code = await run_print_mode(
        runtime_host,
        PrintModeOptions(
            mode="text",
            initial_message="First",
            messages=["Second", "Third"],
        ),
    )

    assert exit_code == 0
    assert session.prompt.call_count == 3
    calls = session.prompt.call_args_list
    assert calls[0].args[0] == "First"
    assert calls[1].args[0] == "Second"
    assert calls[2].args[0] == "Third"


@pytest.mark.asyncio
async def test_cleanup_on_success():
    """Test that cleanup is called on success."""
    session = create_mock_session()
    runtime_host = create_mock_runtime_host(session=session)

    await run_print_mode(
        runtime_host,
        PrintModeOptions(mode="text"),
    )

    runtime_host.dispose.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_on_error():
    """Test that cleanup is called even on error."""
    session = create_mock_session()
    session.prompt = AsyncMock(side_effect=Exception("test error"))
    runtime_host = create_mock_runtime_host(session=session)

    with patch("builtins.print"):
        await run_print_mode(
            runtime_host,
            PrintModeOptions(mode="text"),
        )

    runtime_host.dispose.assert_called_once()

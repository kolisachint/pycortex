# pyright: reportUnknownLambdaType=false, reportAttributeAccessIssue=false, reportReturnType=false
"""Interactive mode: TUI-based user interaction.

Port of ``interactive-mode.ts`` from ``packages/coding-agent/src/modes/interactive/``.

This is a simplified stub implementing the core interactive mode interface.
Full TUI rendering would require integration with the cortex.tui package.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from cortex.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage

# ============================================================================
# Interactive Mode Types
# ============================================================================


@dataclass
class InteractiveConfig:
    """Configuration for interactive mode."""

    cwd: str = ""
    model: str | None = None
    thinking_level: str | None = None
    session_id: str | None = None
    session_file: str | None = None
    print_mode: bool = False


@dataclass
class InteractiveSession:
    """Interactive session state."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    current_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    is_processing: bool = False
    should_exit: bool = False
    exit_code: int = 0


# ============================================================================
# Interactive Mode Implementation
# ============================================================================


def run_interactive_mode(config: InteractiveConfig) -> int:
    """Run the interactive mode.

    This is a simplified stub that implements the core interactive mode interface.
    Full TUI rendering would require integration with the cortex.tui package.

    Args:
        config: Interactive mode configuration.

    Returns:
        Exit code (0 for success).
    """
    session = InteractiveSession()

    # Initialize session
    if config.cwd:
        os.chdir(config.cwd)

    # Main event loop stub
    print(f"Interactive mode started (model: {config.model or 'default'})")

    # Process would continue with TUI rendering...
    # For now, just return success
    return session.exit_code


def create_interactive_session(config: InteractiveConfig) -> InteractiveSession:
    """Create an interactive session.

    Args:
        config: Interactive mode configuration.

    Returns:
        New interactive session.
    """
    return InteractiveSession()


def handle_user_input(session: InteractiveSession, input_text: str) -> dict[str, Any] | None:
    """Handle user input.

    Args:
        session: Interactive session.
        input_text: User input text.

    Returns:
        Message dict to process, or None if no action needed.
    """
    if not input_text.strip():
        return None

    # Handle slash commands
    if input_text.startswith("/"):
        return {
            "role": "user",
            "content": input_text,
        }

    # Regular message
    return {
        "role": "user",
        "content": input_text,
    }


def format_assistant_message(message: AssistantMessage) -> str:
    """Format an assistant message for display.

    Args:
        message: Assistant message to format.

    Returns:
        Formatted string.
    """
    parts = []
    for part in message.content:
        if isinstance(part, TextContent):
            parts.append(part.text)
        elif isinstance(part, ToolCall):
            parts.append(f"[Tool: {part.name}]")
        elif isinstance(part, ToolResultMessage):
            tool_id = part.tool_call_id[:8] if part.tool_call_id else ""
            parts.append(f"[Result: {tool_id}...]")
    return "\n".join(parts)


def format_tool_execution(tool_name: str, args: dict[str, Any]) -> str:
    """Format tool execution for display.

    Args:
        tool_name: Name of the tool being executed.
        args: Tool arguments.

    Returns:
        Formatted string.
    """
    # Truncate long arguments
    args_str = str(args)
    if len(args_str) > 100:
        args_str = args_str[:97] + "..."
    return f"Executing {tool_name}({args_str})"

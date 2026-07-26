# pyright: reportAttributeAccessIssue=false, reportArgumentType=false
"""Custom message types and transformers for agent harnesses.

Mechanical port of hoocode's ``packages/agent/src/harness/messages.ts``.

Extends the base AgentMessage type with harness message types (bash
executions, custom/extension messages, branch and compaction summaries),
and provides a transformer to convert them to LLM-compatible messages.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from cortex.agent.types import AgentMessage, BackgroundToolResult
from cortex.ai.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolResultMessage,
    UserMessage,
)

__all__ = [
    "BACKGROUND_TASK_CUSTOM_TYPE",
    "BRANCH_SUMMARY_PREFIX",
    "BRANCH_SUMMARY_SUFFIX",
    "COMPACTION_SUMMARY_PREFIX",
    "COMPACTION_SUMMARY_SUFFIX",
    "BashExecutionMessage",
    "BranchSummaryMessage",
    "CompactionSummaryMessage",
    "CustomMessage",
    "BackgroundToolInfo",
    "convert_to_llm",
    "create_background_placeholder_text",
    "create_background_task_message",
    "create_branch_summary_message",
    "create_compaction_summary_message",
    "create_custom_message",
    "describe_background_tool",
    "bash_execution_to_text",
    "summarize_args",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COMPACTION_SUMMARY_PREFIX = (
    """The conversation history before this point was compacted into the following summary:"""
    """
<summary>
"""
)

COMPACTION_SUMMARY_SUFFIX = """
</summary>"""

BRANCH_SUMMARY_PREFIX = (
    """The following is a summary of a branch that this conversation came back from:"""
    """
<summary>
"""
)

BRANCH_SUMMARY_SUFFIX = """</summary>"""


# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------


@dataclass
class BashExecutionMessage:
    """Message type for bash executions via the ! command."""

    role: str = "bashExecution"
    command: str = ""
    output: str = ""
    exit_code: int | None = None
    cancelled: bool = False
    truncated: bool = False
    full_output_path: str | None = None
    timestamp: int = 0
    exclude_from_context: bool = False
    """If true, this message is excluded from LLM context (!! prefix)."""


@dataclass
class CustomMessage:
    """Message type for extension-injected messages via sendMessage()."""

    role: str = "custom"
    custom_type: str = ""
    content: str | list[TextContent | ImageContent] = ""
    display: bool = False
    details: Any = None
    timestamp: int = 0


@dataclass
class BranchSummaryMessage:
    """Message type for branch summaries."""

    role: str = "branchSummary"
    summary: str = ""
    from_id: str = ""
    timestamp: int = 0


@dataclass
class CompactionSummaryMessage:
    """Message type for compaction summaries."""

    role: str = "compactionSummary"
    summary: str = ""
    tokens_before: int = 0
    tokens_after: int | None = None
    timestamp: int = 0


# ---------------------------------------------------------------------------
# Conversion functions
# ---------------------------------------------------------------------------


def bash_execution_to_text(msg: BashExecutionMessage) -> str:
    """Convert a BashExecutionMessage to user message text for LLM context."""
    text = f"Ran `{msg.command}`\n"
    if msg.output:
        text += f"```\n{msg.output}\n```"
    else:
        text += "(no output)"
    if msg.cancelled:
        text += "\n\n(command cancelled)"
    elif msg.exit_code is not None and msg.exit_code != 0:
        text += f"\n\nCommand exited with code {msg.exit_code}"
    if msg.truncated and msg.full_output_path:
        text += f"\n\n[Output truncated. Full output: {msg.full_output_path}]"
    return text


def _parse_timestamp(timestamp: str) -> int:
    """Parse a timestamp string to milliseconds since epoch."""
    try:
        # Try ISO format
        from datetime import datetime

        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, ImportError):
        # Fallback to current time
        return int(time.time() * 1000)


def create_branch_summary_message(
    summary: str,
    from_id: str,
    timestamp: str,
) -> BranchSummaryMessage:
    """Create a branch summary message."""
    return BranchSummaryMessage(
        role="branchSummary",
        summary=summary,
        from_id=from_id,
        timestamp=_parse_timestamp(timestamp),
    )


def create_compaction_summary_message(
    summary: str,
    tokens_before: int,
    timestamp: str,
    tokens_after: int | None = None,
) -> CompactionSummaryMessage:
    """Create a compaction summary message."""
    return CompactionSummaryMessage(
        role="compactionSummary",
        summary=summary,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        timestamp=_parse_timestamp(timestamp),
    )


# ---------------------------------------------------------------------------
# Background task helpers
# ---------------------------------------------------------------------------

BACKGROUND_TASK_CUSTOM_TYPE = "backgroundTask"
"""customType used for the follow-up message a finished background tool injects."""


@dataclass
class BackgroundToolCall:
    """Minimal shape of a tool call."""

    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class BackgroundToolInfo:
    """A consistent, human-readable description of a background tool call."""

    is_mcp_tool: bool = False
    """True for MCP server tools (registered as ``mcp_<server>_<tool>``)."""

    subagent_type: str = ""
    """The subagent type for Task calls; the tool name otherwise."""

    label: str = ""
    """Short label used verbatim in both the start and finish messages."""

    summary: str | None = None
    """One-line summary of what the call is doing, derived from its arguments."""


def _first_line(text: str, max_len: int = 120) -> str:
    """First non-empty line of a string, trimmed and capped for one-line display."""
    lines = text.split("\n")
    line = ""
    for line_text in lines:
        if line_text.strip():
            line = line_text.strip()
            break
    if len(line) > max_len:
        return f"{line[: max_len - 1].strip()}…"
    return line


def summarize_args(args: dict[str, Any]) -> str | None:
    """Summarize a tool's arguments as up to three ``key: value`` pairs."""
    parts: list[str] = []
    for key, value in args.items():
        if value is None or value == "":
            continue
        rendered = value if isinstance(value, str) else str(value)
        parts.append(f"{key}: {_first_line(rendered, 48)}")
        if len(parts) == 3:
            break
    return ", ".join(parts) if parts else None


def describe_background_tool(tool_call: BackgroundToolCall) -> BackgroundToolInfo:
    """Describe a background tool call consistently."""
    args = tool_call.arguments
    is_mcp_tool = tool_call.name.startswith("mcp_")

    if is_mcp_tool:
        # Registered name is ``mcp_<server>_<tool>``; drop the prefix for display.
        pretty = tool_call.name.removeprefix("mcp_")
        return BackgroundToolInfo(
            is_mcp_tool=True,
            subagent_type=tool_call.name,
            label=f"MCP tool `{pretty}`",
            summary=summarize_args(args),
        )

    subagent_type = args.get("subagent_type", tool_call.name)
    if not isinstance(subagent_type, str):
        subagent_type = tool_call.name

    summary = None
    description = args.get("description")
    if isinstance(description, str) and description.strip():
        summary = description.strip()
    else:
        prompt = args.get("prompt")
        if isinstance(prompt, str):
            summary = _first_line(prompt)

    return BackgroundToolInfo(
        is_mcp_tool=False,
        subagent_type=subagent_type,
        label=f"subagent `{subagent_type}`",
        summary=summary,
    )


def create_background_placeholder_text(tool_call: BackgroundToolCall) -> str:
    """Verbose, human-readable placeholder shown when a background tool is dispatched."""
    info = describe_background_tool(tool_call)
    what = f" — {_first_line(info.summary, 80)}" if info.summary else ""
    if info.is_mcp_tool:
        return (
            f"Started {info.label} in the background{what}."
            " Its result arrives as a follow-up; keep working."
        )
    return (
        f"Delegated to {info.label} in the background{what}."
        " I'll be notified when it finishes; use TaskOutput to check progress or read the result."
    )


def create_background_task_message(result: BackgroundToolResult) -> CustomMessage:
    """Build the follow-up message injected when a background tool finishes."""
    tool_call = BackgroundToolCall(
        name=result.tool_call.name if hasattr(result.tool_call, "name") else "",
        arguments=result.tool_call.arguments if hasattr(result.tool_call, "arguments") else {},
    )
    info = describe_background_tool(tool_call)

    # Subagent Task: return the compact notification
    if not info.is_mcp_tool:
        return CustomMessage(
            role="custom",
            custom_type=BACKGROUND_TASK_CUSTOM_TYPE,
            content=result.result.content,
            display=True,
            details={
                "subagentType": info.subagent_type,
                "isMcpTool": False,
                "isError": result.is_error,
            },
            timestamp=int(time.time() * 1000),
        )

    # MCP background tool: deliver header + full result body
    verb = "failed" if result.is_error else "finished"
    summary = f" ({info.summary})" if info.summary else ""
    header = f"Background {info.label}{summary} {verb}:"
    content: list[TextContent | ImageContent] = [
        TextContent(type="text", text=header),
        *result.result.content,
    ]
    return CustomMessage(
        role="custom",
        custom_type=BACKGROUND_TASK_CUSTOM_TYPE,
        content=content,
        display=True,
        details={
            "subagentType": info.subagent_type,
            "isMcpTool": True,
            "isError": result.is_error,
        },
        timestamp=int(time.time() * 1000),
    )


def create_custom_message(
    custom_type: str,
    content: str | list[TextContent | ImageContent],
    display: bool,
    details: Any,
    timestamp: str,
) -> CustomMessage:
    """Convert CustomMessageEntry to AgentMessage format."""
    return CustomMessage(
        role="custom",
        custom_type=custom_type,
        content=content,
        display=display,
        details=details,
        timestamp=_parse_timestamp(timestamp),
    )


# ---------------------------------------------------------------------------
# LLM conversion
# ---------------------------------------------------------------------------


def convert_to_llm(
    messages: list[AgentMessage],
) -> list[UserMessage | AssistantMessage | ToolResultMessage]:
    """Transform AgentMessages (including custom types) to LLM-compatible Messages.

    This is used by:
    - Agent's transform_to_llm option (for prompt calls and queued messages)
    - Compaction's generate_summary (for summarization)
    - Custom extensions and tools
    """
    result: list[UserMessage | AssistantMessage | ToolResultMessage] = []
    for m in messages:
        msg: UserMessage | AssistantMessage | ToolResultMessage | None = None
        role = getattr(m, "role", None)

        if role == "bashExecution":
            # Skip messages excluded from context (!! prefix)
            if getattr(m, "exclude_from_context", False):
                continue
            msg = UserMessage(
                role="user",
                content=[TextContent(type="text", text=bash_execution_to_text(m))],
                timestamp=getattr(m, "timestamp", 0),
            )
        elif role == "custom":
            content = getattr(m, "content", "")
            if isinstance(content, str):
                content_list = [TextContent(type="text", text=content)]
            else:
                content_list = content
            msg = UserMessage(
                role="user",
                content=content_list,
                timestamp=getattr(m, "timestamp", 0),
            )
        elif role == "branchSummary":
            summary = getattr(m, "summary", "")
            msg = UserMessage(
                role="user",
                content=[
                    TextContent(
                        type="text", text=BRANCH_SUMMARY_PREFIX + summary + BRANCH_SUMMARY_SUFFIX
                    )
                ],
                timestamp=getattr(m, "timestamp", 0),
            )
        elif role == "compactionSummary":
            summary = getattr(m, "summary", "")
            msg = UserMessage(
                role="user",
                content=[
                    TextContent(
                        type="text",
                        text=COMPACTION_SUMMARY_PREFIX + summary + COMPACTION_SUMMARY_SUFFIX,
                    )
                ],
                timestamp=getattr(m, "timestamp", 0),
            )
        elif role in ("user", "assistant", "toolResult"):
            msg = m  # type: ignore[assignment]

        if msg is not None:
            result.append(msg)

    return result

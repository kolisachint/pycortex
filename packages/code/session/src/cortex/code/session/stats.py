# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnusedCallResult=false, reportCallIssue=false, reportReturnType=false, reportMissingTypeArgument=false, reportUnnecessaryIsInstance=false, reportUnusedVariable=false
"""Pure helpers for AgentSession reporting and export.

Port of ``agent-session-stats.ts`` from ``packages/coding-agent/src/core/``.
These functions derive statistics, context-usage, forkable user messages, and
JSONL exports from session state without touching the live agent or extension
runner.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cortex.agent.types import AgentMessage


@dataclass
class ContextUsage:
    """Context window usage information."""

    tokens: int | None
    context_window: int
    percent: float | None


@dataclass
class SessionStats:
    """Session statistics for /session command."""

    session_file: str | None
    session_id: str
    user_messages: int
    assistant_messages: int
    tool_calls: int
    tool_results: int
    total_messages: int
    tokens: TokenStats
    cost: float
    context_usage: ContextUsage | None = None


@dataclass
class TokenStats:
    """Aggregate token usage statistics."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0


CURRENT_SESSION_VERSION = 1


def extract_user_message_text(content: str | list[dict[str, Any]]) -> str:
    """Extract concatenated text from a user message content value."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
        )
    return ""


def compute_session_stats(
    messages: list[AgentMessage],
    session_file: str | None,
    session_id: str,
    context_usage: ContextUsage | None = None,
) -> SessionStats:
    """Compute aggregate statistics for a session."""
    user_messages = sum(1 for m in messages if m.role == "user")
    assistant_messages = sum(1 for m in messages if m.role == "assistant")
    tool_results = sum(1 for m in messages if m.role == "toolResult")

    tool_calls = 0
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0
    total_cost = 0.0

    for message in messages:
        if message.role == "assistant":
            for content in message.content:
                if content.type == "toolCall":
                    tool_calls += 1
            usage = message.usage
            total_input += usage.input
            total_output += usage.output
            total_cache_read += usage.cache_read
            total_cache_write += usage.cache_write
            total_cost += usage.cost.get("total", 0.0)

    token_stats = TokenStats(
        input=total_input,
        output=total_output,
        cache_read=total_cache_read,
        cache_write=total_cache_write,
        total=total_input + total_output + total_cache_read + total_cache_write,
    )

    return SessionStats(
        session_file=session_file,
        session_id=session_id,
        user_messages=user_messages,
        assistant_messages=assistant_messages,
        tool_calls=tool_calls,
        tool_results=tool_results,
        total_messages=len(messages),
        tokens=token_stats,
        cost=total_cost,
        context_usage=context_usage,
    )


def compute_context_usage(
    context_window: int,
    messages: list[AgentMessage],
    has_post_compaction_usage: bool = True,
) -> ContextUsage | None:
    """Estimate current context-window usage.

    After compaction, the last assistant usage reflects pre-compaction context
    size, so usage is only trusted from an assistant that responded after the
    latest compaction boundary.
    """
    if context_window <= 0:
        return None

    if not has_post_compaction_usage:
        return ContextUsage(tokens=None, context_window=context_window, percent=None)

    # Estimate tokens from messages
    estimate_tokens = 0
    for message in messages:
        if message.role == "assistant":
            usage = message.usage
            estimate_tokens += usage.input + usage.output

    percent = (estimate_tokens / context_window) * 100 if context_window > 0 else None

    return ContextUsage(
        tokens=estimate_tokens,
        context_window=context_window,
        percent=percent,
    )


def collect_user_messages_for_forking(
    entries: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Collect all user messages on the session (for the fork selector)."""
    result = []

    for entry in entries:
        if entry.get("type") != "message":
            continue
        message = entry.get("message", {})
        if message.get("role") != "user":
            continue

        text = extract_user_message_text(message.get("content", ""))
        if text:
            result.append({"entry_id": entry.get("id", ""), "text": text})

    return result


def get_last_assistant_text(messages: list[AgentMessage]) -> str | None:
    """Get the text content of the last non-empty assistant message (for /copy)."""
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        # Skip aborted messages with no content
        if message.stop_reason == "aborted" and not message.content:
            continue

        text = ""
        for content in message.content:
            if content.type == "text":
                text += content.text

        if text.strip():
            return text

    return None


def export_session_branch_to_jsonl(
    session_id: str,
    session_cwd: str,
    branch_entries: list[dict[str, Any]],
    output_path: str | None = None,
) -> str:
    """Export the current session branch to a JSONL file.

    Writes the session header followed by all entries on the current branch path,
    re-chaining parentIds into a linear sequence.
    """
    if output_path is None:
        timestamp = datetime.now(UTC).isoformat().replace(":", "-").replace(".", "-")
        output_path = f"session-{timestamp}.jsonl"

    file_path = Path(output_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    header = {
        "type": "session",
        "version": CURRENT_SESSION_VERSION,
        "id": session_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "cwd": session_cwd,
    }

    lines = [json.dumps(header)]

    # Re-chain parentIds to form a linear sequence
    prev_id: str | None = None
    for entry in branch_entries:
        linear = {**entry, "parentId": prev_id}
        lines.append(json.dumps(linear))
        prev_id = entry.get("id")

    file_path.write_text("\n".join(lines) + "\n")

    return str(file_path)

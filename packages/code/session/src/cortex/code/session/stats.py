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
    model: Any,
    session_manager: Any,
    messages: list[AgentMessage],
) -> ContextUsage | None:
    """Estimate current context-window usage.

    After compaction, the last assistant usage reflects pre-compaction context
    size, so usage is only trusted from an assistant that responded after the
    latest compaction boundary. When no such assistant exists yet, tokens are
    reported as ``None`` — unknown until the next LLM response, which is what
    the footer draws as ``?``.
    """
    from cortex.agent.compaction import calculate_context_tokens, estimate_context_tokens
    from cortex.code.session.manager import get_latest_compaction_entry

    if model is None:
        return None

    context_window = getattr(model, "context_window", 0) or 0
    if context_window <= 0:
        return None

    branch_entries = session_manager.get_branch()
    latest_compaction = get_latest_compaction_entry(branch_entries)

    if latest_compaction is not None:
        # Is there a valid assistant usage after the compaction boundary?
        compaction_index = len(branch_entries) - 1 - branch_entries[::-1].index(latest_compaction)
        has_post_compaction_usage = False
        for index in range(len(branch_entries) - 1, compaction_index, -1):
            entry = branch_entries[index]
            if entry.get("type") != "message":
                continue
            assistant = entry.get("message") or {}
            if _entry_field(assistant, "role") != "assistant":
                continue
            if _entry_field(assistant, "stop_reason") in ("aborted", "error"):
                break
            usage = _entry_field(assistant, "usage")
            if usage is not None and _context_tokens(usage, calculate_context_tokens) > 0:
                has_post_compaction_usage = True
            break

        if not has_post_compaction_usage:
            return ContextUsage(tokens=None, context_window=context_window, percent=None)

    estimate = estimate_context_tokens(messages)
    percent = (estimate.tokens / context_window) * 100

    return ContextUsage(
        tokens=estimate.tokens,
        context_window=context_window,
        percent=percent,
    )


def _entry_field(message: Any, name: str) -> Any:
    """A field off a session entry's message, stored as a dict or held as a model."""
    if isinstance(message, dict):
        return message.get(name)
    return getattr(message, name, None)


def _context_tokens(usage: Any, calculate: Any) -> int:
    """Context tokens for a usage that may still be the dict the session file holds."""
    if isinstance(usage, dict):
        total = usage.get("total_tokens") or 0
        if total:
            return int(total)
        return int(
            (usage.get("input") or 0)
            + (usage.get("output") or 0)
            + (usage.get("cache_read") or 0)
            + (usage.get("cache_write") or 0)
        )
    return int(calculate(usage))


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

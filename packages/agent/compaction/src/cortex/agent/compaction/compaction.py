# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportCallIssue=false
"""Compaction logic for agent sessions.

Mechanical port of hoocode's ``packages/agent/src/harness/compaction/compaction.ts``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from cortex.agent.types import AgentMessage, ThinkingLevel
from cortex.ai.types import Message, Usage

from .utils import (
    SUMMARIZATION_SYSTEM_PROMPT,
    FileOperations,
    compute_file_lists,
    create_file_ops,
    extract_file_ops_from_message,
    format_file_operations,
    serialize_conversation,
)

# ============================================================================
# Constants
# ============================================================================

COMPACTION_SUMMARY_PREFIX = (
    "The conversation history before this point was compacted into "
    "the following summary:\n\n<summary>\n"
)
"""Prefix for compaction summary messages."""

COMPACTION_SUMMARY_SUFFIX = "\n</summary>"
"""Suffix for compaction summary messages."""


# ============================================================================
# Types
# ============================================================================


@dataclass
class CompactionResult:
    """Result of a compaction operation."""

    summary: str
    first_kept_entry_id: str
    tokens_before: int
    tokens_after: int | None = None
    details: Any = None


@dataclass
class CompactionDetails:
    """Details stored in CompactionEntry for file tracking."""

    read_files: list[str]
    modified_files: list[str]


@dataclass
class CompactionSettings:
    """Settings for compaction."""

    enabled: bool = True
    reserve_tokens: int = 16384
    """Tokens reserved for prompt + LLM response."""
    keep_recent_tokens: int = 20000
    """Tokens to keep from recent messages."""
    max_context_ratio: float | None = None
    """Optional soft trigger: compact once context exceeds this fraction of window."""
    max_messages: int = 100
    """Maximum number of messages to keep after compaction."""
    min_messages_to_compact: int = 10
    """Minimum number of messages before compaction is triggered."""


DEFAULT_COMPACTION_SETTINGS = CompactionSettings()
"""Default compaction settings."""


@dataclass
class ContextUsageEstimate:
    """Estimate of context token usage."""

    tokens: int
    usage_tokens: int
    trailing_tokens: int
    last_usage_index: int | None


@dataclass
class CutPointResult:
    """Result of finding a cut point."""

    first_kept_entry_index: int
    turn_start_index: int
    is_split_turn: bool


@dataclass
class CompactionPreparation:
    """Preparation data for compaction."""

    first_kept_entry_id: str
    messages_to_summarize: list[AgentMessage]
    turn_prefix_messages: list[AgentMessage]
    is_split_turn: bool
    tokens_before: int
    previous_summary: str | None = None
    file_ops: FileOperations = field(default_factory=create_file_ops)
    settings: CompactionSettings = field(default_factory=CompactionSettings)


# ============================================================================
# Token Calculation
# ============================================================================


def calculate_context_tokens(usage: Usage) -> int:
    """Calculate total context tokens from usage.

    Uses the native total_tokens field when available, falls back to computing from components.
    """
    return usage.total_tokens or (usage.input + usage.output + usage.cache_read + usage.cache_write)


def get_assistant_usage(msg: AgentMessage) -> Usage | None:
    """Get usage from an assistant message if available.

    Skips aborted and error messages as they don't have valid usage data.
    """
    if hasattr(msg, "role") and msg.role == "assistant":
        assistant_msg = msg
        if hasattr(assistant_msg, "stop_reason"):
            if assistant_msg.stop_reason not in ("aborted", "error") and hasattr(
                assistant_msg, "usage"
            ):
                return assistant_msg.usage
    return None


def get_last_assistant_usage_from_entries(entries: list[Any]) -> Usage | None:
    """Find the last non-aborted assistant message usage from session entries."""
    for entry in reversed(entries):
        if hasattr(entry, "type") and entry.type == "message":
            msg = getattr(entry, "message", None)
            if msg:
                usage = get_assistant_usage(msg)
                if usage:
                    return usage
    return None


def get_last_assistant_usage_info(messages: list[AgentMessage]) -> tuple[Usage, int] | None:
    """Get last assistant usage info from messages."""
    for i in range(len(messages) - 1, -1, -1):
        usage = get_assistant_usage(messages[i])
        if usage:
            return usage, i
    return None


def estimate_context_tokens(messages: list[AgentMessage]) -> ContextUsageEstimate:
    """Estimate context tokens from messages.

    Uses the last assistant usage when available. If there are messages after
    the last usage, estimate their tokens with estimate_tokens.
    """
    usage_info = get_last_assistant_usage_info(messages)

    if not usage_info:
        estimated = sum(estimate_tokens(msg) for msg in messages)
        return ContextUsageEstimate(
            tokens=estimated,
            usage_tokens=0,
            trailing_tokens=estimated,
            last_usage_index=None,
        )

    usage, usage_index = usage_info
    usage_tokens = calculate_context_tokens(usage)
    trailing_tokens = sum(
        estimate_tokens(messages[i]) for i in range(usage_index + 1, len(messages))
    )

    return ContextUsageEstimate(
        tokens=usage_tokens + trailing_tokens,
        usage_tokens=usage_tokens,
        trailing_tokens=trailing_tokens,
        last_usage_index=usage_index,
    )


# ============================================================================
# Token Estimation
# ============================================================================


def estimate_tokens(message: AgentMessage) -> int:
    """Estimate token count for a message using chars/4 heuristic.

    This is conservative (overestimates tokens).
    """
    chars = 0

    role = getattr(message, "role", "")

    if role == "user":
        content = getattr(message, "content", "")
        if isinstance(content, str):
            chars = len(content)
        elif isinstance(content, list):
            for block in content:
                if hasattr(block, "type") and block.type == "text" and hasattr(block, "text"):
                    chars += len(block.text)
        return math.ceil(chars / 4)

    elif role == "assistant":
        assistant = message
        if hasattr(assistant, "content") and isinstance(assistant.content, list):
            for block in assistant.content:
                if hasattr(block, "type"):
                    if block.type == "text" and hasattr(block, "text"):
                        chars += len(block.text)
                    elif block.type == "thinking" and hasattr(block, "thinking"):
                        chars += len(block.thinking)
                    elif block.type == "toolCall" and hasattr(block, "arguments"):
                        chars += len(getattr(block, "name", "")) + len(json.dumps(block.arguments))
        return math.ceil(chars / 4)

    elif role in ("custom", "toolResult"):
        content = getattr(message, "content", "")
        if isinstance(content, str):
            chars = len(content)
        elif isinstance(content, list):
            for block in content:
                if hasattr(block, "type"):
                    if block.type == "text" and hasattr(block, "text"):
                        chars += len(block.text)
                    elif block.type == "image":
                        chars += 4800  # Estimate images as 4000 chars, or 1200 tokens
        return math.ceil(chars / 4)

    elif role == "bashExecution":
        command = getattr(message, "command", "")
        output = getattr(message, "output", "")
        chars = len(command) + len(output)
        return math.ceil(chars / 4)

    elif role in ("branchSummary", "compactionSummary"):
        summary = getattr(message, "summary", "")
        chars = len(summary)
        return math.ceil(chars / 4)

    return 0


def estimate_message_tokens(message: AgentMessage) -> int:
    """Estimate token count for an AgentMessage (alias for estimate_tokens)."""
    return estimate_tokens(message)


# ============================================================================
# Message Conversion
# ============================================================================


def convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Convert AgentMessages to LLM-compatible Messages."""
    from cortex.agent.harness.messages import convert_to_llm as harness_convert

    return harness_convert(messages)


def get_message_from_entry(entry: Any) -> AgentMessage | None:
    """Extract AgentMessage from a session entry."""
    from cortex.agent.harness.messages import (
        create_branch_summary_message,
        create_compaction_summary_message,
        create_custom_message,
    )

    entry_type = getattr(entry, "type", "")
    if entry_type == "message":
        msg = getattr(entry, "message", None)
        if msg and hasattr(msg, "role") and msg.role == "toolResult":
            return None
        return msg
    elif entry_type == "custom_message":
        return create_custom_message(
            entry.custom_type,
            entry.content,
            entry.display,
            entry.details,
            entry.timestamp,
        )
    elif entry_type == "branch_summary":
        return create_branch_summary_message(entry.summary, entry.from_id, entry.timestamp)
    elif entry_type == "compaction":
        return create_compaction_summary_message(
            entry.summary,
            entry.tokens_before,
            entry.timestamp,
            entry.tokens_after,
        )
    return None


def get_message_from_entry_for_compaction(entry: Any) -> AgentMessage | None:
    """Extract AgentMessage from entry, skipping compaction entries."""
    if getattr(entry, "type", "") == "compaction":
        return None
    return get_message_from_entry(entry)


# ============================================================================
# Compaction Check
# ============================================================================


def should_compact(context_tokens: int, context_window: int, settings: CompactionSettings) -> bool:
    """Check if compaction should trigger based on context usage."""
    if not settings.enabled:
        return False

    trigger = context_window - settings.reserve_tokens

    ratio = settings.max_context_ratio
    if ratio is not None and 0 < ratio < 1:
        trigger = min(trigger, math.floor(ratio * context_window))

    return context_tokens > trigger


# ============================================================================
# Cut Point Detection
# ============================================================================


def find_valid_cut_points(entries: list[Any], start_index: int, end_index: int) -> list[int]:
    """Find valid cut points: indices of user, assistant, custom, or bashExecution messages.

    Never cut at tool results (they must follow their tool call).
    """
    cut_points: list[int] = []

    for i in range(start_index, end_index):
        entry = entries[i]
        entry_type = getattr(entry, "type", "")

        if entry_type == "message":
            msg = getattr(entry, "message", None)
            if msg:
                role = getattr(msg, "role", "")
                if role in (
                    "bashExecution",
                    "custom",
                    "branchSummary",
                    "compactionSummary",
                    "user",
                    "assistant",
                ):
                    cut_points.append(i)

        # branch_summary and custom_message are user-role messages, valid cut points
        if entry_type in ("branch_summary", "custom_message"):
            cut_points.append(i)

    return cut_points


def find_turn_start_index(entries: list[Any], entry_index: int, start_index: int) -> int:
    """Find the user message (or bashExecution) that starts the turn.

    Returns -1 if no turn start found before the index.
    """
    for i in range(entry_index, start_index - 1, -1):
        entry = entries[i]
        entry_type = getattr(entry, "type", "")

        # branch_summary and custom_message are user-role messages, can start a turn
        if entry_type in ("branch_summary", "custom_message"):
            return i

        if entry_type == "message":
            msg = getattr(entry, "message", None)
            if msg:
                role = getattr(msg, "role", "")
                if role in ("user", "bashExecution"):
                    return i

    return -1


def find_cut_point(
    entries: list[Any],
    start_index: int,
    end_index: int,
    keep_recent_tokens: int,
) -> CutPointResult:
    """Find the cut point in session entries that keeps approximately keep_recent_tokens.

    Algorithm: Walk backwards from newest, accumulating estimated message sizes.
    Stop when we've accumulated >= keep_recent_tokens. Cut at that point.
    """
    cut_points = find_valid_cut_points(entries, start_index, end_index)

    if not cut_points:
        return CutPointResult(
            first_kept_entry_index=start_index,
            turn_start_index=-1,
            is_split_turn=False,
        )

    # Walk backwards from newest, accumulating estimated message sizes
    accumulated_tokens = 0
    cut_index = cut_points[0]  # Default: keep from first message

    for i in range(end_index - 1, start_index - 1, -1):
        entry = entries[i]
        if getattr(entry, "type", "") != "message":
            continue

        # Estimate this message's size
        msg = getattr(entry, "message", None)
        if msg:
            message_tokens = estimate_tokens(msg)
            accumulated_tokens += message_tokens

            # Check if we've exceeded the budget
            if accumulated_tokens >= keep_recent_tokens:
                # Find the closest valid cut point at or after this entry
                for c in cut_points:
                    if c >= i:
                        cut_index = c
                        break
                break

    # Scan backwards from cutIndex to include any non-message entries
    while cut_index > start_index:
        prev_entry = entries[cut_index - 1]
        prev_type = getattr(prev_entry, "type", "")

        # Stop at session header or compaction boundaries
        if prev_type == "compaction":
            break
        if prev_type == "message":
            break
        # Include this non-message entry
        cut_index -= 1

    # Determine if this is a split turn
    cut_entry = entries[cut_index]
    is_user_message = (
        getattr(cut_entry, "type", "") == "message"
        and getattr(getattr(cut_entry, "message", None), "role", "") == "user"
    )

    turn_start_index = (
        -1 if is_user_message else find_turn_start_index(entries, cut_index, start_index)
    )

    return CutPointResult(
        first_kept_entry_index=cut_index,
        turn_start_index=turn_start_index,
        is_split_turn=not is_user_message and turn_start_index != -1,
    )


# ============================================================================
# File Operation Extraction
# ============================================================================


def extract_file_operations(
    messages: list[AgentMessage],
    entries: list[Any],
    prev_compaction_index: int,
) -> FileOperations:
    """Extract file operations from messages and previous compaction entries."""
    file_ops = create_file_ops()

    # Collect from previous compaction's details (if hoocode-generated)
    if prev_compaction_index >= 0:
        prev_compaction = entries[prev_compaction_index]
        if not getattr(prev_compaction, "from_hook", False) and hasattr(prev_compaction, "details"):
            details = prev_compaction.details
            if isinstance(details, dict):
                read_files = details.get("readFiles", [])
                modified_files = details.get("modifiedFiles", [])
            else:
                read_files = getattr(details, "read_files", []) if details else []
                modified_files = getattr(details, "modified_files", []) if details else []

            if isinstance(read_files, list):
                for f in read_files:
                    file_ops.read.add(f)
            if isinstance(modified_files, list):
                for f in modified_files:
                    file_ops.edited.add(f)

    # Extract from tool calls in messages
    for msg in messages:
        extract_file_ops_from_message(msg, file_ops)

    return file_ops


# ============================================================================
# Compaction Preparation
# ============================================================================


def prepare_compaction(
    path_entries: list[Any],
    settings: CompactionSettings | None = None,
) -> CompactionPreparation | None:
    """Prepare compaction using session entries.

    Returns preparation data for compact(), or None if compaction is not needed.
    """
    if settings is None:
        settings = DEFAULT_COMPACTION_SETTINGS

    # Don't compact if last entry is already a compaction
    if path_entries and getattr(path_entries[-1], "type", "") == "compaction":
        return None

    # Find previous compaction
    prev_compaction_index = -1
    for i in range(len(path_entries) - 1, -1, -1):
        if getattr(path_entries[i], "type", "") == "compaction":
            prev_compaction_index = i
            break

    previous_summary = None
    boundary_start = 0
    if prev_compaction_index >= 0:
        prev_compaction = path_entries[prev_compaction_index]
        previous_summary = getattr(prev_compaction, "summary", None)
        first_kept_id = getattr(prev_compaction, "first_kept_entry_id", None)
        if first_kept_id:
            for idx, entry in enumerate(path_entries):
                if getattr(entry, "id", None) == first_kept_id:
                    boundary_start = idx
                    break
            else:
                boundary_start = prev_compaction_index + 1
        else:
            boundary_start = prev_compaction_index + 1

    boundary_end = len(path_entries)

    # Estimate tokens before compaction
    # Build context from entries to get accurate estimate
    from cortex.agent.harness.types import build_session_context

    context = build_session_context(path_entries)
    tokens_before = estimate_context_tokens(context.messages).tokens

    cut_point = find_cut_point(
        path_entries, boundary_start, boundary_end, settings.keep_recent_tokens
    )

    # Get UUID of first kept entry
    first_kept_entry = (
        path_entries[cut_point.first_kept_entry_index]
        if cut_point.first_kept_entry_index < len(path_entries)
        else None
    )
    if not first_kept_entry or not getattr(first_kept_entry, "id", None):
        return None  # Session needs migration

    first_kept_entry_id = first_kept_entry.id

    history_end = (
        cut_point.turn_start_index if cut_point.is_split_turn else cut_point.first_kept_entry_index
    )

    # Messages to summarize (will be discarded after summary)
    messages_to_summarize: list[AgentMessage] = []
    for i in range(boundary_start, history_end):
        msg = get_message_from_entry_for_compaction(path_entries[i])
        if msg:
            messages_to_summarize.append(msg)

    # Messages for turn prefix summary (if splitting a turn)
    turn_prefix_messages: list[AgentMessage] = []
    if cut_point.is_split_turn:
        for i in range(cut_point.turn_start_index, cut_point.first_kept_entry_index):
            msg = get_message_from_entry_for_compaction(path_entries[i])
            if msg:
                turn_prefix_messages.append(msg)

    # Extract file operations from messages and previous compaction
    file_ops = extract_file_operations(messages_to_summarize, path_entries, prev_compaction_index)

    # Also extract file ops from turn prefix if splitting
    if cut_point.is_split_turn:
        for msg in turn_prefix_messages:
            extract_file_ops_from_message(msg, file_ops)

    return CompactionPreparation(
        first_kept_entry_id=first_kept_entry_id,
        messages_to_summarize=messages_to_summarize,
        turn_prefix_messages=turn_prefix_messages,
        is_split_turn=cut_point.is_split_turn,
        tokens_before=tokens_before,
        previous_summary=previous_summary,
        file_ops=file_ops,
        settings=settings,
    )


# ============================================================================
# Summary Generation (Requires LLM)
# ============================================================================

SUMMARIZATION_PROMPT = (
    "The messages above are a conversation to summarize. Create a structured "
    "context checkpoint summary that another LLM will use to continue the work.\n\n"
    "Use this EXACT format:\n\n"
    "## Goal\n"
    "[What is the user trying to accomplish?]\n\n"
    "## Constraints & Preferences\n"
    "- [Any constraints, preferences, or requirements mentioned by user]\n"
    '- [Or "(none)" if none were mentioned]\n\n'
    "## Progress\n"
    "### Done\n"
    "- [x] [Completed tasks/changes]\n\n"
    "### In Progress\n"
    "- [ ] [Current work]\n\n"
    "### Blocked\n"
    "- [Issues preventing progress, if any]\n\n"
    "## Key Decisions\n"
    "- **[Decision]**: [Brief rationale]\n\n"
    "## Next Steps\n"
    "1. [Ordered list of what should happen next]\n\n"
    "## Critical Context\n"
    "- [Any data, examples, or references needed to continue]\n"
    '- [Or "(none)" if not applicable]\n\n'
    "Keep each section concise. Preserve exact file paths, function names, "
    "and error messages."
)

UPDATE_SUMMARIZATION_PROMPT = (
    "The messages above are NEW conversation messages to incorporate into "
    "the existing summary provided in <previous-summary> tags.\n\n"
    "Update the existing structured summary with new information. RULES:\n"
    "- PRESERVE all existing information from the previous summary\n"
    "- ADD new progress, decisions, and context from the new messages\n"
    '- UPDATE the Progress section: move items from "In Progress" to "Done"\n'
    '- UPDATE "Next Steps" based on what was accomplished\n'
    "- PRESERVE exact file paths, function names, and error messages\n"
    "- If something is no longer relevant, you may remove it\n\n"
    "Use this EXACT format:\n\n"
    "## Goal\n"
    "[Preserve existing goals, add new ones if the task expanded]\n\n"
    "## Constraints & Preferences\n"
    "- [Preserve existing, add new ones discovered]\n\n"
    "## Progress\n"
    "### Done\n"
    "- [x] [Include previously done items AND newly completed items]\n\n"
    "### In Progress\n"
    "- [ ] [Current work - update based on progress]\n\n"
    "### Blocked\n"
    "- [Current blockers - remove if resolved]\n\n"
    "## Key Decisions\n"
    "- **[Decision]**: [Brief rationale] (preserve all previous, add new)\n\n"
    "## Next Steps\n"
    "1. [Update based on current state]\n\n"
    "## Critical Context\n"
    "- [Preserve important context, add new if needed]\n\n"
    "Keep each section concise. Preserve exact file paths, function names, "
    "and error messages."
)


async def generate_summary(
    current_messages: list[AgentMessage],
    model: Any,
    reserve_tokens: int,
    api_key: str,
    headers: dict[str, str] | None = None,
    signal: Any = None,
    custom_instructions: str | None = None,
    previous_summary: str | None = None,
    thinking_level: ThinkingLevel | None = None,
) -> str:
    """Generate a summary of the conversation using the LLM.

    If previous_summary is provided, uses the update prompt to merge.
    """
    from cortex.ai.stream import complete_simple

    max_tokens = math.floor(0.8 * reserve_tokens)

    # Use update prompt if we have a previous summary, otherwise initial prompt
    base_prompt = UPDATE_SUMMARIZATION_PROMPT if previous_summary else SUMMARIZATION_PROMPT
    if custom_instructions:
        base_prompt = f"{base_prompt}\n\nAdditional focus: {custom_instructions}"

    # Serialize conversation to text so model doesn't try to continue it
    llm_messages = convert_to_llm(current_messages)
    conversation_text = serialize_conversation(llm_messages)

    # Build the prompt with conversation wrapped in tags
    prompt_text = f"<conversation>\n{conversation_text}\n</conversation>\n\n"
    if previous_summary:
        prompt_text += f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
    prompt_text += base_prompt

    summarization_messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": prompt_text}],
            "timestamp": 0,  # Will be replaced by complete_simple
        }
    ]

    # Build completion options
    completion_options: dict[str, Any] = {"maxTokens": max_tokens}
    if signal:
        completion_options["signal"] = signal
    if api_key:
        completion_options["apiKey"] = api_key
    if headers:
        completion_options["headers"] = headers

    # Add reasoning only for reasoning models with thinking enabled
    if getattr(model, "reasoning", False) and thinking_level and thinking_level != "off":
        completion_options["reasoning"] = thinking_level

    response = await complete_simple(
        model,
        {"systemPrompt": SUMMARIZATION_SYSTEM_PROMPT, "messages": summarization_messages},
        completion_options,
    )

    if getattr(response, "stop_reason", None) == "error":
        error_msg = getattr(response, "error_message", None) or "Unknown error"
        raise RuntimeError(f"Summarization failed: {error_msg}")

    # Extract text content
    text_content = ""
    content = getattr(response, "content", None)
    if content and isinstance(content, list):
        text_parts = []
        for block in content:
            if hasattr(block, "type") and block.type == "text" and hasattr(block, "text"):
                text_parts.append(block.text)
        text_content = "\n".join(text_parts)

    if not text_content.strip():
        raise RuntimeError("Summarization produced an empty summary")

    return text_content


TURN_PREFIX_SUMMARIZATION_PROMPT = (
    "This is the PREFIX of a turn that was too large to keep. "
    "The SUFFIX (recent work) is retained.\n\n"
    "Summarize the prefix to provide context for the retained suffix:\n\n"
    "## Original Request\n"
    "[What did the user ask for in this turn?]\n\n"
    "## Early Progress\n"
    "- [Key decisions and work done in the prefix]\n\n"
    "## Context for Suffix\n"
    "- [Information needed to understand the retained recent work]\n\n"
    "Be concise. Focus on what's needed to understand the kept suffix."
)


async def generate_turn_prefix_summary(
    messages: list[AgentMessage],
    model: Any,
    reserve_tokens: int,
    api_key: str,
    headers: dict[str, str] | None = None,
    signal: Any = None,
    thinking_level: ThinkingLevel | None = None,
) -> str:
    """Generate a summary for a turn prefix (when splitting a turn)."""
    from cortex.ai.stream import complete_simple

    max_tokens = math.floor(0.5 * reserve_tokens)  # Smaller budget for turn prefix

    llm_messages = convert_to_llm(messages)
    conversation_text = serialize_conversation(llm_messages)
    prompt_text = (
        f"<conversation>\n{conversation_text}\n</conversation>\n\n"
        f"{TURN_PREFIX_SUMMARIZATION_PROMPT}"
    )

    summarization_messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": prompt_text}],
            "timestamp": 0,
        }
    ]

    # Build completion options
    completion_options: dict[str, Any] = {"maxTokens": max_tokens}
    if signal:
        completion_options["signal"] = signal
    if api_key:
        completion_options["apiKey"] = api_key
    if headers:
        completion_options["headers"] = headers

    # Add reasoning only for reasoning models with thinking enabled
    if getattr(model, "reasoning", False) and thinking_level and thinking_level != "off":
        completion_options["reasoning"] = thinking_level

    response = await complete_simple(
        model,
        {"systemPrompt": SUMMARIZATION_SYSTEM_PROMPT, "messages": summarization_messages},
        completion_options,
    )

    if getattr(response, "stop_reason", None) == "error":
        error_msg = getattr(response, "error_message", None) or "Unknown error"
        raise RuntimeError(f"Turn prefix summarization failed: {error_msg}")

    # Extract text content
    text_content = ""
    content = getattr(response, "content", None)
    if content and isinstance(content, list):
        text_parts = []
        for block in content:
            if hasattr(block, "type") and block.type == "text" and hasattr(block, "text"):
                text_parts.append(block.text)
        text_content = "\n".join(text_parts)

    if not text_content.strip():
        raise RuntimeError("Turn prefix summarization produced an empty summary")

    return text_content


# ============================================================================
# Main Compaction Function
# ============================================================================


async def compact(
    preparation: CompactionPreparation,
    model: Any,
    api_key: str,
    headers: dict[str, str] | None = None,
    custom_instructions: str | None = None,
    signal: Any = None,
    thinking_level: ThinkingLevel | None = None,
) -> CompactionResult:
    """Generate summaries for compaction using prepared data.

    Returns CompactionResult - SessionManager adds uuid/parentUuid when saving.
    """
    first_kept_entry_id = preparation.first_kept_entry_id
    messages_to_summarize = preparation.messages_to_summarize
    turn_prefix_messages = preparation.turn_prefix_messages
    is_split_turn = preparation.is_split_turn
    tokens_before = preparation.tokens_before
    previous_summary = preparation.previous_summary
    file_ops = preparation.file_ops
    settings = preparation.settings

    # Generate summaries (can be parallel if both needed) and merge into one
    summary: str

    if is_split_turn and turn_prefix_messages:
        # Generate both summaries
        history_result = "No prior history."
        if messages_to_summarize:
            history_result = await generate_summary(
                messages_to_summarize,
                model,
                settings.reserve_tokens,
                api_key,
                headers,
                signal,
                custom_instructions,
                previous_summary,
                thinking_level,
            )

        turn_prefix_result = await generate_turn_prefix_summary(
            turn_prefix_messages,
            model,
            settings.reserve_tokens,
            api_key,
            headers,
            signal,
            thinking_level,
        )

        # Merge into single summary
        summary = (
            f"{history_result}\n\n---\n\n**Turn Context (split turn):**\n\n{turn_prefix_result}"
        )
    else:
        # Just generate history summary
        summary = await generate_summary(
            messages_to_summarize,
            model,
            settings.reserve_tokens,
            api_key,
            headers,
            signal,
            custom_instructions,
            previous_summary,
            thinking_level,
        )

    # Compute file lists and append to summary
    read_files, modified_files = compute_file_lists(file_ops)
    summary += format_file_operations(read_files, modified_files)

    if not first_kept_entry_id:
        raise RuntimeError("First kept entry has no UUID - session may need migration")

    # Estimate post-compaction context size
    discarded_tokens = sum(estimate_tokens(msg) for msg in messages_to_summarize)
    discarded_tokens += sum(estimate_tokens(msg) for msg in turn_prefix_messages)
    summary_tokens = math.ceil(len(summary) / 4)
    tokens_after = max(0, tokens_before - discarded_tokens + summary_tokens)

    return CompactionResult(
        summary=summary,
        first_kept_entry_id=first_kept_entry_id,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        details=CompactionDetails(read_files=read_files, modified_files=modified_files),
    )


# ============================================================================
# Collect Messages for Compaction (Legacy helper)
# ============================================================================


def collect_messages_for_compaction(
    messages: list[AgentMessage],
    token_budget: int,
) -> tuple[list[AgentMessage], list[AgentMessage]]:
    """Collect messages for compaction, keeping most recent within budget.

    Returns:
        Tuple of (messages_to_compact, messages_to_keep)
    """
    if token_budget <= 0:
        # Zero or negative budget means keep everything
        return [], messages

    # Walk from newest to oldest, keeping messages within budget
    kept: list[AgentMessage] = []
    compact: list[AgentMessage] = []
    current_tokens = 0

    for msg in reversed(messages):
        msg_tokens = estimate_message_tokens(msg)
        if current_tokens + msg_tokens <= token_budget:
            kept.insert(0, msg)
            current_tokens += msg_tokens
        else:
            compact.insert(0, msg)

    # If we couldn't keep any messages, keep at least the last one
    if not kept and messages:
        kept = [messages[-1]]
        compact = messages[:-1]

    return compact, kept


def compute_compaction_result(
    summary: str,
    first_kept_entry_id: str,
    messages_to_compact: list[AgentMessage],
    messages_to_keep: list[AgentMessage],
    file_ops: FileOperations,
) -> CompactionResult:
    """Compute the compaction result with token estimates."""
    # Estimate tokens before compaction
    tokens_before = sum(estimate_message_tokens(msg) for msg in messages_to_compact)
    tokens_before += sum(estimate_message_tokens(msg) for msg in messages_to_keep)

    # Estimate summary tokens
    summary_tokens = estimate_tokens_summary(summary)

    # Estimate tokens after compaction
    tokens_after = sum(estimate_message_tokens(msg) for msg in messages_to_keep)
    tokens_after += summary_tokens

    # Compute file lists
    read_files, modified_files = compute_file_lists(file_ops)
    details = CompactionDetails(read_files=read_files, modified_files=modified_files)

    return CompactionResult(
        summary=summary,
        first_kept_entry_id=first_kept_entry_id,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        details=details,
    )


def estimate_tokens_summary(text: str) -> int:
    """Estimate token count for summary text using chars/4 heuristic."""
    return math.ceil(len(text) / 4)


# ============================================================================
# Summary Generation Prompt (Legacy helper)
# ============================================================================


def generate_summary_prompt(
    messages: list[AgentMessage],
    previous_summary: str | None = None,
    custom_instructions: str | None = None,
) -> str:
    """Generate a summarization prompt from messages.

    This creates the prompt text that would be sent to an LLM for summarization.
    The actual LLM call is not implemented here - it would be done by the harness.
    """
    llm_messages = convert_to_llm(messages)
    conversation_text = serialize_conversation(llm_messages)

    prompt_parts = [
        "<conversation>",
        conversation_text,
        "</conversation>",
        "",
        "Please summarize the above conversation, focusing on:",
        "1. Key decisions and their rationale",
        "2. Files that were read or modified",
        "3. Current state of the task",
        "4. Any pending items or next steps",
    ]

    if previous_summary:
        prompt_parts.extend(
            [
                "",
                "Previous summary to update:",
                previous_summary,
            ]
        )

    if custom_instructions:
        prompt_parts.extend(
            [
                "",
                "Additional instructions:",
                custom_instructions,
            ]
        )

    return "\n".join(prompt_parts)

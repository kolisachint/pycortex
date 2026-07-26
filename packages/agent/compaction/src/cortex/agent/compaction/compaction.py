# pyright: reportAttributeAccessIssue=false
"""Compaction logic for agent sessions.

Mechanical port of hoocode's ``packages/agent/src/harness/compaction/compaction.ts``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from cortex.agent.types import AgentMessage
from cortex.ai.types import Message

from .utils import (
    FileOperations,
    compute_file_lists,
    serialize_conversation,
)

# ============================================================================
# Constants
# ============================================================================

COMPACTION_SUMMARY_PREFIX = "[system summary]: Conversation compacted. "
"""Prefix for compaction summary messages."""

COMPACTION_SUMMARY_SUFFIX = "\n[system: This summary was auto-generated to preserve context.]"
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
    tokens_after: int
    details: CompactionDetails | None = None


@dataclass
class CompactionDetails:
    """Details stored in CompactionEntry for file tracking."""

    read_files: list[str]
    modified_files: list[str]


@dataclass
class CompactionSettings:
    """Settings for compaction."""

    reserve_tokens: int = 16384
    """Tokens reserved for prompt + LLM response."""
    max_messages: int = 100
    """Maximum number of messages to keep after compaction."""
    min_messages_to_compact: int = 10
    """Minimum number of messages before compaction is triggered."""


# ============================================================================
# Token Estimation
# ============================================================================


def estimate_tokens(text: str) -> int:
    """Estimate token count for text using chars/4 heuristic."""
    return math.ceil(len(text) / 4)


def estimate_message_tokens(message: AgentMessage) -> int:
    """Estimate token count for an AgentMessage."""
    if hasattr(message, "content"):
        if isinstance(message.content, str):
            return estimate_tokens(message.content)
        elif isinstance(message.content, list):
            total = 0
            for block in message.content:
                if hasattr(block, "text"):
                    total += estimate_tokens(block.text)
                elif hasattr(block, "thinking"):
                    total += estimate_tokens(block.thinking)
                elif hasattr(block, "arguments"):
                    total += estimate_tokens(str(block.arguments))
            return total
    return 0


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


# ============================================================================
# Compaction Logic
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
    tokens_before = 0
    for msg in messages_to_compact:
        tokens_before += estimate_message_tokens(msg)
    for msg in messages_to_keep:
        tokens_before += estimate_message_tokens(msg)

    # Estimate summary tokens
    summary_tokens = estimate_tokens(summary)

    # Estimate tokens after compaction
    tokens_after = 0
    for msg in messages_to_keep:
        tokens_after += estimate_message_tokens(msg)
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


def should_compact(messages: list[AgentMessage], settings: CompactionSettings) -> bool:
    """Check if compaction should be triggered."""
    if len(messages) < settings.min_messages_to_compact:
        return False

    total_tokens = sum(estimate_message_tokens(msg) for msg in messages)
    return total_tokens > settings.reserve_tokens


# ============================================================================
# Summary Generation (Stub - requires LLM)
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

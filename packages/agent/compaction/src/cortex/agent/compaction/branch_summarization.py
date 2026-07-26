"""Branch summarization for tree navigation.

Mechanical port of hoocode's ``packages/agent/src/harness/compaction/branch-summarization.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cortex.agent.types import AgentMessage
from cortex.ai.types import Message

from .compaction import estimate_message_tokens, get_message_from_entry
from .utils import (
    FileOperations,
    create_file_ops,
    extract_file_ops_from_message,
    serialize_conversation,
)

# ============================================================================
# Types
# ============================================================================


@dataclass
class BranchSummaryResult:
    """Result of a branch summary operation."""

    summary: str | None = None
    read_files: list[str] | None = None
    modified_files: list[str] | None = None
    aborted: bool = False
    error: str | None = None


@dataclass
class BranchSummaryDetails:
    """Details stored in BranchSummaryEntry for file tracking."""

    read_files: list[str]
    modified_files: list[str]


@dataclass
class BranchPreparation:
    """Prepared entries for branch summarization."""

    messages: list[AgentMessage]
    file_ops: FileOperations
    total_tokens: int


@dataclass
class CollectEntriesResult:
    """Result of collecting entries for branch summary."""

    entries: list[Any]
    common_ancestor_id: str | None


# ============================================================================
# Entry Collection
# ============================================================================


async def collect_entries_for_branch_summary(
    session: Any,
    old_leaf_id: str | None,
    target_id: str,
) -> CollectEntriesResult:
    """Collect entries that should be summarized when navigating.

    Walks from oldLeafId back to the common ancestor with targetId, collecting entries
    along the way.

    Args:
        session: Session manager with getBranch and getEntry methods
        old_leaf_id: Current position (where we're navigating from)
        target_id: Target position (where we're navigating to)

    Returns:
        Entries to summarize and the common ancestor
    """
    # If no old position, nothing to summarize
    if old_leaf_id is None:
        return CollectEntriesResult(entries=[], common_ancestor_id=None)

    # Find common ancestor (deepest node that's on both paths)
    old_branch = await session.get_branch(old_leaf_id)
    old_path = {e.id for e in old_branch}
    target_branch = await session.get_branch(target_id)

    # targetBranch is root-first, so iterate backwards to find deepest common ancestor
    common_ancestor_id: str | None = None
    for entry in reversed(target_branch):
        if entry.id in old_path:
            common_ancestor_id = entry.id
            break

    # Collect entries from old leaf back to common ancestor
    entries: list[Any] = []
    current: str | None = old_leaf_id

    while current and current != common_ancestor_id:
        entry = await session.get_entry(current)
        if entry is None:
            break
        entries.append(entry)
        current = getattr(entry, "parent_id", None)

    # Reverse to get chronological order
    entries.reverse()

    return CollectEntriesResult(entries=entries, common_ancestor_id=common_ancestor_id)


# ============================================================================
# Entry to Message Conversion
# ============================================================================


def prepare_branch_entries(
    entries: list[Any],
    token_budget: int = 0,
) -> BranchPreparation:
    """Prepare entries for summarization with token budget.

    Walks entries from NEWEST to OLDEST, adding messages until we hit the token budget.
    This ensures we keep the most recent context when the branch is too long.

    Also collects file operations from tool calls and existing branch summaries.

    Args:
        entries: Entries in chronological order
        token_budget: Maximum tokens to include (0 = no limit)

    Returns:
        BranchPreparation with messages, file ops, and total tokens
    """
    messages: list[AgentMessage] = []
    file_ops = create_file_ops()
    total_tokens = 0

    # First pass: collect file ops from ALL entries
    for entry in entries:
        if hasattr(entry, "type") and entry.type == "message":
            msg = getattr(entry, "message", None)
            if msg:
                extract_file_ops_from_message(msg, file_ops)
        elif hasattr(entry, "type") and entry.type == "branch_summary":
            # Extract file ops from existing branch summary details
            details = getattr(entry, "details", None)
            if isinstance(details, dict):
                for path in details.get("readFiles", []):
                    file_ops.read.add(path)
                for path in details.get("modifiedFiles", []):
                    file_ops.written.add(path)

    # Second pass: collect messages within token budget (newest first)
    for entry in reversed(entries):
        msg = get_message_from_entry(entry)
        if msg is None:
            continue

        msg_tokens = estimate_message_tokens(msg)
        if token_budget > 0 and total_tokens + msg_tokens > token_budget:
            break

        messages.insert(0, msg)
        total_tokens += msg_tokens

    return BranchPreparation(
        messages=messages,
        file_ops=file_ops,
        total_tokens=total_tokens,
    )


# ============================================================================
# Summary Generation (Stub - requires LLM)
# ============================================================================


def generate_branch_summary_prompt(
    messages: list[AgentMessage],
    custom_instructions: str | None = None,
    replace_instructions: bool = False,
) -> str:
    """Generate a branch summary prompt.

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
    ]

    if replace_instructions and custom_instructions:
        prompt_parts.append(custom_instructions)
    else:
        prompt_parts.extend(
            [
                "Please summarize the branch being navigated away from.",
                "Focus on:",
                "1. What was being worked on",
                "2. Key decisions made",
                "3. Files that were read or modified",
                "4. Current state of the work",
            ]
        )
        if custom_instructions:
            prompt_parts.extend(["", "Additional instructions:", custom_instructions])

    return "\n".join(prompt_parts)


def convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Convert AgentMessages to LLM-compatible Messages."""
    from cortex.agent.harness.messages import convert_to_llm as harness_convert

    return harness_convert(messages)

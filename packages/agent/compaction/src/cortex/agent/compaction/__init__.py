"""Compaction and branch summarization for agent sessions.

Mechanical port of hoocode's ``packages/agent/src/harness/compaction/``.
"""

from __future__ import annotations

from .branch_summarization import (
    BranchPreparation,
    BranchSummaryDetails,
    BranchSummaryResult,
    CollectEntriesResult,
    collect_entries_for_branch_summary,
    generate_branch_summary_prompt,
    prepare_branch_entries,
)
from .compaction import (
    COMPACTION_SUMMARY_PREFIX,
    COMPACTION_SUMMARY_SUFFIX,
    CompactionDetails,
    CompactionResult,
    CompactionSettings,
    collect_messages_for_compaction,
    compute_compaction_result,
    estimate_message_tokens,
    estimate_tokens,
    generate_summary_prompt,
    get_message_from_entry,
    should_compact,
)
from .utils import (
    SUMMARIZATION_SYSTEM_PROMPT,
    FileOperations,
    compute_file_lists,
    create_file_ops,
    extract_file_ops_from_message,
    format_file_operations,
    serialize_conversation,
)

__all__ = [
    # Compaction
    "CompactionResult",
    "CompactionDetails",
    "CompactionSettings",
    "COMPACTION_SUMMARY_PREFIX",
    "COMPACTION_SUMMARY_SUFFIX",
    "estimate_tokens",
    "estimate_message_tokens",
    "collect_messages_for_compaction",
    "compute_compaction_result",
    "should_compact",
    "generate_summary_prompt",
    "get_message_from_entry",
    # Branch summarization
    "BranchSummaryResult",
    "BranchSummaryDetails",
    "BranchPreparation",
    "CollectEntriesResult",
    "collect_entries_for_branch_summary",
    "prepare_branch_entries",
    "generate_branch_summary_prompt",
    # Utils
    "FileOperations",
    "SUMMARIZATION_SYSTEM_PROMPT",
    "create_file_ops",
    "extract_file_ops_from_message",
    "compute_file_lists",
    "format_file_operations",
    "serialize_conversation",
]

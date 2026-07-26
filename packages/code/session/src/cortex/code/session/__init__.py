"""Code session management.

Port of ``packages/coding-agent/src/core/agent-session*.ts`` and ``session-manager.ts``.
"""

from __future__ import annotations

from cortex.code.session.compaction import CompactionController
from cortex.code.session.cwd import (
    MissingSessionCwdError,
    SessionCwdIssue,
    assert_session_cwd_exists,
    format_missing_session_cwd_error,
    format_missing_session_cwd_prompt,
    get_missing_session_cwd_issue,
)
from cortex.code.session.manager import (
    CURRENT_SESSION_VERSION,
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    LabelEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionHeader,
    SessionInfo,
    SessionInfoEntry,
    SessionManager,
    SessionTreeNode,
    ThinkingLevelChangeEntry,
    find_most_recent_session,
    get_default_session_dir,
    get_latest_compaction_entry,
)
from cortex.code.session.retry import AutoRetryController
from cortex.code.session.skills import (
    ParsedSkillBlock,
    Skill,
    SkillExpansionError,
    expand_skill_command,
    parse_skill_block,
)
from cortex.code.session.stats import (
    ContextUsage,
    SessionStats,
    TokenStats,
    collect_user_messages_for_forking,
    compute_context_usage,
    compute_session_stats,
    export_session_branch_to_jsonl,
    extract_user_message_text,
    get_last_assistant_text,
)
from cortex.code.session.tree_navigation import (
    NavigateTreeOptions,
    NavigateTreeResult,
    TreeNavigationController,
)

__all__ = [
    # Manager
    "SessionManager",
    "CURRENT_SESSION_VERSION",
    "SessionHeader",
    "SessionInfo",
    "SessionTreeNode",
    "MessageEntry",
    "ThinkingLevelChangeEntry",
    "ModelChangeEntry",
    "CompactionEntry",
    "CustomEntry",
    "SessionInfoEntry",
    "LabelEntry",
    "BranchSummaryEntry",
    "CustomMessageEntry",
    "find_most_recent_session",
    "get_default_session_dir",
    "get_latest_compaction_entry",
    # CWD
    "SessionCwdIssue",
    "MissingSessionCwdError",
    "assert_session_cwd_exists",
    "format_missing_session_cwd_error",
    "format_missing_session_cwd_prompt",
    "get_missing_session_cwd_issue",
    # Stats
    "ContextUsage",
    "SessionStats",
    "TokenStats",
    "collect_user_messages_for_forking",
    "compute_context_usage",
    "compute_session_stats",
    "export_session_branch_to_jsonl",
    "extract_user_message_text",
    "get_last_assistant_text",
    # Skills
    "ParsedSkillBlock",
    "Skill",
    "SkillExpansionError",
    "expand_skill_command",
    "parse_skill_block",
    # Retry
    "AutoRetryController",
    # Compaction
    "CompactionController",
    # Tree Navigation
    "NavigateTreeOptions",
    "NavigateTreeResult",
    "TreeNavigationController",
]

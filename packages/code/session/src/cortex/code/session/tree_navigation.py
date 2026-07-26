# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnusedCallResult=false, reportCallIssue=false, reportReturnType=false, reportMissingTypeArgument=false
"""Tree-navigation controller for AgentSession.

Port of ``agent-session-tree-navigation.ts`` from ``packages/coding-agent/src/core/``.
Handles navigating to a different node in the session tree.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class NavigateTreeOptions:
    """Options for navigating the session tree."""

    summarize: bool = False
    custom_instructions: str | None = None
    replace_instructions: bool = False
    label: str | None = None


@dataclass
class NavigateTreeResult:
    """Result of navigating the session tree."""

    editor_text: str | None = None
    cancelled: bool = False
    aborted: bool = False
    summary_entry: dict[str, Any] | None = None


class TreeNavigationController:
    """Tree-navigation controller for AgentSession."""

    def __init__(self, deps: Any) -> None:
        self._deps = deps
        self._branch_summary_abort: asyncio.Event | None = None

    @property
    def is_summarizing(self) -> bool:
        """Whether branch summarization is currently running."""
        return self._branch_summary_abort is not None

    def abort_branch_summary(self) -> None:
        """Cancel in-progress branch summarization."""
        if self._branch_summary_abort:
            self._branch_summary_abort.set()

    async def navigate_tree(
        self,
        target_id: str,
        options: NavigateTreeOptions | None = None,
    ) -> NavigateTreeResult:
        """Navigate to a different node in the session tree.

        Unlike fork() which creates a new session file, this stays in the same file.
        """
        if options is None:
            options = NavigateTreeOptions()

        session_manager = self._deps.session_manager
        old_leaf_id = session_manager.get_leaf_id()

        # No-op if already at target
        if target_id == old_leaf_id:
            return NavigateTreeResult(cancelled=False)

        # Model required for summarization
        if options.summarize and not self._deps.get_model():
            raise ValueError("No model available for summarization")

        target_entry = session_manager.get_entry(target_id)
        if not target_entry:
            raise ValueError(f"Entry {target_id} not found")

        # Collect entries to summarize
        entries_to_summarize, common_ancestor_id = self._collect_entries_for_summary(
            session_manager, old_leaf_id, target_id
        )

        self._branch_summary_abort = asyncio.Event()

        try:
            extension_summary: dict[str, Any] | None = None
            from_extension = False

            label = options.label

            # Emit session_before_tree event (simplified)
            # In real implementation, this would call extension runner

            # Run default summarizer if needed
            summary_text: str | None = None
            summary_details: dict[str, Any] | None = None

            if options.summarize and entries_to_summarize and not extension_summary:
                model = self._deps.get_model()
                await self._deps.get_required_request_auth(model)
                # Simplified summary generation
                summary_text = f"Branch summary of {len(entries_to_summarize)} entries"
                summary_details = {"readFiles": [], "modifiedFiles": []}
            elif extension_summary:
                summary_text = extension_summary.get("summary")
                summary_details = extension_summary.get("details")

            # Determine new leaf position
            new_leaf_id: str | None
            editor_text: str | None = None

            if target_entry.get("type") == "message":
                message = target_entry.get("message", {})
                if message.get("role") == "user":
                    new_leaf_id = target_entry.get("parentId")
                    editor_text = self._extract_user_message_text(message.get("content", ""))
                else:
                    new_leaf_id = target_id
            elif target_entry.get("type") == "custom_message":
                new_leaf_id = target_entry.get("parentId")
                content = target_entry.get("content", "")
                if isinstance(content, str):
                    editor_text = content
                elif isinstance(content, list):
                    editor_text = "".join(
                        c.get("text", "") for c in content if c.get("type") == "text"
                    )
            else:
                new_leaf_id = target_id

            # Switch leaf (with or without summary)
            summary_entry = None
            if summary_text:
                summary_id = session_manager.branch_with_summary(
                    new_leaf_id, summary_text, summary_details, from_extension
                )
                summary_entry = session_manager.get_entry(summary_id)
                if label:
                    session_manager.append_label_change(summary_id, label)
            elif new_leaf_id is None:
                session_manager.reset_leaf()
            else:
                session_manager.branch(new_leaf_id)

            if label and not summary_text:
                session_manager.append_label_change(target_id, label)

            # Update agent state
            entries = session_manager.get_entries()
            self._deps.set_agent_messages(entries)

            return NavigateTreeResult(
                editor_text=editor_text,
                cancelled=False,
                summary_entry=summary_entry,
            )
        finally:
            self._branch_summary_abort = None

    def _collect_entries_for_summary(
        self,
        session_manager: Any,
        old_leaf_id: str | None,
        target_id: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Collect entries to summarize (simplified version)."""
        # Walk from old leaf to target, collecting entries
        old_branch = session_manager.get_branch(old_leaf_id)
        target_branch = session_manager.get_branch(target_id)

        # Find common ancestor
        old_ids = {e.get("id") for e in old_branch}
        common_ancestor_id = None
        for entry in target_branch:
            if entry.get("id") in old_ids:
                common_ancestor_id = entry.get("id")
                break

        # Entries to summarize are from old leaf to common ancestor
        entries_to_summarize = []
        for entry in old_branch:
            if entry.get("id") == common_ancestor_id:
                break
            entries_to_summarize.append(entry)

        return entries_to_summarize, common_ancestor_id

    def _extract_user_message_text(self, content: Any) -> str:
        """Extract text from user message content."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            )
        return ""

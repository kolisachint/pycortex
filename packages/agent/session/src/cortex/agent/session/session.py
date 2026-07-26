"""Session management.

Mechanical port of hoocode's ``packages/agent/src/harness/session/session.ts``.
"""

from __future__ import annotations

from typing import Any

from cortex.agent.harness.messages import (
    create_branch_summary_message,
    create_compaction_summary_message,
    create_custom_message,
)
from cortex.agent.harness.types import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    LabelEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionContext,
    SessionInfoEntry,
    SessionMetadata,
    SessionStorage,
    SessionTreeEntry,
    ThinkingLevelChangeEntry,
)
from cortex.agent.types import AgentMessage

__all__ = [
    "Session",
    "build_session_context",
]


def build_session_context(path_entries: list[SessionTreeEntry]) -> SessionContext:
    """Build session context from path entries."""
    thinking_level: str = "off"
    model: dict[str, str] | None = None
    compaction: CompactionEntry | None = None

    for entry in path_entries:
        if isinstance(entry, ThinkingLevelChangeEntry):
            thinking_level = entry.thinking_level
        elif isinstance(entry, ModelChangeEntry):
            model = {"provider": entry.provider, "model_id": entry.model_id}
        elif isinstance(entry, MessageEntry) and entry.message.role == "assistant":
            model = {
                "provider": getattr(entry.message, "provider", ""),
                "model_id": getattr(entry.message, "model", ""),
            }
        elif isinstance(entry, CompactionEntry):
            compaction = entry

    messages: list[AgentMessage] = []

    def append_message(entry: SessionTreeEntry) -> None:
        if isinstance(entry, MessageEntry):
            messages.append(entry.message)
        elif isinstance(entry, CustomMessageEntry):
            messages.append(
                create_custom_message(
                    entry.custom_type,
                    entry.content,
                    entry.display,
                    entry.details,
                    entry.timestamp,
                )
            )
        elif isinstance(entry, BranchSummaryEntry) and entry.summary:
            messages.append(
                create_branch_summary_message(entry.summary, entry.from_id, entry.timestamp)
            )

    if compaction:
        messages.append(
            create_compaction_summary_message(
                compaction.summary,
                compaction.tokens_before,
                compaction.timestamp,
                compaction.tokens_after,
            )
        )
        compaction_idx = next(
            (
                i
                for i, e in enumerate(path_entries)
                if isinstance(e, CompactionEntry) and e.id == compaction.id
            ),
            -1,
        )
        found_first_kept = False
        for i in range(compaction_idx):
            entry = path_entries[i]
            if entry.id == compaction.first_kept_entry_id:
                found_first_kept = True
            if found_first_kept:
                append_message(entry)
        for i in range(compaction_idx + 1, len(path_entries)):
            append_message(path_entries[i])
    else:
        for entry in path_entries:
            append_message(entry)

    return SessionContext(messages=messages, thinking_level=thinking_level, model=model)


class Session:
    """Session class for managing conversation history."""

    def __init__(self, storage: SessionStorage) -> None:
        self._storage = storage

    async def get_metadata(self) -> SessionMetadata:
        """Get session metadata."""
        return await self._storage.get_metadata()

    def get_storage(self) -> SessionStorage:
        """Get the underlying storage."""
        return self._storage

    async def get_leaf_id(self) -> str | None:
        """Get the current leaf ID."""
        return await self._storage.get_leaf_id()

    async def get_entry(self, entry_id: str) -> SessionTreeEntry | None:
        """Get an entry by ID."""
        return await self._storage.get_entry(entry_id)

    async def get_entries(self) -> list[SessionTreeEntry]:
        """Get all entries."""
        return await self._storage.get_entries()

    async def get_branch(self, from_id: str | None = None) -> list[SessionTreeEntry]:
        """Get the branch from a given ID (or current leaf)."""
        leaf_id = from_id if from_id is not None else await self._storage.get_leaf_id()
        return await self._storage.get_path_to_root(leaf_id)

    async def build_context(self) -> SessionContext:
        """Build the session context."""
        return build_session_context(await self.get_branch())

    async def get_label(self, entry_id: str) -> str | None:
        """Get the label for an entry."""
        return await self._storage.get_label(entry_id)

    async def get_session_name(self) -> str | None:
        """Get the session name."""
        entries = await self._storage.find_entries("session_info")
        if entries and isinstance(entries[-1], SessionInfoEntry):
            name = entries[-1].name
            if name:
                return name.strip()
        return None

    async def _append_typed_entry(self, entry: SessionTreeEntry) -> str:
        """Append a typed entry and return its ID."""
        await self._storage.append_entry(entry)
        return entry.id

    async def append_message(self, message: AgentMessage) -> str:
        """Append a message entry."""
        return await self._append_typed_entry(
            MessageEntry(
                id=await self._storage.create_entry_id(),
                parent_id=await self._storage.get_leaf_id(),
                timestamp="",
                message=message,
            )
        )

    async def append_thinking_level_change(self, thinking_level: str) -> str:
        """Append a thinking level change entry."""
        return await self._append_typed_entry(
            ThinkingLevelChangeEntry(
                id=await self._storage.create_entry_id(),
                parent_id=await self._storage.get_leaf_id(),
                timestamp="",
                thinking_level=thinking_level,
            )
        )

    async def append_model_change(self, provider: str, model_id: str) -> str:
        """Append a model change entry."""
        return await self._append_typed_entry(
            ModelChangeEntry(
                id=await self._storage.create_entry_id(),
                parent_id=await self._storage.get_leaf_id(),
                timestamp="",
                provider=provider,
                model_id=model_id,
            )
        )

    async def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Any = None,
        from_hook: bool = False,
        tokens_after: int | None = None,
    ) -> str:
        """Append a compaction entry."""
        return await self._append_typed_entry(
            CompactionEntry(
                id=await self._storage.create_entry_id(),
                parent_id=await self._storage.get_leaf_id(),
                timestamp="",
                summary=summary,
                first_kept_entry_id=first_kept_entry_id,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                details=details,
                from_hook=from_hook,
            )
        )

    async def append_custom_entry(self, custom_type: str, details: Any = None) -> str:
        """Append a custom entry."""
        return await self._append_typed_entry(
            CustomEntry(
                id=await self._storage.create_entry_id(),
                parent_id=await self._storage.get_leaf_id(),
                timestamp="",
                custom_type=custom_type,
                details=details,
            )
        )

    async def append_custom_message_entry(
        self,
        custom_type: str,
        content: str | list[Any],
        display: bool,
        details: Any = None,
    ) -> str:
        """Append a custom message entry."""
        return await self._append_typed_entry(
            CustomMessageEntry(
                id=await self._storage.create_entry_id(),
                parent_id=await self._storage.get_leaf_id(),
                timestamp="",
                custom_type=custom_type,
                content=content,
                display=display,
                details=details,
            )
        )

    async def append_label(self, target_id: str, label: str | None) -> str:
        """Append a label entry."""
        entry = await self._storage.get_entry(target_id)
        if entry is None:
            raise ValueError(f"Entry {target_id} not found")
        return await self._append_typed_entry(
            LabelEntry(
                id=await self._storage.create_entry_id(),
                parent_id=await self._storage.get_leaf_id(),
                timestamp="",
                target_id=target_id,
                label=label,
            )
        )

    async def append_session_name(self, name: str) -> str:
        """Append a session info entry with name."""
        return await self._append_typed_entry(
            SessionInfoEntry(
                id=await self._storage.create_entry_id(),
                parent_id=await self._storage.get_leaf_id(),
                timestamp="",
                name=name.strip(),
            )
        )

    async def move_to(
        self,
        entry_id: str | None,
        summary: dict[str, Any] | None = None,
    ) -> str | None:
        """Move the leaf to a given entry ID."""
        if entry_id is not None:
            entry = await self._storage.get_entry(entry_id)
            if entry is None:
                raise ValueError(f"Entry {entry_id} not found")
        await self._storage.set_leaf_id(entry_id)
        if summary is None:
            return None
        return await self._append_typed_entry(
            BranchSummaryEntry(
                id=await self._storage.create_entry_id(),
                parent_id=entry_id,
                timestamp="",
                from_id=entry_id or "root",
                summary=summary.get("summary", ""),
                details=summary.get("details"),
                from_hook=bool(summary.get("from_hook")),
            )
        )

"""Session storage implementations.

Mechanical port of hoocode's ``packages/agent/src/harness/session/storage/``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from cortex.agent.harness.types import (
    JsonlSessionMetadata,
    LabelEntry,
    SessionMetadata,
    SessionStorage,
    SessionTreeEntry,
)


def _update_label_cache(labels_by_id: dict[str, str], entry: SessionTreeEntry) -> None:
    """Update the label cache for an entry."""
    if not isinstance(entry, LabelEntry):
        return
    label = entry.label.strip() if entry.label else ""
    if label:
        labels_by_id[entry.target_id] = label
    else:
        labels_by_id.pop(entry.target_id, None)


def _build_labels_by_id(entries: list[SessionTreeEntry]) -> dict[str, str]:
    """Build a map of entry IDs to labels."""
    labels_by_id: dict[str, str] = {}
    for entry in entries:
        _update_label_cache(labels_by_id, entry)
    return labels_by_id


def _generate_entry_id(by_id: dict[str, Any]) -> str:
    """Generate a unique entry ID."""
    for _ in range(100):
        entry_id = uuid.uuid4().hex[:8]
        if entry_id not in by_id:
            return entry_id
    return str(uuid.uuid4())


class InMemorySessionStorage(SessionStorage):
    """In-memory session storage implementation."""

    def __init__(
        self,
        entries: list[SessionTreeEntry] | None = None,
        leaf_id: str | None = None,
        metadata: SessionMetadata | None = None,
    ) -> None:
        self._entries = list(entries) if entries else []
        self._by_id: dict[str, SessionTreeEntry] = {e.id: e for e in self._entries}
        self._labels_by_id = _build_labels_by_id(self._entries)
        if leaf_id is not None:
            if leaf_id not in self._by_id:
                raise ValueError(f"Entry {leaf_id} not found")
            self._leaf_id = leaf_id
        else:
            self._leaf_id = self._entries[-1].id if self._entries else None
        self._metadata = metadata or SessionMetadata(
            id=str(uuid.uuid4()),
            created_at=datetime.now(UTC).isoformat(),
        )

    async def get_metadata(self) -> SessionMetadata:
        """Get session metadata."""
        return self._metadata

    async def get_leaf_id(self) -> str | None:
        """Get the current leaf entry ID."""
        return self._leaf_id

    async def set_leaf_id(self, leaf_id: str | None) -> None:
        """Set the current leaf entry ID."""
        if leaf_id is not None and leaf_id not in self._by_id:
            raise ValueError(f"Entry {leaf_id} not found")
        self._leaf_id = leaf_id

    async def create_entry_id(self) -> str:
        """Create a unique entry ID."""
        return _generate_entry_id(self._by_id)

    async def append_entry(self, entry: SessionTreeEntry) -> None:
        """Append an entry to storage."""
        self._entries.append(entry)
        self._by_id[entry.id] = entry
        _update_label_cache(self._labels_by_id, entry)
        self._leaf_id = entry.id

    async def get_entry(self, id: str) -> SessionTreeEntry | None:
        """Get an entry by ID."""
        return self._by_id.get(id)

    async def find_entries(self, type: str) -> list[SessionTreeEntry]:
        """Find entries by type."""
        return [e for e in self._entries if e.type == type]

    async def get_label(self, id: str) -> str | None:
        """Get label for an entry."""
        return self._labels_by_id.get(id)

    async def get_path_to_root(self, leaf_id: str | None) -> list[SessionTreeEntry]:
        """Get the path from leaf to root."""
        if leaf_id is None:
            return []
        path: list[SessionTreeEntry] = []
        current = self._by_id.get(leaf_id)
        while current:
            path.insert(0, current)
            current = self._by_id.get(current.parent_id) if current.parent_id else None
        return path

    async def get_entries(self) -> list[SessionTreeEntry]:
        """Get all entries."""
        return list(self._entries)


class JsonlSessionStorage(SessionStorage):
    """JSONL file-based session storage implementation."""

    def __init__(
        self,
        file_path: str,
        header: dict[str, Any],
        entries: list[SessionTreeEntry],
        leaf_id: str | None,
    ) -> None:
        from pathlib import Path

        self._file_path = str(Path(file_path).resolve())
        self._header = header
        self._metadata = JsonlSessionMetadata(
            id=header["id"],
            created_at=header["timestamp"],
            cwd=header.get("cwd", ""),
            path=str(Path(file_path).resolve()),
            parent_session_path=header.get("parentSession"),
        )
        self._entries = entries
        self._by_id: dict[str, SessionTreeEntry] = {e.id: e for e in entries}
        self._labels_by_id = _build_labels_by_id(entries)
        self._leaf_id = leaf_id

    @classmethod
    async def open(cls, file_path: str) -> JsonlSessionStorage:
        """Open an existing JSONL session file."""
        from pathlib import Path

        resolved_path = str(Path(file_path).resolve())
        content = Path(resolved_path).read_text(encoding="utf-8")
        lines = [line for line in content.split("\n") if line.strip()]

        if not lines:
            raise ValueError(f"Invalid JSONL session file {resolved_path}: missing session header")

        import json

        try:
            header = json.loads(lines[0])
        except json.JSONDecodeError as err:
            raise ValueError(
                f"Invalid JSONL session file {resolved_path}:"
                " first line is not a valid session header"
            ) from err

        entries: list[SessionTreeEntry] = []
        leaf_id: str | None = None
        for line in lines[1:]:
            try:
                entry_data = json.loads(line)
                # Convert dict to appropriate entry type
                entry = _dict_to_entry(entry_data)
                entries.append(entry)
                leaf_id = entry.id
            except (json.JSONDecodeError, ValueError):
                # Ignore malformed entry lines
                pass

        return cls(resolved_path, header, entries, leaf_id)

    @classmethod
    async def create(
        cls,
        file_path: str,
        options: dict[str, Any],
    ) -> JsonlSessionStorage:
        """Create a new JSONL session file."""
        import json
        from pathlib import Path

        resolved_path = str(Path(file_path).resolve())
        header = {
            "type": "session",
            "version": 3,
            "id": options["session_id"],
            "timestamp": datetime.now(UTC).isoformat(),
            "cwd": options["cwd"],
            "parentSession": options.get("parent_session_path"),
        }
        Path(resolved_path).parent.mkdir(parents=True, exist_ok=True)
        Path(resolved_path).write_text(f"{json.dumps(header)}\n", encoding="utf-8")
        return cls(resolved_path, header, [], None)

    async def get_metadata(self) -> SessionMetadata:
        """Get session metadata."""
        return self._metadata

    async def get_leaf_id(self) -> str | None:
        """Get the current leaf entry ID."""
        return self._leaf_id

    async def set_leaf_id(self, leaf_id: str | None) -> None:
        """Set the current leaf entry ID."""
        if leaf_id is not None and leaf_id not in self._by_id:
            raise ValueError(f"Entry {leaf_id} not found")
        self._leaf_id = leaf_id

    async def create_entry_id(self) -> str:
        """Create a unique entry ID."""
        return _generate_entry_id(self._by_id)

    async def append_entry(self, entry: SessionTreeEntry) -> None:
        """Append an entry to storage."""
        import json
        from pathlib import Path

        # Read existing content and append the new entry
        existing_content = Path(self._file_path).read_text(encoding="utf-8")
        new_line = f"{json.dumps(_entry_to_dict(entry))}\n"
        Path(self._file_path).write_text(existing_content + new_line, encoding="utf-8")
        self._entries.append(entry)
        self._by_id[entry.id] = entry
        _update_label_cache(self._labels_by_id, entry)
        self._leaf_id = entry.id

    async def get_entry(self, id: str) -> SessionTreeEntry | None:
        """Get an entry by ID."""
        return self._by_id.get(id)

    async def find_entries(self, type: str) -> list[SessionTreeEntry]:
        """Find entries by type."""
        return [e for e in self._entries if e.type == type]

    async def get_label(self, id: str) -> str | None:
        """Get label for an entry."""
        return self._labels_by_id.get(id)

    async def get_path_to_root(self, leaf_id: str | None) -> list[SessionTreeEntry]:
        """Get the path from leaf to root."""
        if leaf_id is None:
            return []
        path: list[SessionTreeEntry] = []
        current = self._by_id.get(leaf_id)
        while current:
            path.insert(0, current)
            current = self._by_id.get(current.parent_id) if current.parent_id else None
        return path

    async def get_entries(self) -> list[SessionTreeEntry]:
        """Get all entries."""
        return list(self._entries)


def _dict_to_entry(data: dict[str, Any]) -> SessionTreeEntry:
    """Convert a dict to a SessionTreeEntry."""
    from cortex.agent.harness.types import (
        BranchSummaryEntry,
        CompactionEntry,
        CustomEntry,
        CustomMessageEntry,
        LabelEntry,
        MessageEntry,
        ModelChangeEntry,
        SessionInfoEntry,
        ThinkingLevelChangeEntry,
    )

    entry_type = data.get("type", "")
    if entry_type == "message":
        return MessageEntry(**data)
    elif entry_type == "thinking_level_change":
        return ThinkingLevelChangeEntry(**data)
    elif entry_type == "model_change":
        return ModelChangeEntry(**data)
    elif entry_type == "compaction":
        return CompactionEntry(**data)
    elif entry_type == "custom":
        return CustomEntry(**data)
    elif entry_type == "custom_message":
        return CustomMessageEntry(**data)
    elif entry_type == "label":
        return LabelEntry(**data)
    elif entry_type == "session_info":
        return SessionInfoEntry(**data)
    elif entry_type == "branch_summary":
        return BranchSummaryEntry(**data)
    else:
        raise ValueError(f"Unknown entry type: {entry_type}")


def _entry_to_dict(entry: SessionTreeEntry) -> dict[str, Any]:
    """Convert a SessionTreeEntry to a dict."""
    from dataclasses import asdict

    return asdict(entry)

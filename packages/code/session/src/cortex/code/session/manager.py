# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnusedCallResult=false, reportCallIssue=false, reportReturnType=false, reportMissingTypeArgument=false, reportUnknownLambdaType=false, reportUnnecessaryIsInstance=false, reportUnusedVariable=false
"""Session manager for coding agent sessions.

Port of ``session-manager.ts`` from ``packages/coding-agent/src/core/``.
Manages conversation sessions as append-only trees stored in JSONL files.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CURRENT_SESSION_VERSION = 3


@dataclass
class SessionHeader:
    """Session file header."""

    type: str = "session"
    version: int = CURRENT_SESSION_VERSION
    id: str = ""
    timestamp: str = ""
    cwd: str = ""
    parent_session: str | None = None


@dataclass
class SessionEntry:
    """Base session entry with tree structure."""

    type: str
    id: str = ""
    parent_id: str | None = None
    timestamp: str = ""


@dataclass
class MessageEntry(SessionEntry):
    """Message entry in session."""

    message: dict[str, Any] = field(default_factory=dict)


@dataclass
class ThinkingLevelChangeEntry(SessionEntry):
    """Thinking level change entry."""

    thinking_level: str = ""


@dataclass
class ModelChangeEntry(SessionEntry):
    """Model change entry."""

    provider: str = ""
    model_id: str = ""


@dataclass
class CompactionEntry(SessionEntry):
    """Compaction entry."""

    summary: str = ""
    first_kept_entry_id: str = ""
    tokens_before: int = 0
    tokens_after: int | None = None
    details: Any = None
    from_hook: bool = False


@dataclass
class CustomEntry(SessionEntry):
    """Custom entry for extensions."""

    custom_type: str = ""
    data: Any = None


@dataclass
class SessionInfoEntry(SessionEntry):
    """Session info entry (e.g., display name)."""

    name: str = ""


@dataclass
class LabelEntry(SessionEntry):
    """Label entry for bookmarking/navigation."""

    target_id: str = ""
    label: str | None = None


@dataclass
class BranchSummaryEntry(SessionEntry):
    """Branch summary entry."""

    from_id: str = ""
    summary: str = ""
    details: Any = None
    from_hook: bool = False


@dataclass
class CustomMessageEntry(SessionEntry):
    """Custom message entry for extensions."""

    custom_type: str = ""
    content: str | list[dict[str, Any]] = ""
    display: bool = True
    details: Any = None


@dataclass
class SessionInfo:
    """Session information for listing."""

    path: str
    id: str
    cwd: str
    name: str | None = None
    parent_session_path: str | None = None
    created: datetime | None = None
    modified: datetime | None = None
    message_count: int = 0
    first_message: str = ""
    all_messages_text: str = ""


@dataclass
class SessionTreeNode:
    """Tree node for getTree()."""

    entry: SessionEntry
    children: list[SessionTreeNode] = field(default_factory=list)
    label: str | None = None
    label_timestamp: str | None = None


def _generate_id(existing_ids: set[str]) -> str:
    """Generate a unique short ID (8 hex chars)."""
    for _ in range(100):
        id_str = uuid.uuid4().hex[:8]
        if id_str not in existing_ids:
            return id_str
    return str(uuid.uuid4())


def _parse_session_entries(content: str) -> list[dict[str, Any]]:
    """Parse session entries from JSONL content."""
    entries = []
    for line in content.strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            entries.append(entry)
        except json.JSONDecodeError:
            # Skip malformed lines
            pass
    return entries


def _load_entries_from_file(file_path: str) -> list[dict[str, Any]]:
    """Load session entries from a JSONL file."""
    if not os.path.exists(file_path):
        return []

    content = Path(file_path).read_text(encoding="utf-8")
    entries = _parse_session_entries(content)

    # Validate session header
    if entries:
        header = entries[0]
        if header.get("type") != "session" or not isinstance(header.get("id"), str):
            return []

    return entries


def _migrate_v1_to_v2(entries: list[dict[str, Any]]) -> None:
    """Migrate v1 to v2: add id/parentId tree structure."""
    ids: set[str] = set()
    prev_id: str | None = None

    for entry in entries:
        if entry.get("type") == "session":
            entry["version"] = 2
            continue

        entry["id"] = _generate_id(ids)
        entry["parentId"] = prev_id
        prev_id = entry["id"]

        # Convert firstKeptEntryIndex to firstKeptEntryId for compaction
        if entry.get("type") == "compaction":
            if "firstKeptEntryIndex" in entry:
                idx = entry["firstKeptEntryIndex"]
                target_entry = entries[idx] if idx < len(entries) else None
                if target_entry and target_entry.get("type") != "session":
                    entry["firstKeptEntryId"] = target_entry.get("id")
                del entry["firstKeptEntryIndex"]


def _migrate_v2_to_v3(entries: list[dict[str, Any]]) -> None:
    """Migrate v2 to v3: rename hookMessage role to custom."""
    for entry in entries:
        if entry.get("type") == "session":
            entry["version"] = 3
            continue

        if entry.get("type") == "message":
            message = entry.get("message", {})
            if message.get("role") == "hookMessage":
                message["role"] = "custom"


def _migrate_to_current_version(entries: list[dict[str, Any]]) -> bool:
    """Run all necessary migrations. Returns True if any migration was applied."""
    header = next((e for e in entries if e.get("type") == "session"), None)
    version = header.get("version", 1) if header else 1

    if version >= CURRENT_SESSION_VERSION:
        return False

    if version < 2:
        _migrate_v1_to_v2(entries)
    if version < 3:
        _migrate_v2_to_v3(entries)

    return True


def get_latest_compaction_entry(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Get the latest compaction entry from entries."""
    for i in range(len(entries) - 1, -1, -1):
        if entries[i].get("type") == "compaction":
            return entries[i]
    return None


def get_default_session_dir(cwd: str, agent_dir: str | None = None) -> str:
    """Compute the default session directory for a cwd."""
    if agent_dir is None:
        agent_dir = os.path.expanduser("~/.hoocode/agent")

    stripped = cwd.lstrip("/").lstrip("\\")
    safe_path = "--" + stripped.replace("/", "-").replace("\\", "-").replace(":", "-") + "--"
    session_dir = os.path.join(agent_dir, "sessions", safe_path)
    os.makedirs(session_dir, exist_ok=True)
    return session_dir


def _is_valid_session_file(file_path: str) -> bool:
    """Check if a file is a valid session file."""
    try:
        with open(file_path) as f:
            first_line = f.readline()
            if not first_line:
                return False
            header = json.loads(first_line)
            return header.get("type") == "session" and isinstance(header.get("id"), str)
    except (json.JSONDecodeError, OSError):
        return False


def find_most_recent_session(session_dir: str) -> str | None:
    """Find the most recent session file in a directory."""
    try:
        files = []
        for f in os.listdir(session_dir):
            if f.endswith(".jsonl"):
                file_path = os.path.join(session_dir, f)
                if _is_valid_session_file(file_path):
                    mtime = os.path.getmtime(file_path)
                    files.append((file_path, mtime))
        files.sort(key=lambda x: x[1], reverse=True)
        return files[0][0] if files else None
    except OSError:
        return None


class SessionManager:
    """Manages conversation sessions as append-only trees stored in JSONL files.

    Each session entry has an id and parentId forming a tree structure. The "leaf"
    pointer tracks the current position. Appending creates a child of the current leaf.
    Branching moves the leaf to an earlier entry, allowing new branches without
    modifying history.
    """

    def __init__(
        self, cwd: str, session_dir: str, session_file: str | None = None, persist: bool = True
    ) -> None:
        self._session_id = ""
        self._session_file: str | None = None
        self._session_dir = session_dir
        self._cwd = cwd
        self._persist = persist
        self._flushed = False
        self._file_entries: list[dict[str, Any]] = []
        self._by_id: dict[str, dict[str, Any]] = {}
        self._labels_by_id: dict[str, str] = {}
        self._label_timestamps_by_id: dict[str, str] = {}
        self._leaf_id: str | None = None

        if persist and session_dir and not os.path.exists(session_dir):
            os.makedirs(session_dir, exist_ok=True)

        if session_file:
            self._set_session_file(session_file)
        else:
            self._new_session()

    def _set_session_file(self, session_file: str) -> None:
        """Switch to a different session file."""
        self._session_file = os.path.abspath(session_file)
        if os.path.exists(self._session_file):
            self._file_entries = _load_entries_from_file(self._session_file)

            if not self._file_entries:
                # Empty or corrupted file - start fresh
                explicit_path = self._session_file
                self._new_session()
                self._session_file = explicit_path
                self._rewrite_file()
                self._flushed = True
                return

            header = next((e for e in self._file_entries if e.get("type") == "session"), None)
            self._session_id = header.get("id", "") if header else ""

            if _migrate_to_current_version(self._file_entries):
                self._rewrite_file()

            self._build_index()
            self._flushed = True
        else:
            explicit_path = self._session_file
            self._new_session()
            self._session_file = explicit_path

    def _new_session(self, id: str | None = None, parent_session: str | None = None) -> str | None:
        """Create a new session."""
        self._session_id = id or str(uuid.uuid4())
        timestamp = datetime.now(UTC).isoformat()
        header = {
            "type": "session",
            "version": CURRENT_SESSION_VERSION,
            "id": self._session_id,
            "timestamp": timestamp,
            "cwd": self._cwd,
            "parentSession": parent_session,
        }
        self._file_entries = [header]
        self._by_id.clear()
        self._labels_by_id.clear()
        self._label_timestamps_by_id.clear()
        self._leaf_id = None
        self._flushed = False

        if self._persist:
            file_timestamp = timestamp.replace(":", "-").replace(".", "-")
            self._session_file = os.path.join(
                self._session_dir, f"{file_timestamp}_{self._session_id}.jsonl"
            )
        return self._session_file

    def _build_index(self) -> None:
        """Build index from file entries."""
        self._by_id.clear()
        self._labels_by_id.clear()
        self._label_timestamps_by_id.clear()
        self._leaf_id = None
        for entry in self._file_entries:
            if entry.get("type") == "session":
                continue
            entry_id = entry.get("id", "")
            self._by_id[entry_id] = entry
            self._leaf_id = entry_id
            if entry.get("type") == "label":
                target_id = entry.get("targetId", "")
                label = entry.get("label")
                if label:
                    self._labels_by_id[target_id] = label
                    self._label_timestamps_by_id[target_id] = entry.get("timestamp", "")
                else:
                    self._labels_by_id.pop(target_id, None)
                    self._label_timestamps_by_id.pop(target_id, None)

    def _rewrite_file(self) -> None:
        """Rewrite the entire session file."""
        if not self._persist or not self._session_file:
            return
        content = "\n".join(json.dumps(e) for e in self._file_entries) + "\n"
        Path(self._session_file).write_text(content, encoding="utf-8")

    def is_persisted(self) -> bool:
        """Check if session is persisted to disk."""
        return self._persist

    def get_cwd(self) -> str:
        """Get the session working directory."""
        return self._cwd

    def get_session_dir(self) -> str:
        """Get the session directory."""
        return self._session_dir

    def get_session_id(self) -> str:
        """Get the session ID."""
        return self._session_id

    def get_session_file(self) -> str | None:
        """Get the session file path."""
        return self._session_file

    def _persist_entry(self, entry: dict[str, Any]) -> None:
        """Persist an entry to disk."""
        if not self._persist or not self._session_file:
            return

        has_assistant = any(
            e.get("type") == "message" and e.get("message", {}).get("role") == "assistant"
            for e in self._file_entries
        )
        if not has_assistant:
            self._flushed = False
            return

        if not self._flushed:
            with open(self._session_file, "a") as f:
                for e in self._file_entries:
                    f.write(json.dumps(e) + "\n")
            self._flushed = True
        else:
            with open(self._session_file, "a") as f:
                f.write(json.dumps(entry) + "\n")

    def _append_entry(self, entry: dict[str, Any]) -> None:
        """Append an entry to the session."""
        self._file_entries.append(entry)
        entry_id = entry.get("id", "")
        self._by_id[entry_id] = entry
        self._leaf_id = entry_id
        self._persist_entry(entry)

    def append_message(self, message: dict[str, Any]) -> str:
        """Append a message entry. Returns entry id."""
        entry = {
            "type": "message",
            "id": _generate_id(set(self._by_id.keys())),
            "parentId": self._leaf_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "message": message,
        }
        self._append_entry(entry)
        return entry["id"]

    def append_thinking_level_change(self, thinking_level: str) -> str:
        """Append a thinking level change. Returns entry id."""
        entry = {
            "type": "thinking_level_change",
            "id": _generate_id(set(self._by_id.keys())),
            "parentId": self._leaf_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "thinkingLevel": thinking_level,
        }
        self._append_entry(entry)
        return entry["id"]

    def append_model_change(self, provider: str, model_id: str) -> str:
        """Append a model change. Returns entry id."""
        entry = {
            "type": "model_change",
            "id": _generate_id(set(self._by_id.keys())),
            "parentId": self._leaf_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "provider": provider,
            "modelId": model_id,
        }
        self._append_entry(entry)
        return entry["id"]

    def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Any = None,
        from_hook: bool = False,
        tokens_after: int | None = None,
    ) -> str:
        """Append a compaction entry. Returns entry id."""
        entry = {
            "type": "compaction",
            "id": _generate_id(set(self._by_id.keys())),
            "parentId": self._leaf_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "summary": summary,
            "firstKeptEntryId": first_kept_entry_id,
            "tokensBefore": tokens_before,
            "tokensAfter": tokens_after,
            "details": details,
            "fromHook": from_hook,
        }
        self._append_entry(entry)
        return entry["id"]

    def append_custom_entry(self, custom_type: str, data: Any = None) -> str:
        """Append a custom entry. Returns entry id."""
        entry = {
            "type": "custom",
            "customType": custom_type,
            "data": data,
            "id": _generate_id(set(self._by_id.keys())),
            "parentId": self._leaf_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self._append_entry(entry)
        return entry["id"]

    def append_session_info(self, name: str) -> str:
        """Append a session info entry. Returns entry id."""
        entry = {
            "type": "session_info",
            "id": _generate_id(set(self._by_id.keys())),
            "parentId": self._leaf_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "name": name.strip(),
        }
        self._append_entry(entry)
        return entry["id"]

    def append_custom_message_entry(
        self,
        custom_type: str,
        content: str | list[dict[str, Any]],
        display: bool,
        details: Any = None,
    ) -> str:
        """Append a custom message entry. Returns entry id."""
        entry = {
            "type": "custom_message",
            "customType": custom_type,
            "content": content,
            "display": display,
            "details": details,
            "id": _generate_id(set(self._by_id.keys())),
            "parentId": self._leaf_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self._append_entry(entry)
        return entry["id"]

    def append_label_change(self, target_id: str, label: str | None) -> str:
        """Set or clear a label on an entry. Returns entry id."""
        if target_id not in self._by_id:
            raise ValueError(f"Entry {target_id} not found")

        entry = {
            "type": "label",
            "id": _generate_id(set(self._by_id.keys())),
            "parentId": self._leaf_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "targetId": target_id,
            "label": label,
        }
        self._append_entry(entry)
        if label:
            self._labels_by_id[target_id] = label
            self._label_timestamps_by_id[target_id] = entry["timestamp"]
        else:
            self._labels_by_id.pop(target_id, None)
            self._label_timestamps_by_id.pop(target_id, None)
        return entry["id"]

    def get_leaf_id(self) -> str | None:
        """Get the current leaf entry ID."""
        return self._leaf_id

    def get_leaf_entry(self) -> dict[str, Any] | None:
        """Get the current leaf entry."""
        if self._leaf_id:
            return self._by_id.get(self._leaf_id)
        return None

    def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        """Get an entry by ID."""
        return self._by_id.get(entry_id)

    def get_children(self, parent_id: str) -> list[dict[str, Any]]:
        """Get all direct children of an entry."""
        return [entry for entry in self._by_id.values() if entry.get("parentId") == parent_id]

    def get_label(self, entry_id: str) -> str | None:
        """Get the label for an entry."""
        return self._labels_by_id.get(entry_id)

    def get_session_name(self) -> str | None:
        """Get the current session name from the latest session_info entry."""
        entries = self.get_entries()
        for entry in reversed(entries):
            if entry.get("type") == "session_info":
                name = entry.get("name", "").strip()
                return name if name else None
        return None

    def get_branch(self, from_id: str | None = None) -> list[dict[str, Any]]:
        """Walk from entry to root, returning all entries in path order."""
        path = []
        start_id = from_id if from_id is not None else self._leaf_id
        current = self._by_id.get(start_id) if start_id else None

        while current:
            path.insert(0, current)
            parent_id = current.get("parentId")
            current = self._by_id.get(parent_id) if parent_id else None

        return path

    def get_entries(self) -> list[dict[str, Any]]:
        """Get all session entries (excludes header)."""
        return [e for e in self._file_entries if e.get("type") != "session"]

    def get_header(self) -> dict[str, Any] | None:
        """Get session header."""
        return next((e for e in self._file_entries if e.get("type") == "session"), None)

    def get_tree(self) -> list[SessionTreeNode]:
        """Get the session as a tree structure."""
        entries = self.get_entries()
        node_map: dict[str, SessionTreeNode] = {}
        roots: list[SessionTreeNode] = []

        # Create nodes
        for entry in entries:
            entry_id = entry.get("id", "")
            label = self._labels_by_id.get(entry_id)
            label_ts = self._label_timestamps_by_id.get(entry_id)
            node_map[entry_id] = SessionTreeNode(
                entry=entry,
                children=[],
                label=label,
                label_timestamp=label_ts,
            )

        # Build tree
        for entry in entries:
            node = node_map.get(entry.get("id", ""))
            if not node:
                continue
            parent_id = entry.get("parentId")
            if parent_id is None or parent_id == entry.get("id"):
                roots.append(node)
            else:
                parent = node_map.get(parent_id)
                if parent:
                    parent.children.append(node)
                else:
                    roots.append(node)

        # Sort children by timestamp
        def timestamp_ms(node: SessionTreeNode) -> float:
            ts = node.entry.get("timestamp", "")
            if ts:
                try:
                    return datetime.fromisoformat(ts).timestamp()
                except ValueError:
                    return 0.0
            return 0.0

        def sort_tree(nodes: list[SessionTreeNode]) -> None:
            nodes.sort(key=timestamp_ms)
            for node in nodes:
                sort_tree(node.children)

        sort_tree(roots)
        return roots

    def branch(self, branch_from_id: str) -> None:
        """Start a new branch from an earlier entry."""
        if branch_from_id not in self._by_id:
            raise ValueError(f"Entry {branch_from_id} not found")
        self._leaf_id = branch_from_id

    def reset_leaf(self) -> None:
        """Reset the leaf pointer to null."""
        self._leaf_id = None

    def branch_with_summary(
        self,
        branch_from_id: str | None,
        summary: str,
        details: Any = None,
        from_hook: bool = False,
    ) -> str:
        """Start a new branch with a summary of the abandoned path."""
        if branch_from_id is not None and branch_from_id not in self._by_id:
            raise ValueError(f"Entry {branch_from_id} not found")

        self._leaf_id = branch_from_id
        entry = {
            "type": "branch_summary",
            "id": _generate_id(set(self._by_id.keys())),
            "parentId": branch_from_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "fromId": branch_from_id or "root",
            "summary": summary,
            "details": details,
            "fromHook": from_hook,
        }
        self._append_entry(entry)
        return entry["id"]

    def create_branched_session(self, leaf_id: str) -> str | None:
        """Create a new session file containing only the path to the specified leaf."""
        previous_session_file = self._session_file
        path = self.get_branch(leaf_id)
        if not path:
            raise ValueError(f"Entry {leaf_id} not found")

        # Filter out label entries
        path_without_labels = [e for e in path if e.get("type") != "label"]

        new_session_id = str(uuid.uuid4())
        timestamp = datetime.now(UTC).isoformat()
        file_timestamp = timestamp.replace(":", "-").replace(".", "-")
        new_session_file = os.path.join(
            self._session_dir, f"{file_timestamp}_{new_session_id}.jsonl"
        )

        header = {
            "type": "session",
            "version": CURRENT_SESSION_VERSION,
            "id": new_session_id,
            "timestamp": timestamp,
            "cwd": self._cwd,
            "parentSession": previous_session_file if self._persist else None,
        }

        # Collect labels for entries in the path
        path_entry_ids = {e.get("id") for e in path_without_labels}
        labels_to_write = []
        for target_id, label in self._labels_by_id.items():
            if target_id in path_entry_ids:
                labels_to_write.append(
                    {
                        "targetId": target_id,
                        "label": label,
                        "timestamp": self._label_timestamps_by_id.get(target_id, ""),
                    }
                )

        if self._persist:
            # Build label entries
            last_entry_id = path_without_labels[-1].get("id") if path_without_labels else None
            parent_id = last_entry_id
            label_entries = []
            for item in labels_to_write:
                label_entry = {
                    "type": "label",
                    "id": _generate_id(path_entry_ids),
                    "parentId": parent_id,
                    "timestamp": item["timestamp"],
                    "targetId": item["targetId"],
                    "label": item["label"],
                }
                path_entry_ids.add(label_entry["id"])
                label_entries.append(label_entry)
                parent_id = label_entry["id"]

            self._file_entries = [header, *path_without_labels, *label_entries]
            self._session_id = new_session_id
            self._session_file = new_session_file
            self._build_index()

            has_assistant = any(
                e.get("type") == "message" and e.get("message", {}).get("role") == "assistant"
                for e in self._file_entries
            )
            if has_assistant:
                self._rewrite_file()
                self._flushed = True
            else:
                self._flushed = False

            return new_session_file

        # In-memory mode
        label_entries = []
        parent_id = path_without_labels[-1].get("id") if path_without_labels else None
        used_ids = set(path_entry_ids)
        for item in labels_to_write:
            label_entry = {
                "type": "label",
                "id": _generate_id(used_ids),
                "parentId": parent_id,
                "timestamp": item["timestamp"],
                "targetId": item["targetId"],
                "label": item["label"],
            }
            used_ids.add(label_entry["id"])
            label_entries.append(label_entry)
            parent_id = label_entry["id"]

        self._file_entries = [header, *path_without_labels, *label_entries]
        self._session_id = new_session_id
        self._build_index()
        return None

    @classmethod
    def create(cls, cwd: str, session_dir: str | None = None) -> SessionManager:
        """Create a new session."""
        dir_path = session_dir or get_default_session_dir(cwd)
        return cls(cwd, dir_path, None, True)

    @classmethod
    def open(
        cls, path: str, session_dir: str | None = None, cwd_override: str | None = None
    ) -> SessionManager:
        """Open a specific session file."""
        entries = _load_entries_from_file(path)
        header = next((e for e in entries if e.get("type") == "session"), None)
        cwd = cwd_override or (header.get("cwd", "") if header else "") or os.getcwd()
        dir_path = session_dir or str(Path(path).parent)
        return cls(cwd, dir_path, path, True)

    @classmethod
    def continue_recent(cls, cwd: str, session_dir: str | None = None) -> SessionManager:
        """Continue the most recent session, or create new if none."""
        dir_path = session_dir or get_default_session_dir(cwd)
        most_recent = find_most_recent_session(dir_path)
        if most_recent:
            return cls(cwd, dir_path, most_recent, True)
        return cls(cwd, dir_path, None, True)

    @classmethod
    def in_memory(cls, cwd: str | None = None) -> SessionManager:
        """Create an in-memory session (no file persistence)."""
        return cls(cwd or os.getcwd(), "", None, False)

    @classmethod
    def fork_from(
        cls, source_path: str, target_cwd: str, session_dir: str | None = None
    ) -> SessionManager:
        """Fork a session from another project directory."""
        source_entries = _load_entries_from_file(source_path)
        if not source_entries:
            raise ValueError(f"Cannot fork: source session file is empty or invalid: {source_path}")

        source_header = next((e for e in source_entries if e.get("type") == "session"), None)
        if not source_header:
            raise ValueError(f"Cannot fork: source session has no header: {source_path}")

        dir_path = session_dir or get_default_session_dir(target_cwd)
        os.makedirs(dir_path, exist_ok=True)

        new_session_id = str(uuid.uuid4())
        timestamp = datetime.now(UTC).isoformat()
        file_timestamp = timestamp.replace(":", "-").replace(".", "-")
        new_session_file = os.path.join(dir_path, f"{file_timestamp}_{new_session_id}.jsonl")

        new_header = {
            "type": "session",
            "version": CURRENT_SESSION_VERSION,
            "id": new_session_id,
            "timestamp": timestamp,
            "cwd": target_cwd,
            "parentSession": source_path,
        }

        with open(new_session_file, "w") as f:
            f.write(json.dumps(new_header) + "\n")
            for entry in source_entries:
                if entry.get("type") != "session":
                    f.write(json.dumps(entry) + "\n")

        return cls(target_cwd, dir_path, new_session_file, True)

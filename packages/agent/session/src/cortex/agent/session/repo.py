# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportCallIssue=false
"""Session repository implementations.

Mechanical port of hoocode's ``packages/agent/src/harness/session/repo/``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cortex.agent.harness.types import (
    JsonlSessionMetadata,
    SessionMetadata,
    SessionStorage,
    SessionTreeEntry,
)

from .storage import InMemorySessionStorage, JsonlSessionStorage


def _create_session_id() -> str:
    """Create a new session ID (UUIDv7 in TS, UUID4 in Python)."""
    return str(uuid.uuid4())


def _create_timestamp() -> str:
    """Create a timestamp string."""
    return datetime.now(UTC).isoformat()


class InMemorySessionRepo:
    """In-memory session repository implementation."""

    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}

    async def create(self, options: dict[str, Any] | None = None) -> Any:
        """Create a new session."""
        options = options or {}
        metadata = SessionMetadata(
            id=options.get("id", _create_session_id()),
            created_at=_create_timestamp(),
        )
        storage = InMemorySessionStorage(metadata=metadata)
        session = _to_session(storage)
        self._sessions[metadata.id] = session
        return session

    async def open(self, metadata: SessionMetadata) -> Any:
        """Open an existing session."""
        session = self._sessions.get(metadata.id)
        if session is None:
            raise ValueError(f"Session not found: {metadata.id}")
        return session

    async def list(self, options: Any = None) -> list[SessionMetadata]:
        """List all sessions."""
        results = []
        for session in self._sessions.values():
            results.append(await session.get_metadata())
        return results

    async def delete(self, metadata: SessionMetadata) -> None:
        """Delete a session."""
        self._sessions.pop(metadata.id, None)

    async def fork(
        self,
        source_metadata: SessionMetadata,
        options: dict[str, Any] | None = None,
    ) -> Any:
        """Fork a session."""
        options = options or {}
        source = await self.open(source_metadata)
        forked_entries = await _get_entries_to_fork(source.get_storage(), options)
        metadata = SessionMetadata(
            id=options.get("id", _create_session_id()),
            created_at=_create_timestamp(),
        )
        leaf_id = forked_entries[-1].id if forked_entries else None
        storage = InMemorySessionStorage(metadata=metadata, entries=forked_entries, leaf_id=leaf_id)
        session = _to_session(storage)
        self._sessions[metadata.id] = session
        return session


class JsonlSessionRepo:
    """JSONL file-based session repository implementation."""

    def __init__(self, sessions_root: str) -> None:
        self._sessions_root = str(Path(sessions_root).resolve())

    def _get_session_dir(self, cwd: str) -> str:
        """Get the session directory for a given cwd."""
        encoded = (
            "--"
            + cwd.lstrip("/").lstrip("\\").replace("/", "-").replace("\\", "-").replace(":", "-")
            + "--"
        )
        return str(Path(self._sessions_root) / encoded)

    def _create_session_file_path(self, cwd: str, session_id: str, timestamp: str) -> str:
        """Create a session file path."""
        safe_timestamp = timestamp.replace(":", "-").replace(".", "-")
        return str(Path(self._get_session_dir(cwd)) / f"{safe_timestamp}_{session_id}.jsonl")

    async def create(self, options: dict[str, Any]) -> Any:
        """Create a new session."""
        Path(self._sessions_root).mkdir(parents=True, exist_ok=True)
        session_id = options.get("id", _create_session_id())
        created_at = _create_timestamp()
        file_path = self._create_session_file_path(options["cwd"], session_id, created_at)
        storage = await JsonlSessionStorage.create(
            file_path,
            {
                "cwd": options["cwd"],
                "session_id": session_id,
                "parent_session_path": options.get("parent_session_path"),
            },
        )
        return _to_session(storage)

    async def open(self, metadata: JsonlSessionMetadata) -> Any:
        """Open an existing session."""
        if not Path(metadata.path).exists():
            raise ValueError(f"Session not found: {metadata.path}")
        storage = await JsonlSessionStorage.open(metadata.path)
        return _to_session(storage)

    async def list(self, options: dict[str, Any] | None = None) -> list[JsonlSessionMetadata]:
        """List sessions."""
        options = options or {}
        if "cwd" in options:
            dirs = [self._get_session_dir(options["cwd"])]
        else:
            dirs = await self._list_session_dirs()
        sessions: list[JsonlSessionMetadata] = []
        for dir_path in dirs:
            if not Path(dir_path).exists():
                continue
            for file_path in Path(dir_path).glob("*.jsonl"):
                try:
                    storage = await JsonlSessionStorage.open(str(file_path))
                    metadata = await storage.get_metadata()
                    if isinstance(metadata, JsonlSessionMetadata):
                        sessions.append(metadata)
                except (ValueError, Exception):
                    # Ignore invalid session files
                    pass
        sessions.sort(key=lambda x: x.created_at, reverse=True)
        return sessions

    async def delete(self, metadata: JsonlSessionMetadata) -> None:
        """Delete a session."""
        path = Path(metadata.path)
        if path.exists():
            path.unlink()

    async def fork(
        self,
        source_metadata: JsonlSessionMetadata,
        options: dict[str, Any] | None = None,
    ) -> Any:
        """Fork a session."""
        options = options or {}
        source = await self.open(source_metadata)
        forked_entries = await _get_entries_to_fork(source.get_storage(), options)
        session_id = options.get("id", _create_session_id())
        created_at = _create_timestamp()
        storage = await JsonlSessionStorage.create(
            self._create_session_file_path(options["cwd"], session_id, created_at),
            {
                "cwd": options["cwd"],
                "session_id": session_id,
                "parent_session_path": options.get("parent_session_path", source_metadata.path),
            },
        )
        for entry in forked_entries:
            await storage.append_entry(entry)
        return _to_session(storage)

    async def _list_session_dirs(self) -> list[str]:
        """List all session directories."""
        root = Path(self._sessions_root)
        if not root.exists():
            return []
        return [str(p) for p in root.iterdir() if p.is_dir()]


def _to_session(storage: SessionStorage) -> Any:
    """Convert storage to a Session."""
    from . import Session

    return Session(storage)


async def _get_entries_to_fork(
    storage: SessionStorage,
    options: dict[str, Any],
) -> list[SessionTreeEntry]:
    """Get entries to fork from storage."""
    entry_id = options.get("entry_id")
    if not entry_id:
        return await storage.get_entries()

    target = await storage.get_entry(entry_id)
    if target is None:
        raise ValueError(f"Entry {entry_id} not found")

    position = options.get("position", "before")
    if position == "at":
        effective_leaf_id = target.id
    else:
        if target.type != "message":
            raise ValueError(f"Entry {entry_id} is not a user message")
        effective_leaf_id = target.parent_id

    return await storage.get_path_to_root(effective_leaf_id)


async def load_jsonl_session_metadata(file_path: str) -> JsonlSessionMetadata:
    """Load metadata from a JSONL session file."""
    import json
    from pathlib import Path

    resolved_path = str(Path(file_path).resolve())
    content = Path(resolved_path).read_text(encoding="utf-8")
    lines = [line for line in content.split("\n") if line.strip()]

    if not lines:
        raise ValueError(f"Invalid JSONL session file {resolved_path}: missing session header")

    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as err:
        raise ValueError(
            f"Invalid JSONL session file {resolved_path}: first line is not a valid session header"
        ) from err

    return JsonlSessionMetadata(
        id=header["id"],
        created_at=header["timestamp"],
        cwd=header.get("cwd", ""),
        path=resolved_path,
        parent_session_path=header.get("parentSession"),
    )

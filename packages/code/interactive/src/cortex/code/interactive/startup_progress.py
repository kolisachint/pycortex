"""Process-wide store for transient startup progress.

Port of ``core/startup-progress.ts``.

First-run downloads of the external tool binaries (fd, rg, the semantic-index
engine) and the semantic index build itself report here. Modeled on ``taskStore``
— a singleton the footer reads directly and re-renders on change — but
deliberately separate from it: this is short-lived startup status, not agent
work, so it must never render as a task-panel plan row. Entries are keyed so
several concurrent downloads each own a stable footer line; a caller removes its
key when the work settles and the line disappears.

The TS keeps this under ``core/``; it lives beside the footer here because the
footer is its only reader in the port — nothing has yet ported the downloads
(``main.ts``) or the index build (``core/embsearch/``) that fill it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "DownloadProgress",
    "ErrorProgress",
    "StartupProgress",
    "StartupProgressStore",
    "WorkProgress",
    "startup_progress",
]


@dataclass(frozen=True)
class DownloadProgress:
    """A file coming down the wire. ``total_bytes`` is ``None`` without a Content-Length."""

    key: str
    label: str
    received_bytes: int
    total_bytes: int | None
    kind: str = "download"


@dataclass(frozen=True)
class WorkProgress:
    """Countable work — ``done`` of ``total`` ``unit`` — such as an index build."""

    key: str
    label: str
    done: int
    total: int
    unit: str
    kind: str = "work"


@dataclass(frozen=True)
class ErrorProgress:
    """Work that failed; the footer shows the message dimmed until the key is dropped."""

    key: str
    label: str
    message: str
    kind: str = "error"


StartupProgress = DownloadProgress | WorkProgress | ErrorProgress

Listener = Callable[[], None]


class StartupProgressStore:
    def __init__(self) -> None:
        # A dict preserves insertion order, so lines stay in the order work
        # started (fd, rg, then the index) instead of jumping as byte counts
        # update.
        self._entries: dict[str, StartupProgress] = {}
        self._listeners: list[Listener] = []

    def set(self, entry: StartupProgress) -> None:
        """Insert or replace the entry for its key."""
        self._entries[entry.key] = entry
        self._emit()

    def remove(self, key: str) -> None:
        """Drop a key's line once its work settled (download done, index ready/skipped)."""
        if self._entries.pop(key, None) is not None:
            self._emit()

    def clear(self) -> None:
        """Wipe everything — called when the first user turn starts."""
        if not self._entries:
            return
        self._entries.clear()
        self._emit()

    def list(self) -> list[StartupProgress]:
        return list(self._entries.values())

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _emit(self) -> None:
        for listener in list(self._listeners):
            listener()


#: Shared, process-wide startup-progress store.
startup_progress = StartupProgressStore()

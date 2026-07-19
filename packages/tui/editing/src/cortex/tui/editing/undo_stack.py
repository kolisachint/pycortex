"""Generic undo stack with clone-on-push semantics.

Stores deep clones of state snapshots. Popped snapshots are returned
directly (no re-cloning) since they are already detached.
"""

from __future__ import annotations

import copy
from typing import Generic, TypeVar

T = TypeVar("T")


class UndoStack(Generic[T]):
    """Generic undo stack with clone-on-push semantics."""

    def __init__(self) -> None:
        self._stack: list[T] = []

    def push(self, state: T) -> None:
        """Push a deep clone of the given state onto the stack."""
        self._stack.append(copy.deepcopy(state))

    def pop(self) -> T | None:
        """Pop and return the most recent snapshot, or None if empty."""
        return self._stack.pop() if self._stack else None

    def clear(self) -> None:
        """Remove all snapshots."""
        self._stack.clear()

    @property
    def length(self) -> int:
        """Get the number of snapshots in the stack."""
        return len(self._stack)

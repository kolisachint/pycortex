"""Tests for `cortex.tui.editing` (port of kill-ring.ts and undo-stack.ts)."""

from __future__ import annotations

from cortex.tui.editing import KillRing, UndoStack

# =============================================================================
# KillRing tests (port of kill-ring.ts)
# =============================================================================


def test_kill_ring_push_basic() -> None:
    """Basic push adds text to the ring."""
    ring = KillRing()
    ring.push("hello", prepend=False)
    assert ring.peek() == "hello"
    assert ring.length == 1


def test_kill_ring_push_prepend() -> None:
    """Prepend option prepends text to the last entry."""
    ring = KillRing()
    ring.push("world", prepend=False)
    ring.push("hello ", prepend=True, accumulate=True)
    assert ring.peek() == "hello world"
    assert ring.length == 1


def test_kill_ring_push_append() -> None:
    """Append option appends text to the last entry."""
    ring = KillRing()
    ring.push("hello", prepend=False)
    ring.push(" world", prepend=False, accumulate=True)
    assert ring.peek() == "hello world"
    assert ring.length == 1


def test_kill_ring_push_no_accumulate() -> None:
    """Without accumulate, creates new entry."""
    ring = KillRing()
    ring.push("hello", prepend=False)
    ring.push("world", prepend=False, accumulate=False)
    assert ring.length == 2
    assert ring.peek() == "world"


def test_kill_ring_push_empty() -> None:
    """Pushing empty text is a no-op."""
    ring = KillRing()
    ring.push("", prepend=False)
    assert ring.length == 0
    assert ring.peek() is None


def test_kill_ring_peek_empty() -> None:
    """Peek on empty ring returns None."""
    ring = KillRing()
    assert ring.peek() is None


def test_kill_ring_rotate() -> None:
    """Rotate moves last entry to front, cycling through entries."""
    ring = KillRing()
    ring.push("a", prepend=False)
    ring.push("b", prepend=False)
    ring.push("c", prepend=False)
    # After rotate: ["c", "a", "b"], peek() returns last element "b"
    ring.rotate()
    assert ring.peek() == "b"
    assert ring.length == 3


def test_kill_ring_rotate_single() -> None:
    """Rotate with single entry is a no-op."""
    ring = KillRing()
    ring.push("a", prepend=False)
    ring.rotate()
    assert ring.peek() == "a"
    assert ring.length == 1


def test_kill_ring_rotate_empty() -> None:
    """Rotate on empty ring is a no-op."""
    ring = KillRing()
    ring.rotate()
    assert ring.length == 0


def test_kill_ring_length() -> None:
    """Length property returns correct count."""
    ring = KillRing()
    assert ring.length == 0
    ring.push("a", prepend=False)
    assert ring.length == 1
    ring.push("b", prepend=False)
    assert ring.length == 2


# =============================================================================
# UndoStack tests (port of undo-stack.ts)
# =============================================================================


def test_undo_stack_push_pop() -> None:
    """Push and pop work correctly."""
    stack: UndoStack[list[str]] = UndoStack()
    state = ["hello", "world"]
    stack.push(state)
    result = stack.pop()
    assert result == ["hello", "world"]


def test_undo_stack_push_clone() -> None:
    """Push clones the state, so original changes don't affect stack."""
    stack: UndoStack[list[str]] = UndoStack()
    state = ["hello", "world"]
    stack.push(state)
    state.append("!")
    result = stack.pop()
    assert result == ["hello", "world"]
    assert len(state) == 3


def test_undo_stack_pop_empty() -> None:
    """Pop on empty stack returns None."""
    stack: UndoStack[list[str]] = UndoStack()
    assert stack.pop() is None


def test_undo_stack_clear() -> None:
    """Clear empties the stack."""
    stack: UndoStack[list[str]] = UndoStack()
    stack.push(["a"])
    stack.push(["b"])
    stack.clear()
    assert stack.length == 0
    assert stack.pop() is None


def test_undo_stack_length() -> None:
    """Length property returns correct count."""
    stack: UndoStack[list[str]] = UndoStack()
    assert stack.length == 0
    stack.push(["a"])
    assert stack.length == 1
    stack.push(["b"])
    assert stack.length == 2
    stack.pop()
    assert stack.length == 1


def test_undo_stack_lifo_order() -> None:
    """Stack follows LIFO order."""
    stack: UndoStack[str] = UndoStack()
    stack.push("first")
    stack.push("second")
    stack.push("third")
    assert stack.pop() == "third"
    assert stack.pop() == "second"
    assert stack.pop() == "first"


def test_undo_stack_dict_state() -> None:
    """Works with dict state."""
    stack: UndoStack[dict[str, int]] = UndoStack()
    state = {"x": 1, "y": 2}
    stack.push(state)
    state["z"] = 3  # type: ignore[literal-required]
    result = stack.pop()
    assert result == {"x": 1, "y": 2}
    assert "z" not in result  # type: ignore[operator]


def test_undo_stack_nested_list_clone() -> None:
    """Deep clone works for nested structures."""
    stack: UndoStack[list[list[int]]] = UndoStack()
    state = [[1, 2], [3, 4]]
    stack.push(state)
    state[0].append(99)
    result = stack.pop()
    assert result == [[1, 2], [3, 4]]
    assert state == [[1, 2, 99], [3, 4]]

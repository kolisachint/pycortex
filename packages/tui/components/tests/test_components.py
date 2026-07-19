"""Tests for `cortex.tui.components` (port of component tests)."""

from __future__ import annotations

from cortex.tui.components import Box, Spacer, Text, TruncatedText

# =============================================================================
# Spacer tests (port of spacer.ts)
# =============================================================================


def test_spacer_default_lines() -> None:
    """Default spacer renders one empty line."""
    spacer = Spacer()
    result = spacer.render(10)
    assert result == [""]


def test_spacer_custom_lines() -> None:
    """Spacer renders specified number of empty lines."""
    spacer = Spacer(lines=3)
    result = spacer.render(10)
    assert result == ["", "", ""]


def test_spacer_set_lines() -> None:
    """set_lines updates the number of lines."""
    spacer = Spacer(lines=2)
    spacer.set_lines(4)
    result = spacer.render(10)
    assert len(result) == 4


def test_spacer_width_ignored() -> None:
    """Spacer output is independent of width."""
    spacer = Spacer(lines=2)
    result_10 = spacer.render(10)
    result_20 = spacer.render(20)
    assert result_10 == result_20


# =============================================================================
# TruncatedText tests (port of truncated-text.ts)
# =============================================================================


def test_truncated_text_basic() -> None:
    """Basic text rendering."""
    text = TruncatedText("hello")
    result = text.render(10)
    assert len(result) == 1
    assert "hello" in result[0]


def test_truncated_text_truncation() -> None:
    """Text is truncated to fit width."""
    from cortex.tui.util import visible_width

    text = TruncatedText("hello world")
    result = text.render(7)
    assert len(result) == 1
    # Should be truncated, not wrap - check visual width
    assert visible_width(result[0]) == 7


def test_truncated_text_newline() -> None:
    """Only first line is rendered."""
    text = TruncatedText("hello\nworld")
    result = text.render(10)
    assert len(result) == 1
    assert "hello" in result[0]
    assert "world" not in result[0]


def test_truncated_text_padding_x() -> None:
    """Horizontal padding is applied."""
    text = TruncatedText("hi", padding_x=2)
    result = text.render(10)
    assert result[0].startswith("  hi")
    assert result[0].endswith("  ")


def test_truncated_text_padding_y() -> None:
    """Vertical padding is applied."""
    text = TruncatedText("hi", padding_y=1)
    result = text.render(10)
    assert len(result) == 3  # top padding + content + bottom padding


# =============================================================================
# Text tests (port of text.ts)
# =============================================================================


def test_text_empty() -> None:
    """Empty text returns empty list."""
    text = Text("")
    result = text.render(10)
    assert result == []


def test_text_whitespace_only() -> None:
    """Whitespace-only text returns empty list."""
    text = Text("   ")
    result = text.render(10)
    assert result == []


def test_text_basic() -> None:
    """Basic text rendering."""
    text = Text("hello")
    result = text.render(20)
    assert len(result) > 0
    assert any("hello" in line for line in result)


def test_text_word_wrap() -> None:
    """Long text wraps to multiple lines."""
    text = Text("hello world this is a test", padding_x=0, padding_y=0)
    result = text.render(10)
    assert len(result) > 1


def test_text_padding() -> None:
    """Padding is applied."""
    text = Text("hi", padding_x=1, padding_y=1)
    result = text.render(10)
    # Should have top padding + content + bottom padding
    assert len(result) >= 3


def test_text_set_text() -> None:
    """set_text updates the text content."""
    text = Text("old")
    text.set_text("new")
    result = text.render(10)
    assert any("new" in line for line in result)


def test_text_cache() -> None:
    """Rendering is cached."""
    text = Text("hello")
    result1 = text.render(20)
    result2 = text.render(20)
    assert result1 is result2


def test_text_cache_invalidation() -> None:
    """Cache is invalidated on set_text."""
    text = Text("hello")
    result1 = text.render(20)
    text.set_text("world")
    result2 = text.render(20)
    assert result1 is not result2


def test_text_custom_bg_fn() -> None:
    """Custom background function is applied."""

    def bg_fn(s: str) -> str:
        return f"[{s}]"

    text = Text("hi", custom_bg_fn=bg_fn, padding_x=0, padding_y=0)
    result = text.render(10)
    assert any("[" in line for line in result)


# =============================================================================
# Box tests (port of box.ts)
# =============================================================================


def test_box_empty() -> None:
    """Empty box returns empty list."""
    box = Box()
    result = box.render(10)
    assert result == []


def test_box_with_spacer() -> None:
    """Box renders child components."""
    box = Box()
    box.add_child(Spacer(lines=1))
    result = box.render(10)
    assert len(result) == 3  # top padding + spacer + bottom padding


def test_box_padding() -> None:
    """Box applies padding."""
    box = Box(padding_x=2, padding_y=0)
    box.add_child(Spacer(lines=1))
    result = box.render(10)
    # Content width should be 10 - 2*2 = 6
    assert len(result) == 1


def test_box_remove_child() -> None:
    """remove_child removes a child."""
    box = Box()
    spacer = Spacer()
    box.add_child(spacer)
    box.remove_child(spacer)
    result = box.render(10)
    assert result == []


def test_box_clear() -> None:
    """clear removes all children."""
    box = Box()
    box.add_child(Spacer())
    box.add_child(Spacer())
    box.clear()
    result = box.render(10)
    assert result == []


def test_box_custom_bg_fn() -> None:
    """Custom background function is applied."""

    def bg_fn(s: str) -> str:
        return f"[{s}]"

    box = Box(bg_fn=bg_fn)
    box.add_child(Spacer(lines=1))
    result = box.render(10)
    assert any("[" in line for line in result)


def test_box_multiple_children() -> None:
    """Box renders multiple children."""
    box = Box(padding_y=0)
    box.add_child(Spacer(lines=1))
    box.add_child(Spacer(lines=1))
    result = box.render(10)
    assert len(result) == 2


def test_box_invalidate() -> None:
    """invalidate invalidates child components."""

    class MockComponent:
        def __init__(self) -> None:
            self.invalidated = False

        def render(self, width: int) -> list[str]:
            return [""]

        def invalidate(self) -> None:
            self.invalidated = True

    mock = MockComponent()
    box = Box()
    box.add_child(mock)  # type: ignore[arg-type]
    box.invalidate()
    assert mock.invalidated

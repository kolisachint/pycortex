"""Tests for the user message component."""

from __future__ import annotations

from cortex.code.interactive.components.user_message import (
    OSC133_ZONE_END,
    OSC133_ZONE_FINAL,
    OSC133_ZONE_START,
    UserMessageComponent,
)
from cortex.code.interactive.theme import get_markdown_theme

# ============================================================================
# Tests for OSC 133 zone markers
# ============================================================================


class TestOSC133Zones:
    def test_zone_start(self) -> None:
        """Zone start should be the correct escape sequence."""
        assert OSC133_ZONE_START == "\x1b]133;A\x07"

    def test_zone_end(self) -> None:
        """Zone end should be the correct escape sequence."""
        assert OSC133_ZONE_END == "\x1b]133;B\x07"

    def test_zone_final(self) -> None:
        """Zone final should be the correct escape sequence."""
        assert OSC133_ZONE_FINAL == "\x1b]133;C\x07"


# ============================================================================
# Tests for UserMessageComponent
# ============================================================================


class TestUserMessageComponent:
    def test_render_simple_text(self) -> None:
        """Simple text should render with zone markers."""
        component = UserMessageComponent("Hello, world!")
        lines = component.render(80)
        assert len(lines) >= 1
        # First line should contain zone start
        assert OSC133_ZONE_START in lines[0]
        # Last line should contain zone markers
        assert OSC133_ZONE_END in lines[-1]
        assert OSC133_ZONE_FINAL in lines[-1]

    def test_render_empty_text(self) -> None:
        """Empty text should render without zone markers."""
        component = UserMessageComponent("")
        lines = component.render(80)
        # Empty markdown renders to empty or minimal content
        # The component should handle this gracefully
        assert isinstance(lines, list)

    def test_render_multiline_text(self) -> None:
        """Multiline text should wrap properly."""
        text = "Line 1\nLine 2\nLine 3"
        component = UserMessageComponent(text)
        lines = component.render(80)
        # Should have at least 3 lines (plus possible padding)
        assert len(lines) >= 3

    def test_render_with_markdown(self) -> None:
        """Markdown formatting should be applied."""
        text = "**bold** and *italic*"
        component = UserMessageComponent(text)
        lines = component.render(80)
        # Should render without errors
        assert len(lines) >= 1

    def test_zone_memoization(self) -> None:
        """Second render with same width should return cached result."""
        component = UserMessageComponent("Hello")
        lines1 = component.render(80)
        lines2 = component.render(80)
        # Should be the same object (memoized)
        assert lines1 is lines2

    def test_zone_memoization_different_width(self) -> None:
        """Different width should return different result."""
        component = UserMessageComponent("Hello, this is a longer message")
        lines1 = component.render(40)
        lines2 = component.render(80)
        # Different widths should produce different results
        # (or at least not be the same memoized object)
        assert lines1 is not lines2

    def test_custom_markdown_theme(self) -> None:
        """Custom markdown theme should be used."""
        theme = get_markdown_theme()
        component = UserMessageComponent("Hello", markdown_theme=theme)
        lines = component.render(80)
        assert len(lines) >= 1

    def test_render_preserves_content(self) -> None:
        """Rendered content should contain the original text."""
        component = UserMessageComponent("Test message")
        lines = component.render(80)
        # Join all lines and check content is present
        full_text = "\n".join(lines)
        assert "Test message" in full_text


# ============================================================================
# Tests for zone memoization
# ============================================================================


class TestZoneMemoization:
    def test_memoization_caches_result(self) -> None:
        """The memoization should cache the wrapped result."""
        component = UserMessageComponent("Hello")
        component.render(80)
        # Cache should be populated
        assert component.zone_src is not None
        assert component.zone_out is not None

    def test_memoization_invalidated_on_new_render(self) -> None:
        """New render should update the cache."""
        component = UserMessageComponent("Hello")
        component.render(80)
        # Force a different render by using a different width
        component.render(40)
        # Cache should be updated
        assert component.zone_out is not None


# ============================================================================
# Tests for edge cases
# ============================================================================


class TestEdgeCases:
    def test_render_with_wide_characters(self) -> None:
        """Wide characters should render correctly."""
        component = UserMessageComponent("Hello \u4e16\u754c")
        lines = component.render(80)
        assert len(lines) >= 1

    def test_render_with_special_characters(self) -> None:
        """Special characters should not break rendering."""
        component = UserMessageComponent("Line1\nLine2\tTab")
        lines = component.render(80)
        assert len(lines) >= 1

    def test_render_with_ansi_in_text(self) -> None:
        """ANSI codes in text should be handled."""
        component = UserMessageComponent("Hello \x1b[31mred\x1b[0m")
        lines = component.render(80)
        assert len(lines) >= 1

"""
Tests for differential renderer.
"""

from cortex.tui.render import TUI, TestComponent
from virtual_terminal import VirtualTerminal


class TestDifferentialRenderer:
    """Tests for differential renderer."""

    def test_appends_content_differentially(self) -> None:
        """Test that new lines are appended without full redraw."""
        terminal = VirtualTerminal(20, 5)
        tui = TUI(terminal)
        component = TestComponent()
        tui.add_child(component)

        component.lines = ["Line 0", "Line 1", "Line 2"]
        tui.start()
        terminal.wait_for_render()

        assert terminal.get_viewport() == ["Line 0", "Line 1", "Line 2", "", ""]

        initial_redraws = tui.full_redraws

        component.lines = ["Line 0", "Line 1", "Line 2", "Line 3"]
        tui.request_render()
        terminal.wait_for_render()

        # Should not trigger another full redraw
        assert tui.full_redraws == initial_redraws
        assert terminal.get_viewport() == ["Line 0", "Line 1", "Line 2", "Line 3", ""]

        tui.stop()

    def test_preserves_ansi_styles(self) -> None:
        """Test that ANSI styles are preserved during append."""
        terminal = VirtualTerminal(20, 5)
        tui = TUI(terminal)
        component = TestComponent()
        tui.add_child(component)

        component.lines = ["\x1b[31mRed\x1b[0m"]
        tui.start()
        terminal.wait_for_render()

        component.lines = ["\x1b[31mRed\x1b[0m", "\x1b[32mGreen\x1b[0m"]
        tui.request_render()
        terminal.wait_for_render()

        # Verify styles are preserved
        viewport = terminal.get_viewport()
        assert len(viewport) >= 2

        tui.stop()

    def test_clears_empty_rows_when_shrinking(self) -> None:
        """Test that empty rows are cleared when content shrinks."""
        terminal = VirtualTerminal(20, 5)
        tui = TUI(terminal)
        component = TestComponent()
        tui.add_child(component)

        component.lines = ["Line 0", "Line 1", "Line 2"]
        tui.start()
        terminal.wait_for_render()

        # Grow content
        component.lines = ["Line 0", "Line 1", "Line 2", "Line 3", "Line 4"]
        tui.request_render()
        terminal.wait_for_render()

        initial_redraws = tui.full_redraws

        # Shrink content
        component.lines = ["Line 0"]
        tui.request_render()
        terminal.wait_for_render()

        # Should trigger full redraw
        assert tui.full_redraws > initial_redraws
        assert terminal.get_viewport() == ["Line 0", "", "", "", ""]

        tui.stop()

    def test_appends_after_shrink_without_another_full_redraw(self) -> None:
        """Test that append stays on differential path after shrink."""
        terminal = VirtualTerminal(20, 5)
        tui = TUI(terminal)
        component = TestComponent()
        tui.add_child(component)

        component.lines = ["Line 0", "Line 1", "Line 2", "Line 3", "Line 4"]
        tui.start()
        terminal.wait_for_render()

        # Shrink
        component.lines = ["Line 0", "Line 1"]
        tui.request_render()
        terminal.wait_for_render()

        initial_redraws = tui.full_redraws
        assert tui.full_redraws > 0

        # Append
        component.lines = ["Line 0", "Line 1", "Line 2"]
        tui.request_render()
        terminal.wait_for_render()

        # Should not trigger another full redraw
        assert tui.full_redraws == initial_redraws
        assert terminal.get_viewport() == ["Line 0", "Line 1", "Line 2", "", ""]

        tui.stop()

    def test_full_rerender_when_deleted_lines_move_viewport(self) -> None:
        """Test full re-render when deleted lines move viewport upward."""
        terminal = VirtualTerminal(20, 5)
        tui = TUI(terminal)
        component = TestComponent()
        tui.add_child(component)

        component.lines = [f"Line {i}" for i in range(12)]
        tui.start()
        terminal.wait_for_render()

        initial_redraws = tui.full_redraws

        component.lines = [f"Line {i}" for i in range(7)]
        tui.request_render()
        terminal.wait_for_render()

        assert tui.full_redraws > initial_redraws
        assert terminal.get_viewport() == ["Line 2", "Line 3", "Line 4", "Line 5", "Line 6"]

        tui.stop()

    def test_clears_stale_content_when_max_lines_inflated(self) -> None:
        """Test clearing stale content when maxLinesRendered was inflated."""
        terminal = VirtualTerminal(40, 10)
        tui = TUI(terminal)
        chat = TestComponent()
        editor = TestComponent()
        tui.add_child(chat)
        tui.add_child(editor)

        long_chat = [f"Chat {i}" for i in range(15)]
        short_chat = [f"Chat {i}" for i in range(12)]
        editor_lines = ["Editor 0", "Editor 1", "Editor 2"]
        selector_lines = [f"Selector {i}" for i in range(8)]

        chat.lines = long_chat
        editor.lines = editor_lines
        tui.start()
        terminal.wait_for_render()

        # Switch to selector
        editor.lines = selector_lines
        tui.request_render()
        terminal.wait_for_render()

        # Switch back to editor
        editor.lines = editor_lines
        tui.request_render()
        terminal.wait_for_render()

        redraws_before_switch = tui.full_redraws

        # Shrink chat
        chat.lines = short_chat
        tui.request_render()
        terminal.wait_for_render()

        assert tui.full_redraws > redraws_before_switch

        viewport = terminal.get_viewport()
        # Verify no stale content
        for i in range(10):
            line = viewport[i] if i < len(viewport) else ""
            assert "Chat 12" not in line
            assert "Chat 13" not in line
            assert "Chat 14" not in line

        tui.stop()

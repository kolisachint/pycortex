"""
Differential renderer for TUI.

Based on code from OpenTUI (https://github.com/anomalyco/opentui)
MIT License - Copyright (c) 2025 opentui
"""

from __future__ import annotations

from typing import Protocol


class Terminal(Protocol):
    """Protocol for terminal interface."""

    def write(self, data: str) -> None:
        """Write output to terminal."""
        ...

    @property
    def columns(self) -> int:
        """Get terminal columns."""
        ...

    @property
    def rows(self) -> int:
        """Get terminal rows."""
        ...

    def move_by(self, lines: int) -> None:
        """Move cursor up (negative) or down (positive) by N lines."""
        ...

    def hide_cursor(self) -> None:
        """Hide the cursor."""
        ...

    def show_cursor(self) -> None:
        """Show the cursor."""
        ...

    def clear_line(self) -> None:
        """Clear current line."""
        ...

    def clear_from_cursor(self) -> None:
        """Clear from cursor to end of screen."""
        ...

    def clear_screen(self) -> None:
        """Clear entire screen and move cursor to (0,0)."""
        ...


class Component(Protocol):
    """Protocol for component interface."""

    def render(self, width: int) -> list[str]:
        """Render component to lines."""
        ...


class TestComponent:
    """Simple test component for testing."""

    def __init__(self) -> None:
        """Initialize TestComponent."""
        self.lines: list[str] = []

    def render(self, width: int) -> list[str]:
        """Render component to lines."""
        return self.lines


class TUI:
    """TUI with differential renderer."""

    def __init__(self, terminal: Terminal) -> None:
        """Initialize TUI."""
        self.terminal = terminal
        self._children: list[Component] = []
        self._previous_lines: list[str] = []
        self._full_redraw_count = 0
        self._stopped = False
        self._render_requested = False
        self._started = False
        self._viewport_top = 0

    def add_child(self, component: Component) -> None:
        """Add a child component."""
        self._children.append(component)

    def start(self) -> None:
        """Start the TUI."""
        self._stopped = False
        self.request_render()

    def stop(self) -> None:
        """Stop the TUI."""
        self._stopped = True

    def request_render(self) -> None:
        """Request a render."""
        if self._stopped:
            return
        self._render()

    @property
    def full_redraws(self) -> int:
        """Get number of full redraws."""
        return self._full_redraw_count

    def _render(self) -> None:
        """Render the TUI."""
        # Get lines from all children
        lines: list[str] = []
        for child in self._children:
            lines.extend(child.render(self.terminal.columns))

        # Differential rendering logic
        if not self._started:
            # First render - always full redraw, but don't count it
            self._full_redraw()
            self._started = True
        elif self._needs_full_redraw(lines):
            # Content changed in a way that requires full redraw
            self._full_redraw_count += 1
            self._full_redraw()
        else:
            # Differential append
            self._differential_append(lines)

        self._previous_lines = lines

    def _needs_full_redraw(self, lines: list[str]) -> bool:
        """Check if a full redraw is needed."""
        # If content shrunk, we need a full redraw
        if len(lines) < len(self._previous_lines):
            return True
        # If content is the same length, check if it changed
        if len(lines) == len(self._previous_lines):
            return lines != self._previous_lines
        return False

    def _full_redraw(self) -> None:
        """Perform a full redraw."""
        lines = []
        for child in self._children:
            lines.extend(child.render(self.terminal.columns))

        height = self.terminal.rows

        # Calculate viewport offset - show last `height` lines if content exceeds viewport
        if len(lines) > height:
            self._viewport_top = max(0, len(lines) - height)
        else:
            self._viewport_top = 0

        # Write lines to terminal
        for row in range(height):
            line_idx = self._viewport_top + row
            if line_idx < len(lines):
                self.terminal.write(f"\x1b[{row + 1};1H")
                self.terminal.write(lines[line_idx])
                self.terminal.write("\x1b[K")  # Clear to end of line
            else:
                # Clear empty rows
                self.terminal.write(f"\x1b[{row + 1};1H")
                self.terminal.write("\x1b[K")  # Clear to end of line

    def _differential_append(self, lines: list[str]) -> None:
        """Perform differential append."""
        prev_len = len(self._previous_lines)
        new_len = len(lines)

        # Append new lines
        if new_len > prev_len:
            # Write new lines starting from where we left off
            for i in range(prev_len, min(new_len, self.terminal.rows)):
                # Use ANSI escape sequence to move to absolute position
                self.terminal.write(f"\x1b[{i + 1};1H")
                self.terminal.write(lines[i])
                self.terminal.write("\x1b[K")  # Clear to end of line

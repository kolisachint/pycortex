"""
Virtual terminal for testing.
"""

from __future__ import annotations

import re
from collections.abc import Callable


class VirtualTerminal:
    """Virtual terminal for testing."""

    def __init__(self, columns: int = 80, rows: int = 24) -> None:
        """Initialize VirtualTerminal."""
        self._columns = columns
        self._rows = rows
        self._input_handler: Callable[[str], None] | None = None
        self._resize_handler: Callable[[], None] | None = None
        # Viewport: list of rows, each row is the current content
        self._viewport: list[str] = ["" for _ in range(rows)]
        self._cursor_row = 0
        self._cursor_col = 0
        # Buffer to accumulate all written data
        self._write_buffer = ""

    def start(self, on_input: Callable[[str], None], on_resize: Callable[[], None]) -> None:
        """Start the terminal with input and resize handlers."""
        self._input_handler = on_input
        self._resize_handler = on_resize

    def drain_input(self, max_ms: float = 1000, idle_ms: float = 50) -> None:
        """No-op for virtual terminal - no stdin to drain."""

    def stop(self) -> None:
        """Stop the terminal and restore state."""
        self._input_handler = None
        self._resize_handler = None

    def write(self, data: str) -> None:
        """Write output to terminal."""
        self._write_buffer += data
        # Parse ANSI sequences and update viewport
        self._process_ansi_data(data)

    def _process_ansi_data(self, data: str) -> None:
        """Process ANSI data and update viewport."""
        # Pattern to match ANSI escape sequences
        ansi_pattern = re.compile(r"\x1b\[([0-9;]*)([A-Za-z])")

        pos = 0
        while pos < len(data):
            match = ansi_pattern.search(data, pos)
            if not match:
                # No more escape sequences, write remaining text
                text = data[pos:]
                if text and self._cursor_row < self._rows:
                    self._viewport[self._cursor_row] = text
                    self._cursor_col += len(text)
                break

            # Write text before the escape sequence
            if match.start() > pos:
                text = data[pos : match.start()]
                if text and self._cursor_row < self._rows:
                    self._viewport[self._cursor_row] = text
                    self._cursor_col += len(text)

            # Process escape sequence
            params = match.group(1)
            command = match.group(2)

            if command == "H":  # Cursor position
                # ESC[row;colH
                if params:
                    parts = params.split(";")
                    row = int(parts[0]) - 1 if parts[0] else 0
                    col = int(parts[1]) - 1 if len(parts) > 1 and parts[1] else 0
                    self._cursor_row = max(0, min(row, self._rows - 1))
                    self._cursor_col = max(0, min(col, self._columns - 1))
                else:
                    self._cursor_row = 0
                    self._cursor_col = 0
            elif command == "A":  # Cursor up
                n = int(params) if params else 1
                self._cursor_row = max(0, self._cursor_row - n)
            elif command == "B":  # Cursor down
                n = int(params) if params else 1
                self._cursor_row = min(self._rows - 1, self._cursor_row + n)
            elif command == "C":  # Cursor forward
                n = int(params) if params else 1
                self._cursor_col = min(self._columns - 1, self._cursor_col + n)
            elif command == "D":  # Cursor backward
                n = int(params) if params else 1
                self._cursor_col = max(0, self._cursor_col - n)
            elif command == "K":  # Erase in line
                n = int(params) if params else 0
                if n == 0:  # Erase to end of line
                    if self._cursor_row < self._rows:
                        # Only clear from cursor to end, keeping what's before
                        self._viewport[self._cursor_row] = self._viewport[self._cursor_row][
                            : self._cursor_col
                        ]
                elif n == 2:  # Erase entire line
                    if self._cursor_row < self._rows:
                        self._viewport[self._cursor_row] = ""
            elif command == "J":  # Erase in display
                n = int(params) if params else 0
                if n == 0:  # Erase to end of screen
                    for i in range(self._cursor_row, self._rows):
                        if i == self._cursor_row:
                            self._viewport[i] = self._viewport[i][: self._cursor_col]
                        else:
                            self._viewport[i] = ""
                elif n == 2:  # Erase entire screen
                    self._viewport = ["" for _ in range(self._rows)]
                    self._cursor_row = 0
                    self._cursor_col = 0

            pos = match.end()

    @property
    def columns(self) -> int:
        """Get terminal columns."""
        return self._columns

    @property
    def rows(self) -> int:
        """Get terminal rows."""
        return self._rows

    @property
    def kitty_protocol_active(self) -> bool:
        """Whether Kitty keyboard protocol is active."""
        return True

    def move_by(self, lines: int) -> None:
        """Move cursor up (negative) or down (positive) by N lines."""
        if lines > 0:
            self._cursor_row = min(self._cursor_row + lines, self._rows - 1)
        elif lines < 0:
            self._cursor_row = max(self._cursor_row + lines, 0)

    def hide_cursor(self) -> None:
        """Hide the cursor."""
        pass

    def show_cursor(self) -> None:
        """Show the cursor."""
        pass

    def clear_line(self) -> None:
        """Clear current line."""
        if self._cursor_row < self._rows:
            self._viewport[self._cursor_row] = ""

    def clear_from_cursor(self) -> None:
        """Clear from cursor to end of screen."""
        for i in range(self._cursor_row, self._rows):
            self._viewport[i] = ""

    def clear_screen(self) -> None:
        """Clear entire screen and move cursor to (0,0)."""
        self._viewport = ["" for _ in range(self._rows)]
        self._cursor_row = 0
        self._cursor_col = 0

    def set_title(self, title: str) -> None:
        """Set terminal window title."""
        pass

    def set_progress(self, active: bool) -> None:
        """Set progress indicator."""
        pass

    # Test-specific methods

    def send_input(self, data: str) -> None:
        """Simulate keyboard input."""
        if self._input_handler:
            self._input_handler(data)

    def resize(self, columns: int, rows: int) -> None:
        """Resize the terminal."""
        self._columns = columns
        self._rows = rows
        # Adjust viewport
        while len(self._viewport) < rows:
            self._viewport.append("")
        self._viewport = self._viewport[:rows]
        if self._resize_handler:
            self._resize_handler()

    def get_viewport(self) -> list[str]:
        """Get the visible viewport (what's currently on screen)."""
        return self._viewport.copy()

    def clear(self) -> None:
        """Clear the terminal viewport."""
        self._viewport = ["" for _ in range(self._rows)]

    def reset(self) -> None:
        """Reset the terminal completely."""
        self.clear()
        self._cursor_row = 0
        self._cursor_col = 0
        self._write_buffer = ""

    def wait_for_render(self) -> None:
        """Wait for TUI's throttled render pipeline to settle."""
        import time

        time.sleep(0.02)

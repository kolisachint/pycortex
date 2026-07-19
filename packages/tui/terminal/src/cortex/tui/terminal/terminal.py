"""
Minimal terminal interface for TUI.

Based on code from OpenTUI (https://github.com/anomalyco/opentui)
MIT License - Copyright (c) 2025 opentui
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable

from .stdin_buffer import StdinBuffer

TERMINAL_PROGRESS_KEEPALIVE_MS = 1000
TERMINAL_PROGRESS_ACTIVE_SEQUENCE = "\x1b]9;4;3\x07"
TERMINAL_PROGRESS_CLEAR_SEQUENCE = "\x1b]9;4;0;\x07"


class Terminal(ABC):
    """Abstract base class for terminal interface."""

    @abstractmethod
    def start(self, on_input: Callable[[str], None], on_resize: Callable[[], None]) -> None:
        """
        Start the terminal with input and resize handlers.

        Args:
            on_input: Callback for input events.
            on_resize: Callback for resize events.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the terminal and restore state."""
        ...

    @abstractmethod
    def drain_input(self, max_ms: float = 1000, idle_ms: float = 50) -> None:
        """
        Drain stdin before exiting to prevent Kitty key release events from
        leaking to the parent shell over slow SSH connections.

        Args:
            max_ms: Maximum time to drain (default: 1000ms).
            idle_ms: Exit early if no input arrives within this time (default: 50ms).
        """
        ...

    @abstractmethod
    def write(self, data: str) -> None:
        """
        Write output to terminal.

        Args:
            data: Data to write.
        """
        ...

    @property
    @abstractmethod
    def columns(self) -> int:
        """Get terminal columns."""
        ...

    @property
    @abstractmethod
    def rows(self) -> int:
        """Get terminal rows."""
        ...

    @property
    @abstractmethod
    def kitty_protocol_active(self) -> bool:
        """Whether Kitty keyboard protocol is active."""
        ...

    @abstractmethod
    def move_by(self, lines: int) -> None:
        """
        Move cursor up (negative) or down (positive) by N lines.

        Args:
            lines: Number of lines to move.
        """
        ...

    @abstractmethod
    def hide_cursor(self) -> None:
        """Hide the cursor."""
        ...

    @abstractmethod
    def show_cursor(self) -> None:
        """Show the cursor."""
        ...

    @abstractmethod
    def clear_line(self) -> None:
        """Clear current line."""
        ...

    @abstractmethod
    def clear_from_cursor(self) -> None:
        """Clear from cursor to end of screen."""
        ...

    @abstractmethod
    def clear_screen(self) -> None:
        """Clear entire screen and move cursor to (0,0)."""
        ...

    @abstractmethod
    def set_title(self, title: str) -> None:
        """
        Set terminal window title.

        Args:
            title: Window title.
        """
        ...

    @abstractmethod
    def set_progress(self, active: bool) -> None:
        """
        Set progress indicator (OSC 9;4).

        Args:
            active: Whether progress indicator is active.
        """
        ...


class ProcessTerminal(Terminal):
    """Real terminal using process.stdin/stdout."""

    def __init__(self) -> None:
        """Initialize ProcessTerminal."""
        self._was_raw: bool = False
        self._input_handler: Callable[[str], None] | None = None
        self._resize_handler: Callable[[], None] | None = None
        self._kitty_protocol_active: bool = False
        self._modify_other_keys_active: bool = False
        self._stdin_buffer: StdinBuffer | None = None
        self._stdin_data_handler: Callable[[str], None] | None = None
        self._progress_interval: float | None = None
        self._write_log_path: str = self._init_write_log_path()

    def _init_write_log_path(self) -> str:
        """Initialize write log path from environment."""
        env = os.environ.get("HOOCODE_TUI_WRITE_LOG", "")
        if not env:
            return ""
        try:
            if os.path.isdir(env):
                now = time.localtime()
                ts = time.strftime("%Y-%m-%d_%H-%M-%S", now)
                return os.path.join(env, f"tui-{ts}-{os.getpid()}.log")
        except OSError:
            # Not an existing directory - use as-is (file path)
            pass
        return env

    @property
    def kitty_protocol_active(self) -> bool:
        """Whether Kitty keyboard protocol is active."""
        return self._kitty_protocol_active

    def start(self, on_input: Callable[[str], None], on_resize: Callable[[], None]) -> None:
        """
        Start the terminal with input and resize handlers.

        Args:
            on_input: Callback for input events.
            on_resize: Callback for resize events.
        """
        self._input_handler = on_input
        self._resize_handler = on_resize

        # Enable bracketed paste mode - terminal will wrap pastes in \\x1b[200~ ... \\x1b[201~
        sys.stdout.write("\x1b[?2004h")

        # Query and enable Kitty keyboard protocol
        self._query_and_enable_kitty_protocol()

    def _setup_stdin_buffer(self) -> None:
        """
        Set up StdinBuffer to split batched input into individual sequences.

        This ensures components receive single events, making matchesKey/isKeyRelease
        work correctly.

        Also watches for Kitty protocol response and enables it when detected.
        This is done here (after stdinBuffer parsing) rather than on raw stdin
        to handle the case where the response arrives split across multiple events.
        """
        self._stdin_buffer = StdinBuffer(timeout=10)

        # Kitty protocol response pattern: \\x1b[?<flags>u
        kitty_response_pattern = re.compile(r"^\x1b\[\?(\d+)u$")

        # Forward individual sequences to the input handler
        def on_data(sequence: str) -> None:
            # Check for Kitty protocol response (only if not already enabled)
            if not self._kitty_protocol_active:
                match = kitty_response_pattern.match(sequence)
                if match:
                    self._kitty_protocol_active = True

                    # Enable Kitty keyboard protocol (push flags)
                    # Flag 1 = disambiguate escape codes
                    # Flag 2 = report event types (press/repeat/release)
                    # Flag 4 = report alternate keys (shifted key, base layout key)
                    # Base layout key enables shortcuts to work with non-Latin keyboard layouts
                    sys.stdout.write("\x1b[>7u")
                    return  # Don't forward protocol response to TUI

            if self._input_handler:
                self._input_handler(sequence)

        self._stdin_buffer.on_data(on_data)

        # Re-wrap paste content with bracketed paste markers for existing editor handling
        def on_paste(content: str) -> None:
            if self._input_handler:
                self._input_handler(f"\x1b[200~{content}\x1b[201~")

        self._stdin_buffer.on_paste(on_paste)

        # Handler that pipes stdin data through the buffer
        def stdin_data_handler(data: str) -> None:
            if self._stdin_buffer is not None:
                self._stdin_buffer.process(data)

        self._stdin_data_handler = stdin_data_handler

    def _query_and_enable_kitty_protocol(self) -> None:
        """
        Query terminal for Kitty keyboard protocol support and enable if available.

        Sends CSI ? u to query current flags. If terminal responds with CSI ? <flags> u,
        it supports the protocol and we enable it with CSI > 1 u.

        If no Kitty response arrives shortly after startup, fall back to enabling
        xterm modifyOtherKeys mode 2. This is needed for tmux, which can forward
        modified enter keys as CSI-u when extended-keys is enabled, but may not
        answer the Kitty protocol query.

        The response is detected in setupStdinBuffer's data handler, which properly
        handles the case where the response arrives split across multiple stdin events.
        """
        self._setup_stdin_buffer()
        # Note: In a real implementation, we would set up stdin reading here
        # For now, we'll just query the terminal
        sys.stdout.write("\x1b[?u")

    def drain_input(self, max_ms: float = 1000, idle_ms: float = 50) -> None:
        """
        Drain stdin before exiting to prevent Kitty key release events from
        leaking to the parent shell over slow SSH connections.

        Args:
            max_ms: Maximum time to drain (default: 1000ms).
            idle_ms: Exit early if no input arrives within this time (default: 50ms).
        """
        if self._kitty_protocol_active:
            # Disable Kitty keyboard protocol first so any late key releases
            # do not generate new Kitty escape sequences.
            sys.stdout.write("\x1b[<u")
            self._kitty_protocol_active = False

        if self._modify_other_keys_active:
            sys.stdout.write("\x1b[>4;0m")
            self._modify_other_keys_active = False

        previous_handler = self._input_handler
        self._input_handler = None

        # Note: In a real implementation, we would set up stdin data handler
        # and track last data time. For now, we just sleep.
        time.sleep(max_ms / 1000)

        self._input_handler = previous_handler

    def stop(self) -> None:
        """Stop the terminal and restore state."""
        if self._progress_interval is not None:
            self._clear_progress_interval()
            sys.stdout.write(TERMINAL_PROGRESS_CLEAR_SEQUENCE)

        # Disable bracketed paste mode
        sys.stdout.write("\x1b[?2004l")

        # Disable Kitty keyboard protocol if not already done by drainInput()
        if self._kitty_protocol_active:
            sys.stdout.write("\x1b[<u")
            self._kitty_protocol_active = False

        if self._modify_other_keys_active:
            sys.stdout.write("\x1b[>4;0m")
            self._modify_other_keys_active = False

        # Clean up StdinBuffer
        if self._stdin_buffer:
            self._stdin_buffer.destroy()
            self._stdin_buffer = None

        # Remove event handlers
        self._stdin_data_handler = None
        self._input_handler = None
        self._resize_handler = None

    def write(self, data: str) -> None:
        """
        Write output to terminal.

        Args:
            data: Data to write.
        """
        sys.stdout.write(data)
        if self._write_log_path:
            try:
                with open(self._write_log_path, "a", encoding="utf-8") as f:
                    f.write(data)
            except OSError:
                # Ignore logging errors
                pass

    @property
    def columns(self) -> int:
        """Get terminal columns."""
        # Try to get from environment first
        cols = os.environ.get("COLUMNS")
        if cols:
            try:
                return int(cols)
            except ValueError:
                pass

        # Try to get from terminal size
        try:
            size = shutil.get_terminal_size()
            return size.columns
        except (OSError, ValueError):
            pass

        return 80

    @property
    def rows(self) -> int:
        """Get terminal rows."""
        # Try to get from environment first
        lines = os.environ.get("LINES")
        if lines:
            try:
                return int(lines)
            except ValueError:
                pass

        # Try to get from terminal size
        try:
            size = shutil.get_terminal_size()
            return size.lines
        except (OSError, ValueError):
            pass

        return 24

    def move_by(self, lines: int) -> None:
        """
        Move cursor up (negative) or down (positive) by N lines.

        Args:
            lines: Number of lines to move.
        """
        if lines > 0:
            # Move down
            sys.stdout.write(f"\x1b[{lines}B")
        elif lines < 0:
            # Move up
            sys.stdout.write(f"\x1b[{-lines}A")
        # lines === 0: no movement

    def hide_cursor(self) -> None:
        """Hide the cursor."""
        sys.stdout.write("\x1b[?25l")

    def show_cursor(self) -> None:
        """Show the cursor."""
        sys.stdout.write("\x1b[?25h")

    def clear_line(self) -> None:
        """Clear current line."""
        sys.stdout.write("\x1b[K")

    def clear_from_cursor(self) -> None:
        """Clear from cursor to end of screen."""
        sys.stdout.write("\x1b[J")

    def clear_screen(self) -> None:
        """Clear entire screen and move cursor to (0,0)."""
        sys.stdout.write("\x1b[2J\x1b[H")  # Clear screen and move to home (1,1)

    def set_title(self, title: str) -> None:
        """
        Set terminal window title.

        Args:
            title: Window title.
        """
        # OSC 0;title BEL - set terminal window title
        sys.stdout.write(f"\x1b]0;{title}\x07")

    def set_progress(self, active: bool) -> None:
        """
        Set progress indicator (OSC 9;4).

        Args:
            active: Whether progress indicator is active.
        """
        if active:
            # OSC 9;4;3 - indeterminate progress
            sys.stdout.write(TERMINAL_PROGRESS_ACTIVE_SEQUENCE)
            if self._progress_interval is None:
                # Start keepalive interval
                self._progress_interval = time.time()
        else:
            self._clear_progress_interval()
            # OSC 9;4;0 - clear progress
            sys.stdout.write(TERMINAL_PROGRESS_CLEAR_SEQUENCE)

    def _clear_progress_interval(self) -> bool:
        """Clear progress interval. Returns True if interval was active."""
        if self._progress_interval is None:
            return False
        self._progress_interval = None
        return True

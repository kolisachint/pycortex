"""
Minimal terminal interface for TUI.

Based on code from OpenTUI (https://github.com/anomalyco/opentui)
MIT License - Copyright (c) 2025 opentui
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import os
import re
import select
import shutil
import signal
import sys
import termios
import threading
import time
import tty
from abc import ABC, abstractmethod
from collections.abc import Callable
from types import FrameType

from .stdin_buffer import StdinBuffer

#: How long to wait for a Kitty keyboard-protocol reply before falling back to
#: xterm modifyOtherKeys mode 2 (tmux answers the latter and not the former).
KITTY_QUERY_TIMEOUT_MS = 150

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
        # Saved tty attributes, restored on stop — the Python counterpart of
        # `process.stdin.isRaw` + `setRawMode(this.wasRaw)`.
        self._saved_tty_attrs: list[int | list[bytes | int]] | None = None
        self._stdin_fd: int = -1
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._reader_loop: asyncio.AbstractEventLoop | None = None
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._previous_sigwinch: object = None
        self._last_stdin_at: float = 0.0

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

        # Save previous state and enable raw mode. Without this the tty stays
        # canonical: nothing arrives until Enter, and Ctrl+C is turned into a
        # SIGINT by the line discipline instead of being delivered as \x03 —
        # which is why `handleCtrlC` reads a keystroke and not a signal.
        self._enable_raw_mode()

        # Enable bracketed paste mode - terminal will wrap pastes in \\x1b[200~ ... \\x1b[201~
        sys.stdout.write("\x1b[?2004h")
        sys.stdout.flush()

        # Set up resize handler immediately. Node gets a `resize` event on
        # stdout; the platform signal underneath it is SIGWINCH.
        self._install_resize_handler()

        # Refresh terminal dimensions - they may be stale after suspend/resume
        # (SIGWINCH is lost while the process is stopped).
        with contextlib.suppress(OSError, ValueError):
            os.kill(os.getpid(), signal.SIGWINCH)

        # Query and enable Kitty keyboard protocol
        self._query_and_enable_kitty_protocol()

    # ---- stdin ------------------------------------------------------------

    def _enable_raw_mode(self) -> None:
        """Put the tty in raw mode, remembering what to put back."""
        self._stdin_fd = -1
        try:
            fd = sys.stdin.fileno()
        except (AttributeError, ValueError, OSError):
            return
        if not os.isatty(fd):
            return
        try:
            self._saved_tty_attrs = termios.tcgetattr(fd)
            tty.setraw(fd)
        except termios.error:
            self._saved_tty_attrs = None
            return
        self._stdin_fd = fd

    def _restore_tty(self) -> None:
        if self._stdin_fd >= 0 and self._saved_tty_attrs is not None:
            with contextlib.suppress(termios.error, OSError):
                termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._saved_tty_attrs)
        self._saved_tty_attrs = None

    def _wait_readable(self, timeout_s: float) -> bool:
        """Whether stdin has something to read within ``timeout_s``.

        Raw mode leaves the fd blocking with ``VMIN=1``, so a bare read waits
        forever on an idle terminal. Everything that is not driven by the event
        loop's readability callback asks here first.
        """
        if self._stdin_fd < 0:
            return False
        try:
            ready, _, _ = select.select([self._stdin_fd], [], [], timeout_s)
        except (OSError, ValueError):
            return False
        return bool(ready)

    def _read_stdin_once(self) -> None:
        """Drain whatever stdin has and push it through the buffer.

        Reads bytes and decodes incrementally rather than reading text: a read
        can land mid-codepoint, and node's `setEncoding("utf8")` stitches those
        halves back together where a naive `os.read(...).decode()` would raise.
        """
        if self._stdin_fd < 0:
            return
        try:
            chunk = os.read(self._stdin_fd, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            return
        if not chunk:
            return
        self._last_stdin_at = time.monotonic() * 1000
        text = self._decoder.decode(chunk)
        if text and self._stdin_data_handler is not None:
            self._stdin_data_handler(text)

    def _install_stdin_reader(self) -> None:
        """Watch stdin. On the event loop when there is one, else on a thread.

        `run_interactive_mode` starts the TUI inside `asyncio.run`, so the loop
        path is the one that runs in production: it keeps input, rendering and
        the render-coalescing timer on a single thread. The thread fallback is
        for a synchronous driver, which has to flush its own frames anyway.
        """
        if self._stdin_fd < 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            self._reader_loop = loop
            loop.add_reader(self._stdin_fd, self._read_stdin_once)
            return

        self._reader_stop.clear()

        def pump() -> None:
            while not self._reader_stop.is_set():
                if self._wait_readable(0.05):
                    self._read_stdin_once()

        self._reader_thread = threading.Thread(target=pump, daemon=True)
        self._reader_thread.start()

    def _remove_stdin_reader(self) -> None:
        if self._reader_loop is not None and self._stdin_fd >= 0:
            with contextlib.suppress(RuntimeError, ValueError, OSError):
                self._reader_loop.remove_reader(self._stdin_fd)
        self._reader_loop = None
        self._reader_stop.set()
        self._reader_thread = None

    # ---- resize -----------------------------------------------------------

    def _install_resize_handler(self) -> None:
        def on_sigwinch(signum: int, frame: FrameType | None) -> None:
            if self._resize_handler is not None:
                self._resize_handler()

        try:
            self._previous_sigwinch = signal.signal(signal.SIGWINCH, on_sigwinch)
        except ValueError:
            # Not the main thread — no signal handling available here.
            self._previous_sigwinch = None

    def _remove_resize_handler(self) -> None:
        if self._previous_sigwinch is not None:
            with contextlib.suppress(ValueError, TypeError, OSError):
                signal.signal(
                    signal.SIGWINCH,
                    self._previous_sigwinch,  # pyright: ignore[reportArgumentType]
                )
            self._previous_sigwinch = None

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
                    sys.stdout.flush()
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
        self._install_stdin_reader()
        sys.stdout.write("\x1b[?u")
        sys.stdout.flush()

        def fall_back_to_modify_other_keys() -> None:
            if not self._kitty_protocol_active and not self._modify_other_keys_active:
                self.write("\x1b[>4;2m")
                self._modify_other_keys_active = True

        # The TS hangs this off `setTimeout`; with no loop running there is
        # nothing to schedule it on, and the fallback is best-effort anyway.
        if self._reader_loop is not None:
            self._reader_loop.call_later(
                KITTY_QUERY_TIMEOUT_MS / 1000, fall_back_to_modify_other_keys
            )

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
            sys.stdout.flush()
            self._kitty_protocol_active = False

        if self._modify_other_keys_active:
            sys.stdout.write("\x1b[>4;0m")
            sys.stdout.flush()
            self._modify_other_keys_active = False

        previous_handler = self._input_handler
        self._input_handler = None

        # Read and discard until stdin has been quiet for `idle_ms`, or until
        # `max_ms` is up. Sleeping the whole `max_ms` — which is what this did
        # before stdin was ever read — makes every exit take a second, and
        # drains nothing.
        self._last_stdin_at = time.monotonic() * 1000
        end_time = self._last_stdin_at + max_ms
        try:
            while True:
                now = time.monotonic() * 1000
                time_left = end_time - now
                if time_left <= 0:
                    break
                if now - self._last_stdin_at >= idle_ms:
                    break
                if self._wait_readable(min(idle_ms, time_left) / 1000):
                    self._read_stdin_once()
        finally:
            self._input_handler = previous_handler

    def stop(self) -> None:
        """Stop the terminal and restore state."""
        if self._progress_interval is not None:
            self._clear_progress_interval()
            sys.stdout.write(TERMINAL_PROGRESS_CLEAR_SEQUENCE)
            sys.stdout.flush()

        # Disable bracketed paste mode
        sys.stdout.write("\x1b[?2004l")
        sys.stdout.flush()

        # Disable Kitty keyboard protocol if not already done by drainInput()
        if self._kitty_protocol_active:
            sys.stdout.write("\x1b[<u")
            sys.stdout.flush()
            self._kitty_protocol_active = False

        if self._modify_other_keys_active:
            sys.stdout.write("\x1b[>4;0m")
            sys.stdout.flush()
            self._modify_other_keys_active = False

        # Clean up StdinBuffer
        if self._stdin_buffer:
            self._stdin_buffer.destroy()
            self._stdin_buffer = None

        # Remove event handlers
        self._remove_stdin_reader()
        self._stdin_data_handler = None
        self._input_handler = None
        self._remove_resize_handler()
        self._resize_handler = None

        # Restore the tty last, so nothing buffered (e.g. Ctrl+D) is
        # re-interpreted by the line discipline on the way out — over SSH that
        # could close the parent shell.
        self._restore_tty()
        sys.stdout.flush()

    def write(self, data: str) -> None:
        """
        Write output to terminal.

        Args:
            data: Data to write.
        """
        sys.stdout.write(data)
        # Node's stdout writes go out as they are made; Python's are buffered,
        # and a TUI frame contains no newline to flush a line-buffered stream —
        # so without this the screen updates a buffer-full at a time.
        sys.stdout.flush()
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
            sys.stdout.flush()
        elif lines < 0:
            # Move up
            sys.stdout.write(f"\x1b[{-lines}A")
            sys.stdout.flush()
        # lines === 0: no movement

    def hide_cursor(self) -> None:
        """Hide the cursor."""
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()

    def show_cursor(self) -> None:
        """Show the cursor."""
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()

    def clear_line(self) -> None:
        """Clear current line."""
        sys.stdout.write("\x1b[K")
        sys.stdout.flush()

    def clear_from_cursor(self) -> None:
        """Clear from cursor to end of screen."""
        sys.stdout.write("\x1b[J")
        sys.stdout.flush()

    def clear_screen(self) -> None:
        """Clear entire screen and move cursor to (0,0)."""
        sys.stdout.write("\x1b[2J\x1b[H")  # Clear screen and move to home (1,1)
        sys.stdout.flush()

    def set_title(self, title: str) -> None:
        """
        Set terminal window title.

        Args:
            title: Window title.
        """
        # OSC 0;title BEL - set terminal window title
        sys.stdout.write(f"\x1b]0;{title}\x07")
        sys.stdout.flush()

    def set_progress(self, active: bool) -> None:
        """
        Set progress indicator (OSC 9;4).

        Args:
            active: Whether progress indicator is active.
        """
        if active:
            # OSC 9;4;3 - indeterminate progress
            sys.stdout.write(TERMINAL_PROGRESS_ACTIVE_SEQUENCE)
            sys.stdout.flush()
            if self._progress_interval is None:
                # Start keepalive interval
                self._progress_interval = time.time()
        else:
            self._clear_progress_interval()
            # OSC 9;4;0 - clear progress
            sys.stdout.write(TERMINAL_PROGRESS_CLEAR_SEQUENCE)
            sys.stdout.flush()

    def _clear_progress_interval(self) -> bool:
        """Clear progress interval. Returns True if interval was active."""
        if self._progress_interval is None:
            return False
        self._progress_interval = None
        return True

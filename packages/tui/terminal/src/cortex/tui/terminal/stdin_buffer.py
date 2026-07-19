"""
StdinBuffer buffers input and emits complete sequences.

This is necessary because stdin data events can arrive in partial chunks,
especially for escape sequences like mouse events. Without buffering,
partial sequences can be misinterpreted as regular keypresses.

For example, the mouse SGR sequence ``\\x1b[<35;20;5m`` might arrive as:
- Event 1: ``\\x1b``
- Event 2: ``[<35``
- Event 3: ``;20;5m``

The buffer accumulates these until a complete sequence is detected.
Call the ``process()`` method to feed input data.

Based on code from OpenTUI (https://github.com/anomalyco/opentui)
MIT License - Copyright (c) 2025 opentui
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable

ESC = "\x1b"
BRACKETED_PASTE_START = "\x1b[200~"
BRACKETED_PASTE_END = "\x1b[201~"


def is_complete_sequence(data: str) -> str:
    """
    Check if a string is a complete escape sequence or needs more data.

    Returns:
        "complete", "incomplete", or "not-escape"
    """
    if not data.startswith(ESC):
        return "not-escape"

    if len(data) == 1:
        return "incomplete"

    after_esc = data[1:]

    # CSI sequences: ESC [
    if after_esc.startswith("["):
        # Check for old-style mouse sequence: ESC[M + 3 bytes
        if after_esc.startswith("[M"):
            # Old-style mouse needs ESC[M + 3 bytes = 6 total
            return "complete" if len(data) >= 6 else "incomplete"
        return is_complete_csi_sequence(data)

    # OSC sequences: ESC ]
    if after_esc.startswith("]"):
        return is_complete_osc_sequence(data)

    # DCS sequences: ESC P ... ESC \\ (includes XTVersion responses)
    if after_esc.startswith("P"):
        return is_complete_dcs_sequence(data)

    # APC sequences: ESC _ ... ESC \\ (includes Kitty graphics responses)
    if after_esc.startswith("_"):
        return is_complete_apc_sequence(data)

    # SS3 sequences: ESC O
    if after_esc.startswith("O"):
        # ESC O followed by a single character
        return "complete" if len(after_esc) >= 2 else "incomplete"

    # Meta key sequences: ESC followed by a single character
    if len(after_esc) == 1:
        return "complete"

    # Unknown escape sequence - treat as complete
    return "complete"


def is_complete_csi_sequence(data: str) -> str:
    """
    Check if CSI sequence is complete.

    CSI sequences: ESC [ ... followed by a final byte (0x40-0x7E)
    """
    if not data.startswith(f"{ESC}["):
        return "complete"

    # Need at least ESC [ and one more character
    if len(data) < 3:
        return "incomplete"

    payload = data[2:]

    # CSI sequences end with a byte in the range 0x40-0x7E (@-~)
    # This includes all letters and several special characters
    last_char = payload[-1]
    last_char_code = ord(last_char)

    if 0x40 <= last_char_code <= 0x7E:
        # Special handling for SGR mouse sequences
        # Format: ESC[<B;X;Ym or ESC[<B;X;YM
        if payload.startswith("<"):
            # Must have format: <digits;digits;digits[Mm]
            mouse_match = re.match(r"^<\d+;\d+;\d+[Mm]$", payload)
            if mouse_match:
                return "complete"
            # If it ends with M or m but doesn't match the pattern, still incomplete
            if last_char in ("M", "m"):
                # Check if we have the right structure
                parts = payload[1:-1].split(";")
                if len(parts) == 3 and all(p.isdigit() for p in parts):
                    return "complete"

            return "incomplete"

        return "complete"

    return "incomplete"


def is_complete_osc_sequence(data: str) -> str:
    """
    Check if OSC sequence is complete.

    OSC sequences: ESC ] ... ST (where ST is ESC \\ or BEL)
    """
    if not data.startswith(f"{ESC}]"):
        return "complete"

    # OSC sequences end with ST (ESC \\) or BEL (\\x07)
    if data.endswith(f"{ESC}\\") or data.endswith("\x07"):
        return "complete"

    return "incomplete"


def is_complete_dcs_sequence(data: str) -> str:
    """
    Check if DCS (Device Control String) sequence is complete.

    DCS sequences: ESC P ... ST (where ST is ESC \\)
    Used for XTVersion responses like ESC P >| ... ESC \\
    """
    if not data.startswith(f"{ESC}P"):
        return "complete"

    # DCS sequences end with ST (ESC \\)
    if data.endswith(f"{ESC}\\"):
        return "complete"

    return "incomplete"


def is_complete_apc_sequence(data: str) -> str:
    """
    Check if APC (Application Program Command) sequence is complete.

    APC sequences: ESC _ ... ST (where ST is ESC \\)
    Used for Kitty graphics responses like ESC _ G ... ESC \\
    """
    if not data.startswith(f"{ESC}_"):
        return "complete"

    # APC sequences end with ST (ESC \\)
    if data.endswith(f"{ESC}\\"):
        return "complete"

    return "incomplete"


def parse_unmodified_kitty_printable_codepoint(sequence: str) -> int | None:
    """Parse unmodified Kitty printable codepoint from sequence."""
    match = re.match(r"^\x1b\[(\d+)(?::\d*)?(?::\d+)?u$", sequence)
    if not match:
        return None

    codepoint = int(match.group(1))
    return codepoint if codepoint >= 32 else None


def extract_complete_sequences(buffer: str) -> tuple[list[str], str]:
    """
    Split accumulated buffer into complete sequences.

    Returns:
        Tuple of (sequences, remainder)
    """
    sequences: list[str] = []
    pos = 0

    while pos < len(buffer):
        remaining = buffer[pos:]

        # Try to extract a sequence starting at this position
        if remaining.startswith(ESC):
            # Find the end of this escape sequence
            seq_end = 1
            while seq_end <= len(remaining):
                candidate = remaining[:seq_end]
                status = is_complete_sequence(candidate)

                if status == "complete":
                    sequences.append(candidate)
                    pos += seq_end
                    break
                elif status == "incomplete":
                    seq_end += 1
                else:
                    # Should not happen when starting with ESC
                    sequences.append(candidate)
                    pos += seq_end
                    break

            if seq_end > len(remaining):
                return sequences, remaining
        else:
            # Not an escape sequence - take a single character
            sequences.append(remaining[0])
            pos += 1

    return sequences, ""


class StdinBuffer:
    """
    Buffers stdin input and emits complete sequences.

    Handles partial escape sequences that arrive across multiple chunks.
    """

    def __init__(self, timeout: int = 10) -> None:
        """
        Initialize StdinBuffer.

        Args:
            timeout: Maximum time to wait for sequence completion (default: 10ms)
                     After this time, the buffer is flushed even if incomplete.
        """
        self._buffer: str = ""
        self._timeout_ms: int = timeout
        self._paste_mode: bool = False
        self._paste_buffer: str = ""
        self._pending_kitty_printable_codepoint: int | None = None
        self._timeout_timer: threading.Timer | None = None
        self._data_handlers: list[Callable[[str], None]] = []
        self._paste_handlers: list[Callable[[str], None]] = []

    def on_data(self, handler: Callable[[str], None]) -> None:
        """Register a handler for data events."""
        self._data_handlers.append(handler)

    def on_paste(self, handler: Callable[[str], None]) -> None:
        """Register a handler for paste events."""
        self._paste_handlers.append(handler)

    def _emit_data(self, sequence: str) -> None:
        """Emit a data event to all registered handlers."""
        raw_codepoint = ord(sequence) if len(sequence) == 1 else None
        if raw_codepoint is not None and raw_codepoint == self._pending_kitty_printable_codepoint:
            self._pending_kitty_printable_codepoint = None
            return

        self._pending_kitty_printable_codepoint = parse_unmodified_kitty_printable_codepoint(
            sequence
        )
        for handler in self._data_handlers:
            handler(sequence)

    def _emit_paste(self, content: str) -> None:
        """Emit a paste event to all registered handlers."""
        for handler in self._paste_handlers:
            handler(content)

    def process(self, data: str | bytes) -> None:
        """
        Process input data through the buffer.

        Args:
            data: Input data as string or bytes.
        """
        # Clear any pending timeout
        if self._timeout_timer is not None:
            self._timeout_timer.cancel()
            self._timeout_timer = None

        # Handle high-byte conversion (for compatibility with parseKeypress)
        # If buffer has single byte > 127, convert to ESC + (byte - 128)
        if isinstance(data, bytes):
            if len(data) == 1 and data[0] > 127:
                byte = data[0] - 128
                s = f"\x1b{chr(byte)}"
            else:
                s = data.decode("utf-8", errors="replace")
        else:
            s = data

        if len(s) == 0 and len(self._buffer) == 0:
            self._emit_data("")
            return

        self._buffer += s

        if self._paste_mode:
            self._paste_buffer += self._buffer
            self._buffer = ""

            end_index = self._paste_buffer.find(BRACKETED_PASTE_END)
            if end_index != -1:
                pasted_content = self._paste_buffer[:end_index]
                remaining = self._paste_buffer[end_index + len(BRACKETED_PASTE_END) :]

                self._paste_mode = False
                self._paste_buffer = ""
                self._pending_kitty_printable_codepoint = None

                self._emit_paste(pasted_content)

                if len(remaining) > 0:
                    self.process(remaining)
            return

        start_index = self._buffer.find(BRACKETED_PASTE_START)
        if start_index != -1:
            if start_index > 0:
                before_paste = self._buffer[:start_index]
                sequences, _ = extract_complete_sequences(before_paste)
                for sequence in sequences:
                    self._emit_data(sequence)

            self._pending_kitty_printable_codepoint = None
            self._buffer = self._buffer[start_index + len(BRACKETED_PASTE_START) :]
            self._paste_mode = True
            self._paste_buffer = self._buffer
            self._buffer = ""

            end_index = self._paste_buffer.find(BRACKETED_PASTE_END)
            if end_index != -1:
                pasted_content = self._paste_buffer[:end_index]
                remaining = self._paste_buffer[end_index + len(BRACKETED_PASTE_END) :]

                self._paste_mode = False
                self._paste_buffer = ""
                self._pending_kitty_printable_codepoint = None

                self._emit_paste(pasted_content)

                if len(remaining) > 0:
                    self.process(remaining)
            return

        sequences, remainder = extract_complete_sequences(self._buffer)
        self._buffer = remainder

        for sequence in sequences:
            self._emit_data(sequence)

        if len(self._buffer) > 0:
            self._timeout_timer = threading.Timer(self._timeout_ms / 1000, self._flush_timeout)
            self._timeout_timer.daemon = True
            self._timeout_timer.start()

    def _flush_timeout(self) -> None:
        """Flush timeout callback."""
        flushed = self.flush()
        for sequence in flushed:
            self._emit_data(sequence)

    def flush(self) -> list[str]:
        """
        Flush incomplete sequences.

        Returns:
            List of flushed sequences.
        """
        if self._timeout_timer is not None:
            self._timeout_timer.cancel()
            self._timeout_timer = None

        if len(self._buffer) == 0:
            return []

        sequences = [self._buffer]
        self._buffer = ""
        self._pending_kitty_printable_codepoint = None
        return sequences

    def clear(self) -> None:
        """Clear the buffer."""
        if self._timeout_timer is not None:
            self._timeout_timer.cancel()
            self._timeout_timer = None
        self._buffer = ""
        self._paste_mode = False
        self._paste_buffer = ""
        self._pending_kitty_printable_codepoint = None

    def get_buffer(self) -> str:
        """
        Get the current buffer contents.

        Returns:
            Current buffer string.
        """
        return self._buffer

    def destroy(self) -> None:
        """Destroy the buffer and clean up resources."""
        self.clear()

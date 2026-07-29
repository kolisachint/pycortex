"""Run a shell command, streaming its output, and hand back what it printed.

Port of ``core/bash-executor.ts``. This is what
:meth:`~cortex.code.session.AgentSession.execute_bash` runs — the `!command`
prompt mode and the RPC mode's bash call, not the bash *tool* (that one is
:func:`cortex.code.tools.create_bash_tool`, and it keeps its own accumulator).

Three things happen to every chunk on the way out of the process, in this order,
and the order matters: ANSI is stripped (a shell that paints its own colours
would otherwise fight the theme), bytes that break width measurement are dropped
(:func:`sanitize_binary_output`), and carriage returns go (a progress bar
rewriting one line becomes one line, not a hundred). Only then is it appended to
the rolling tail and handed to ``on_chunk``.

Output is kept twice over. The rolling buffer is bounded at twice the model's
byte ceiling and is what the caller gets back; the moment the command has printed
more than the ceiling, a temp file opens and everything is written there too, so
"Full output: /tmp/…" in the status line points at something real.

``BashOperations.exec`` blocks — it waits on a child process — so it runs on a
worker thread here, and the chunks it produces are posted back to the event loop
rather than delivered on that thread. The TS gets this for free from Node's
streams; a port that just called ``exec`` inline would freeze the UI for the
length of the command and then paint all of its output at once, which is the one
thing a *streaming* executor must not do.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TextIO

from cortex.code.tools import DEFAULT_MAX_BYTES, truncate_tail
from cortex.code.tools.output_compression import compress_bash_output

__all__ = [
    "BashResult",
    "execute_bash_with_operations",
    "sanitize_binary_output",
    "strip_ansi",
]

import re

# OSC first: its introducer (`ESC ]`) also matches the two-character escape
# alternative, and alternation is ordered, so the terminated form has to be
# offered before the loose one or a window title would leave its payload behind.
_ANSI_ESCAPE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]"
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (the ``strip-ansi`` package's job)."""
    return _ANSI_ESCAPE.sub("", text)


def sanitize_binary_output(text: str) -> str:
    """Drop the code points that break terminal width measurement.

    Port of ``utils/shell.sanitizeBinaryOutput``: control characters other than
    tab, newline and carriage return, and the Unicode interlinear-annotation
    format characters. Python iterates code points natively, so the TS's
    ``Array.from`` surrogate handling has no counterpart here.
    """
    kept: list[str] = []
    for char in text:
        code = ord(char)
        if code in (0x09, 0x0A, 0x0D):
            kept.append(char)
            continue
        if code <= 0x1F:
            continue
        if 0xFFF9 <= code <= 0xFFFB:
            continue
        kept.append(char)
    return "".join(kept)


@dataclass
class BashResult:
    """What a finished (or cancelled) command left behind."""

    #: Combined stdout + stderr, sanitized and possibly truncated.
    output: str
    #: Process exit code; ``None`` when the command was killed or cancelled.
    exit_code: int | None
    #: Whether the command was cancelled through the signal.
    cancelled: bool
    #: Whether ``output`` is a truncated view of what was printed.
    truncated: bool
    #: Where the full output was spilled, when it outgrew the byte ceiling.
    full_output_path: str | None = None


class _OutputSink:
    """The rolling tail, and the temp file it spills into."""

    def __init__(self) -> None:
        self.chunks: list[str] = []
        self.output_bytes = 0
        self.total_bytes = 0
        self.max_output_bytes = DEFAULT_MAX_BYTES * 2
        self.temp_file_path: str | None = None
        self._temp_file: TextIO | None = None

    def ensure_temp_file(self) -> None:
        if self.temp_file_path is not None:
            return
        file_id = secrets.token_hex(8)
        self.temp_file_path = os.path.join(tempfile.gettempdir(), f"hoocode-bash-{file_id}.log")
        self._temp_file = open(self.temp_file_path, "w", encoding="utf-8")  # noqa: SIM115
        for chunk in self.chunks:
            self._temp_file.write(chunk)

    def append(self, text: str, byte_length: int) -> None:
        self.total_bytes += byte_length
        if self.total_bytes > DEFAULT_MAX_BYTES:
            self.ensure_temp_file()
        if self._temp_file is not None:
            self._temp_file.write(text)
        self.chunks.append(text)
        self.output_bytes += len(text)
        while self.output_bytes > self.max_output_bytes and len(self.chunks) > 1:
            self.output_bytes -= len(self.chunks.pop(0))

    def close(self) -> None:
        if self._temp_file is not None:
            self._temp_file.close()
            self._temp_file = None

    def joined(self) -> str:
        return "".join(self.chunks)


def _finish(sink: _OutputSink, command: str, exit_code: int | None, cancelled: bool) -> BashResult:
    """Compress, truncate and package what the sink collected."""
    compressed = compress_bash_output(command, sink.joined())
    truncation = truncate_tail(compressed)
    if truncation.truncated:
        sink.ensure_temp_file()
    sink.close()
    return BashResult(
        output=truncation.content if truncation.truncated else compressed,
        exit_code=None if cancelled else exit_code,
        cancelled=cancelled,
        truncated=truncation.truncated,
        full_output_path=sink.temp_file_path,
    )


async def execute_bash_with_operations(
    command: str,
    cwd: str,
    operations: Any,
    on_chunk: Callable[[str], None] | None = None,
    signal: Any = None,
) -> BashResult:
    """Run *command* through *operations*, streaming chunks to *on_chunk*.

    An aborted command is not an error: the signal being set is the caller's own
    Escape, so the output collected so far comes back with ``cancelled=True``
    rather than propagating the abort as an exception.
    """
    sink = _OutputSink()
    loop = asyncio.get_running_loop()
    decoder_remainder = bytearray()

    def handle_data(data: bytes) -> None:
        # Decode incrementally: a chunk boundary can fall inside a multi-byte
        # character, and decoding each chunk on its own would corrupt it.
        decoder_remainder.extend(data)
        try:
            decoded = decoder_remainder.decode("utf-8")
        except UnicodeDecodeError as err:
            decoded = decoder_remainder[: err.start].decode("utf-8", errors="replace")
            del decoder_remainder[: err.start]
        else:
            decoder_remainder.clear()

        text = sanitize_binary_output(strip_ansi(decoded)).replace("\r", "")
        if not text:
            return
        sink.append(text, len(data))
        if on_chunk is not None:
            # `handle_data` runs on the worker thread; the callback touches
            # components the renderer owns, so it goes back to the loop.
            try:
                loop.call_soon_threadsafe(on_chunk, text)
            except RuntimeError:
                # The loop has gone (the app shut down mid-command). The child
                # is still writing and cannot be told to stop from here; drop
                # the chunk rather than raising on its reader thread, where the
                # only outcome would be a traceback on a terminal nobody owns.
                pass

    def run() -> Any:
        return operations.exec(command, cwd, on_data=handle_data, signal=signal)

    try:
        result = await asyncio.to_thread(run)
    except Exception:
        if signal is not None and getattr(signal, "aborted", False):
            return _finish(sink, command, None, True)
        sink.close()
        raise

    cancelled = bool(signal is not None and getattr(signal, "aborted", False))
    return _finish(sink, command, getattr(result, "exit_code", None), cancelled)

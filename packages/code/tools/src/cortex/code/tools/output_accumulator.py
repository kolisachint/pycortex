"""Streaming output buffer with bounded memory and temp-file spill.

Mechanical port of ``core/tools/output-accumulator.ts``. Incrementally tracks
streaming output, keeps only a decoded tail for display snapshots, and opens a
temp file when the full output needs to be preserved.
"""

from __future__ import annotations

import codecs
import os
import secrets
import tempfile
from dataclasses import dataclass

from cortex.code.tools.output_compression import compress_bash_output
from cortex.code.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationOptions,
    TruncationResult,
    truncate_tail,
)


@dataclass
class OutputSnapshot:
    content: str
    truncation: TruncationResult
    fullOutputPath: str | None = None


def _default_temp_file_path(prefix: str) -> str:
    file_id = secrets.token_hex(8)
    return os.path.join(tempfile.gettempdir(), f"{prefix}-{file_id}.log")


def _byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


class OutputAccumulator:
    """Incrementally tracks streaming output with bounded memory."""

    def __init__(
        self,
        *,
        max_lines: int | None = None,
        max_bytes: int | None = None,
        temp_file_prefix: str | None = None,
        command: str | None = None,
    ) -> None:
        self._max_lines = max_lines if max_lines is not None else DEFAULT_MAX_LINES
        self._max_bytes = max_bytes if max_bytes is not None else DEFAULT_MAX_BYTES
        self._max_rolling_bytes = max(self._max_bytes * 2, 1)
        self._temp_file_prefix = (
            temp_file_prefix if temp_file_prefix is not None else "hoocode-output"
        )
        self._command = command
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        self._raw_chunks: list[bytes] = []
        self._tail_text = ""
        self._tail_bytes = 0
        self._tail_starts_at_line_boundary = True
        self._total_raw_bytes = 0
        self._total_decoded_bytes = 0
        self._total_lines = 1
        self._current_line_bytes = 0
        self._finished = False

        self._temp_file_path: str | None = None
        self._temp_file_handle = None

        self._snapshot_cache: tuple[str, TruncationResult] | None = None

    def append(self, data: bytes) -> None:
        if self._finished:
            raise RuntimeError("Cannot append to a finished output accumulator")

        self._total_raw_bytes += len(data)
        self._snapshot_cache = None
        self._append_decoded_text(self._decoder.decode(data))

        if self._temp_file_handle is not None or self._should_use_temp_file():
            self._ensure_temp_file()
            if self._temp_file_handle is not None:
                self._temp_file_handle.write(data)
        elif len(data) > 0:
            self._raw_chunks.append(data)

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._snapshot_cache = None
        self._append_decoded_text(self._decoder.decode(b"", final=True))
        if self._should_use_temp_file():
            self._ensure_temp_file()

    def snapshot(self, *, persist_if_truncated: bool = False) -> OutputSnapshot:
        cached = self._snapshot_cache
        if cached is None:
            raw_text = self._get_snapshot_text()
            text = (
                compress_bash_output(self._command, raw_text)
                if self._finished and self._command
                else raw_text
            )
            tail_truncation = truncate_tail(
                text, TruncationOptions(max_lines=self._max_lines, max_bytes=self._max_bytes)
            )
            truncated = (
                self._total_lines > self._max_lines or self._total_decoded_bytes > self._max_bytes
            )
            if truncated:
                truncated_by = tail_truncation.truncated_by
                if truncated_by is None:
                    truncated_by = (
                        "bytes" if self._total_decoded_bytes > self._max_bytes else "lines"
                    )
            else:
                truncated_by = None
            truncation = TruncationResult(
                content=tail_truncation.content,
                truncated=truncated,
                truncated_by=truncated_by,
                total_lines=self._total_lines,
                total_bytes=self._total_decoded_bytes,
                output_lines=tail_truncation.output_lines,
                output_bytes=tail_truncation.output_bytes,
                last_line_partial=tail_truncation.last_line_partial,
                first_line_exceeds_limit=tail_truncation.first_line_exceeds_limit,
                max_lines=self._max_lines,
                max_bytes=self._max_bytes,
            )
            cached = (truncation.content, truncation)
            self._snapshot_cache = cached

        if persist_if_truncated and cached[1].truncated:
            self._ensure_temp_file()

        return OutputSnapshot(
            content=cached[0],
            truncation=cached[1],
            fullOutputPath=self._temp_file_path,
        )

    def close_temp_file(self) -> None:
        if self._temp_file_handle is None:
            return
        handle = self._temp_file_handle
        self._temp_file_handle = None
        handle.flush()
        handle.close()

    def get_last_line_bytes(self) -> int:
        return self._current_line_bytes

    def _append_decoded_text(self, text: str) -> None:
        if len(text) == 0:
            return

        num_bytes = _byte_length(text)
        self._total_decoded_bytes += num_bytes
        self._tail_text += text
        self._tail_bytes += num_bytes
        if self._tail_bytes > self._max_rolling_bytes * 2:
            self._trim_tail()

        newlines = text.count("\n")
        if newlines == 0:
            self._current_line_bytes += num_bytes
        else:
            last_newline = text.rfind("\n")
            self._total_lines += newlines
            self._current_line_bytes = _byte_length(text[last_newline + 1 :])

    def _trim_tail(self) -> None:
        buffer = self._tail_text.encode("utf-8")
        if len(buffer) <= self._max_rolling_bytes:
            self._tail_bytes = len(buffer)
            return

        start = len(buffer) - self._max_rolling_bytes
        while start < len(buffer) and (buffer[start] & 0xC0) == 0x80:
            start += 1

        if start != 0:
            self._tail_starts_at_line_boundary = buffer[start - 1] == 0x0A
        self._tail_text = buffer[start:].decode("utf-8", errors="replace")
        self._tail_bytes = _byte_length(self._tail_text)

    def _get_snapshot_text(self) -> str:
        if self._tail_starts_at_line_boundary:
            return self._tail_text
        first_newline = self._tail_text.find("\n")
        return self._tail_text if first_newline == -1 else self._tail_text[first_newline + 1 :]

    def _should_use_temp_file(self) -> bool:
        return (
            self._total_raw_bytes > self._max_bytes
            or self._total_decoded_bytes > self._max_bytes
            or self._total_lines > self._max_lines
        )

    def _ensure_temp_file(self) -> None:
        if self._temp_file_path:
            return
        self._temp_file_path = _default_temp_file_path(self._temp_file_prefix)
        self._temp_file_handle = open(self._temp_file_path, "wb")
        for chunk in self._raw_chunks:
            self._temp_file_handle.write(chunk)
        self._raw_chunks = []

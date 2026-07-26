"""Shared truncation utilities for tool outputs.

Truncation is based on two independent limits - whichever is hit first wins:
- Line limit (default: 800 lines)
- Byte limit (default: 32KB)

These caps bound how much a single tool result can inject into the transcript.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DEFAULT_MAX_LINES = 800
"""Maximum number of lines to return from a tool."""

DEFAULT_MAX_BYTES = 32 * 1024
"""Maximum bytes to return from a tool (32KB)."""

GREP_MAX_LINE_LENGTH = 500
"""Max chars per grep match line."""


@dataclass
class TruncationResult:
    """Result of truncation operation."""

    content: str
    truncated: bool
    truncated_by: Literal["lines", "bytes"] | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    last_line_partial: bool
    first_line_exceeds_limit: bool
    max_lines: int
    max_bytes: int


@dataclass
class TruncationOptions:
    """Options for truncation."""

    max_lines: int | None = None
    max_bytes: int | None = None


def format_size(bytes_count: int) -> str:
    """Format bytes as human-readable size."""
    if bytes_count < 1024:
        return f"{bytes_count}B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f}KB"
    else:
        return f"{bytes_count / (1024 * 1024):.1f}MB"


def _byte_length(text: str) -> int:
    """Get byte length of text in UTF-8."""
    return len(text.encode("utf-8"))


def truncate_head(content: str, options: TruncationOptions | None = None) -> TruncationResult:
    """Truncate content from the head (keep first N lines/bytes).

    Suitable for file reads where you want to see the beginning.
    Never returns partial lines.
    """
    max_lines = (
        options.max_lines if options and options.max_lines is not None else DEFAULT_MAX_LINES
    )
    max_bytes = (
        options.max_bytes if options and options.max_bytes is not None else DEFAULT_MAX_BYTES
    )

    total_bytes = _byte_length(content)
    lines = content.split("\n")
    total_lines = len(lines)

    # Check if no truncation needed
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content=content,
            truncated=False,
            truncated_by=None,
            total_lines=total_lines,
            total_bytes=total_bytes,
            output_lines=total_lines,
            output_bytes=total_bytes,
            last_line_partial=False,
            first_line_exceeds_limit=False,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    # Check if first line alone exceeds byte limit
    first_line_bytes = _byte_length(lines[0])
    if first_line_bytes > max_bytes:
        return TruncationResult(
            content="",
            truncated=True,
            truncated_by="bytes",
            total_lines=total_lines,
            total_bytes=total_bytes,
            output_lines=0,
            output_bytes=0,
            last_line_partial=False,
            first_line_exceeds_limit=True,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    # Collect complete lines that fit
    output_lines_arr: list[str] = []
    output_bytes_count = 0
    truncated_by: Literal["lines", "bytes"] = "lines"

    for i, line in enumerate(lines):
        if i >= max_lines:
            break
        line_bytes = _byte_length(line) + (1 if i > 0 else 0)  # +1 for newline

        if output_bytes_count + line_bytes > max_bytes:
            truncated_by = "bytes"
            break

        output_lines_arr.append(line)
        output_bytes_count += line_bytes

    # If we exited due to line limit
    if len(output_lines_arr) >= max_lines and output_bytes_count <= max_bytes:
        truncated_by = "lines"

    output_content = "\n".join(output_lines_arr)
    final_output_bytes = _byte_length(output_content)

    return TruncationResult(
        content=output_content,
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(output_lines_arr),
        output_bytes=final_output_bytes,
        last_line_partial=False,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def truncate_tail(content: str, options: TruncationOptions | None = None) -> TruncationResult:
    """Truncate content from the tail (keep last N lines/bytes).

    Suitable for bash output where you want to see the end (errors, final results).
    May return partial first line if the last line exceeds byte limit.
    """
    max_lines = (
        options.max_lines if options and options.max_lines is not None else DEFAULT_MAX_LINES
    )
    max_bytes = (
        options.max_bytes if options and options.max_bytes is not None else DEFAULT_MAX_BYTES
    )

    total_bytes = _byte_length(content)
    lines = content.split("\n")
    total_lines = len(lines)

    # Check if no truncation needed
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content=content,
            truncated=False,
            truncated_by=None,
            total_lines=total_lines,
            total_bytes=total_bytes,
            output_lines=total_lines,
            output_bytes=total_bytes,
            last_line_partial=False,
            first_line_exceeds_limit=False,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    # Work backwards from the end
    output_lines_arr: list[str] = []
    output_bytes_count = 0
    truncated_by: Literal["lines", "bytes"] = "lines"
    last_line_partial = False

    for i in range(len(lines) - 1, -1, -1):
        if len(output_lines_arr) >= max_lines:
            break
        line = lines[i]
        line_bytes = _byte_length(line) + (1 if len(output_lines_arr) > 0 else 0)  # +1 for newline

        if output_bytes_count + line_bytes > max_bytes:
            truncated_by = "bytes"
            # Edge case: if we haven't added ANY lines yet and this line exceeds maxBytes,
            # take the end of the line (partial)
            if len(output_lines_arr) == 0:
                truncated_line = _truncate_string_to_bytes_from_end(line, max_bytes)
                output_lines_arr.insert(0, truncated_line)
                output_bytes_count = _byte_length(truncated_line)
                last_line_partial = True
            break

        output_lines_arr.insert(0, line)
        output_bytes_count += line_bytes

    # If we exited due to line limit
    if len(output_lines_arr) >= max_lines and output_bytes_count <= max_bytes:
        truncated_by = "lines"

    output_content = "\n".join(output_lines_arr)
    final_output_bytes = _byte_length(output_content)

    return TruncationResult(
        content=output_content,
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(output_lines_arr),
        output_bytes=final_output_bytes,
        last_line_partial=last_line_partial,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def _truncate_string_to_bytes_from_end(text: str, max_bytes: int) -> str:
    """Truncate a string to fit within a byte limit (from the end).

    Handles multi-byte UTF-8 characters correctly.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text

    # Start from the end, skip max_bytes back
    start = len(encoded) - max_bytes

    # Find a valid UTF-8 boundary (start of a character)
    while start < len(encoded) and (encoded[start] & 0xC0) == 0x80:
        start += 1

    return encoded[start:].decode("utf-8")


def truncate_line(
    line: str,
    max_chars: int = GREP_MAX_LINE_LENGTH,
) -> tuple[str, bool]:
    """Truncate a single line to max characters, adding [truncated] suffix.

    Used for grep match lines.
    Returns (text, was_truncated).
    """
    if len(line) <= max_chars:
        return line, False
    return f"{line[:max_chars]}... [truncated]", True

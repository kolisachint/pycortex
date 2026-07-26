# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnusedCallResult=false, reportCallIssue=false, reportReturnType=false, reportMissingTypeArgument=false
"""Read tool for reading file contents.

Mechanical port of ``core/tools/read.ts`` (execute path). TUI rendering
(``renderCall``/``renderResult``), image auto-resize, session-manager dedup, and
the non-vision model note are not ported in this leaf; images are returned at
full size.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cortex.agent.types import AgentTool, AgentToolResult
from cortex.ai.types import ImageContent, TextContent
from cortex.code.tools.mime import detect_supported_image_mime_type_from_file
from cortex.code.tools.output_compression import compress_read_output
from cortex.code.tools.path_utils import resolve_read_path
from cortex.code.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationOptions,
    TruncationResult,
    format_size,
    truncate_head,
)

# Structured/binary document formats that a plain utf-8 read cannot handle.
_DOC_FORMAT_EXTENSIONS: dict[str, str] = {
    ".docx": "Word (OOXML)",
    ".xlsx": "Excel (OOXML)",
    ".pptx": "PowerPoint (OOXML)",
    ".pdf": "PDF",
}


def _get_doc_format_hint(absolute_path: str) -> str | None:
    dot = absolute_path.rfind(".")
    if dot == -1:
        return None
    return _DOC_FORMAT_EXTENSIONS.get(absolute_path[dot:].lower())


@dataclass
class ReadToolDetails:
    truncation: TruncationResult | None = None


class ReadOperations(Protocol):
    """Pluggable operations for the read tool."""

    def read_file(self, absolute_path: str) -> bytes: ...
    def access(self, absolute_path: str) -> None: ...
    def detect_image_mime_type(self, absolute_path: str) -> str | None: ...


class _DefaultReadOperations:
    def read_file(self, absolute_path: str) -> bytes:
        return Path(absolute_path).read_bytes()

    def access(self, absolute_path: str) -> None:
        # Raises FileNotFoundError / PermissionError on failure.
        with open(absolute_path, "rb"):
            pass

    def detect_image_mime_type(self, absolute_path: str) -> str | None:
        return detect_supported_image_mime_type_from_file(absolute_path)


@dataclass
class ReadToolOptions:
    auto_resize_images: bool = True
    operations: ReadOperations | None = None
    max_output_bytes: int = DEFAULT_MAX_BYTES
    max_output_lines: int = DEFAULT_MAX_LINES
    dedup_reads: bool = False


def create_read_tool(
    cwd: str,
    options: ReadToolOptions | None = None,
) -> AgentTool[dict[str, Any], ReadToolDetails | None]:
    """Create a read tool."""
    ops: ReadOperations = (
        options.operations if options and options.operations else _DefaultReadOperations()
    )
    max_bytes = options.max_output_bytes if options else DEFAULT_MAX_BYTES
    max_lines = options.max_output_lines if options else DEFAULT_MAX_LINES

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
        on_update: Callable[..., Any] | None = None,
    ) -> AgentToolResult[ReadToolDetails | None]:
        if signal is not None and getattr(signal, "aborted", False):
            raise RuntimeError("Operation aborted")

        path = params.get("path", "")
        offset = params.get("offset")
        limit = params.get("limit")

        absolute_path = resolve_read_path(path, cwd)

        ops.access(absolute_path)

        mime_type = ops.detect_image_mime_type(absolute_path)
        content: list[TextContent | ImageContent]
        details: ReadToolDetails | None = None

        if mime_type:
            buffer = ops.read_file(absolute_path)
            b64 = base64.b64encode(buffer).decode("ascii")
            content = [
                TextContent(text=f"Read image file [{mime_type}]"),
                ImageContent(data=b64, mime_type=mime_type),
            ]
        elif _get_doc_format_hint(absolute_path):
            label = _get_doc_format_hint(absolute_path)
            content = [
                TextContent(
                    text=(
                        f"[{label} document — not plain text. Use DocRead on {path} to "
                        "extract editable, id-addressed structure (then DocEdit/DocWrite "
                        "to modify it losslessly). DocRead requires --enable-filetools.]"
                    )
                )
            ]
        else:
            buffer = ops.read_file(absolute_path)
            text_content = buffer.decode("utf-8")
            all_lines = text_content.split("\n")
            total_file_lines = len(all_lines)

            start_line = max(0, offset - 1) if offset else 0
            start_line_display = start_line + 1
            if start_line >= len(all_lines):
                raise RuntimeError(
                    f"Offset {offset} is beyond end of file ({len(all_lines)} lines total)"
                )

            user_limited_lines: int | None = None
            if limit is not None:
                end_line = min(start_line + limit, len(all_lines))
                selected_content = "\n".join(all_lines[start_line:end_line])
                user_limited_lines = end_line - start_line
            elif start_line == 0:
                selected_content = text_content
            else:
                selected_content = "\n".join(all_lines[start_line:])

            compressed_content = compress_read_output(selected_content)
            truncation = truncate_head(
                compressed_content, TruncationOptions(max_bytes=max_bytes, max_lines=max_lines)
            )

            if truncation.first_line_exceeds_limit:
                first_line_size = format_size(len(all_lines[start_line].encode("utf-8")))
                output_text = (
                    f"[Line {start_line_display} is {first_line_size}, exceeds "
                    f"{format_size(max_bytes)} limit. Use bash: sed -n "
                    f"'{start_line_display}p' {path} | head -c {max_bytes}]"
                )
                details = ReadToolDetails(truncation=truncation)
            elif truncation.truncated:
                end_line_display = start_line_display + truncation.output_lines - 1
                next_offset = end_line_display + 1
                output_text = truncation.content
                if truncation.truncated_by == "lines":
                    output_text += (
                        f"\n\n[Showing lines {start_line_display}-{end_line_display} of "
                        f"{total_file_lines}. Use offset={next_offset} to continue.]"
                    )
                else:
                    output_text += (
                        f"\n\n[Showing lines {start_line_display}-{end_line_display} of "
                        f"{total_file_lines} ({format_size(max_bytes)} limit). "
                        f"Use offset={next_offset} to continue.]"
                    )
                details = ReadToolDetails(truncation=truncation)
            elif user_limited_lines is not None and start_line + user_limited_lines < len(
                all_lines
            ):
                remaining = len(all_lines) - (start_line + user_limited_lines)
                next_offset = start_line + user_limited_lines + 1
                output_text = (
                    f"{truncation.content}\n\n"
                    f"[{remaining} more lines in file. Use offset={next_offset} to continue.]"
                )
            else:
                output_text = truncation.content

            content = [TextContent(text=output_text)]

        return AgentToolResult(content=content, details=details)

    return AgentTool(
        name="read",
        label="read",
        description=(
            "Read the contents of a file. Supports text files and images "
            "(jpg, png, gif, webp). Images are sent as attachments. For text files, "
            f"output is truncated to {max_lines} lines or {round(max_bytes / 1024)}KB "
            "(whichever is hit first). Use offset/limit for large files. When you need "
            "the full file, continue with offset until complete."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read (relative or absolute)",
                },
                "offset": {
                    "type": "number",
                    "description": "Line number to start reading from (1-indexed)",
                },
                "limit": {"type": "number", "description": "Maximum number of lines to read"},
            },
            "required": ["path"],
        },
        execute=execute,
    )


# Re-exported for callers/tests.
__all__ = [
    "ReadOperations",
    "ReadToolDetails",
    "ReadToolOptions",
    "create_read_tool",
]

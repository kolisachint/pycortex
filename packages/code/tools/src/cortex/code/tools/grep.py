# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnusedCallResult=false, reportCallIssue=false, reportReturnType=false, reportMissingTypeArgument=false
"""Grep tool for content search.

Mechanical port of ``core/tools/grep.ts`` (execute path). The TS drives the fast
path with ``rg`` and falls back to a pure-JS scan; this port always uses the
native scan (:mod:`cortex.code.tools.native_search`), which reproduces rg's
.gitignore handling, hidden-file inclusion, and ``.git`` exclusion. The embsearch
hint and TUI rendering are not ported.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from cortex.agent.types import AgentTool, AgentToolResult
from cortex.ai.types import TextContent
from cortex.code.tools.native_search import (
    InvalidRegexError,
    NativeGrepOptions,
    native_grep,
)
from cortex.code.tools.output_compression import compress_grep_output
from cortex.code.tools.path_utils import resolve_to_cwd
from cortex.code.tools.truncate import (
    DEFAULT_MAX_BYTES,
    GREP_MAX_LINE_LENGTH,
    TruncationOptions,
    TruncationResult,
    format_size,
    truncate_head,
    truncate_line,
)

_DEFAULT_LIMIT = 100
_MAX_SAFE = 2**53 - 1


def _normalize_grep_glob(glob: str | None) -> str | None:
    if not glob:
        return None
    if "/" in glob and not glob.startswith("/") and not glob.startswith("**/"):
        return f"**/{glob}"
    return glob


@dataclass
class GrepToolDetails:
    truncation: TruncationResult | None = None
    match_limit_reached: int | None = None
    lines_truncated: bool | None = None


class GrepOperations(Protocol):
    """Pluggable operations for the grep tool."""

    def is_directory(self, absolute_path: str) -> bool: ...
    def read_file(self, absolute_path: str) -> str: ...


class _DefaultGrepOperations:
    def is_directory(self, absolute_path: str) -> bool:
        return os.path.isdir(absolute_path)

    def read_file(self, absolute_path: str) -> str:
        with open(absolute_path, encoding="utf-8", errors="replace") as fh:
            return fh.read()


class GrepToolOptions:
    """Options for the grep tool."""

    def __init__(self, operations: GrepOperations | None = None) -> None:
        self.operations = operations


def create_grep_tool(
    cwd: str,
    options: GrepToolOptions | None = None,
) -> AgentTool[dict[str, Any], GrepToolDetails | None]:
    """Create a grep tool."""
    custom_ops = options.operations if options else None

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
        on_update: Callable[..., Any] | None = None,
    ) -> AgentToolResult[GrepToolDetails | None]:
        if signal is not None and getattr(signal, "aborted", False):
            raise RuntimeError("Operation aborted")

        pattern = params.get("pattern", "")
        search_dir = params.get("path")
        glob = params.get("glob")
        ignore_case = bool(params.get("ignoreCase", False))
        literal = bool(params.get("literal", False))
        context = params.get("context")
        limit = params.get("limit")

        if not pattern:
            raise RuntimeError("pattern must not be empty")

        normalized_glob = _normalize_grep_glob(glob)
        search_path = resolve_to_cwd(search_dir or ".", cwd)
        ops: GrepOperations = custom_ops if custom_ops else _DefaultGrepOperations()

        try:
            is_directory = ops.is_directory(search_path)
        except OSError as e:
            raise RuntimeError(f"Path not found: {search_path}") from e

        context_value = context if context and context > 0 else 0
        effective_limit = max(1, limit if limit is not None else _DEFAULT_LIMIT)

        def format_path(file_path: str) -> str:
            if is_directory:
                relative = os.path.relpath(file_path, search_path)
                if relative and not relative.startswith(".."):
                    return relative.replace("\\", "/")
            return os.path.basename(file_path)

        file_cache: dict[str, list[str]] = {}

        def get_file_lines(file_path: str) -> list[str]:
            lines = file_cache.get(file_path)
            if lines is None:
                try:
                    content = ops.read_file(file_path)
                    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                except OSError:
                    lines = []
                file_cache[file_path] = lines
            return lines

        lines_truncated = False

        def format_block(file_path: str, line_number: int) -> list[str]:
            nonlocal lines_truncated
            lines = get_file_lines(file_path)
            if not lines:
                return [f"  {line_number}: (unable to read file)"]
            block: list[str] = []
            start = max(1, line_number - context_value) if context_value > 0 else line_number
            end = min(len(lines), line_number + context_value) if context_value > 0 else line_number
            for current in range(start, end + 1):
                line_text = lines[current - 1] if current - 1 < len(lines) else ""
                sanitized = line_text.replace("\r", "")
                is_match_line = current == line_number
                truncated_text, was_truncated = truncate_line(sanitized)
                if was_truncated:
                    lines_truncated = True
                if is_match_line:
                    block.append(f"{current}: {truncated_text}")
                else:
                    block.append(f"{current}- {truncated_text}")
            return block

        try:
            result = await native_grep(
                search_path,
                NativeGrepOptions(
                    pattern=pattern,
                    isDirectory=is_directory,
                    ignoreCase=ignore_case,
                    literal=literal,
                    glob=normalized_glob,
                    limit=effective_limit,
                    signal=signal,
                    readFile=ops.read_file,
                ),
            )
        except InvalidRegexError as err:
            error_msg = str(err) or "grep failed"
            if getattr(err, "invalid_regex", False) and not literal:
                error_msg += (
                    "\n\nThe pattern is not a valid regex. To search for it as plain "
                    "text, pass literal: true (or escape the regex metacharacters)."
                )
            raise RuntimeError(error_msg) from err

        if signal is not None and getattr(signal, "aborted", False):
            raise RuntimeError("Operation aborted")

        matches = result.matches
        match_limit_reached = result.matchLimitReached

        if len(matches) == 0:
            return AgentToolResult(
                content=[TextContent(text="No matches found")],
                details=None,
            )

        output_lines: list[str] = []
        file_groups: dict[str, list[Any]] = {}
        for match in matches:
            file_groups.setdefault(match.filePath, []).append(match)

        for file_path, file_matches in file_groups.items():
            relative_path = format_path(file_path)
            output_lines.append(relative_path)
            for match in file_matches:
                if context_value == 0 and match.lineText is not None:
                    sanitized = match.lineText.replace("\r\n", "\n").replace("\r", "")
                    if sanitized.endswith("\n"):
                        sanitized = sanitized[:-1]
                    truncated_text, was_truncated = truncate_line(sanitized)
                    if was_truncated:
                        lines_truncated = True
                    output_lines.append(f"{match.lineNumber}: {truncated_text}")
                else:
                    output_lines.extend(format_block(match.filePath, match.lineNumber))

        raw_output = "\n".join(output_lines)
        compressed_output = compress_grep_output(raw_output)
        truncation = truncate_head(compressed_output, TruncationOptions(max_lines=_MAX_SAFE))
        output = truncation.content
        details = GrepToolDetails()
        has_detail = False
        notices: list[str] = []
        if match_limit_reached:
            notices.append(
                f"{effective_limit} match{'' if effective_limit == 1 else 'es'} limit reached. "
                f"Use limit={effective_limit * 2} for more, or refine pattern"
            )
            details.match_limit_reached = effective_limit
            has_detail = True
        if truncation.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
            details.truncation = truncation
            has_detail = True
        if lines_truncated:
            notices.append(
                f"Some lines truncated to {GREP_MAX_LINE_LENGTH} chars. "
                "Use read tool to see full lines"
            )
            details.lines_truncated = True
            has_detail = True
        if notices:
            output += f"\n\n[{'. '.join(notices)}]"

        return AgentToolResult(
            content=[TextContent(text=output)],
            details=details if has_detail else None,
        )

    return AgentTool(
        name="grep",
        label="grep",
        description=(
            "Search file contents for a pattern. Returns matching lines with file paths "
            "and line numbers. Case-sensitive by default (set ignoreCase for "
            "case-insensitive). The pattern is a regex; set literal: true to match plain "
            "text. Respects .gitignore; never searches .git. Output is truncated to 100 "
            "matches or 32KB (whichever is hit first). Long lines are truncated to 500 "
            "chars. For conceptual or half-known-name queries, prefer the search tool."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (regex or literal string)",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search (default: current directory)",
                },
                "glob": {
                    "type": "string",
                    "description": "Filter files by glob pattern, e.g. '*.ts' or '**/*.spec.ts'",
                },
                "ignoreCase": {
                    "type": "boolean",
                    "description": "Case-insensitive search (default: false)",
                },
                "literal": {
                    "type": "boolean",
                    "description": (
                        "Treat pattern as literal string instead of regex (default: false)"
                    ),
                },
                "context": {
                    "type": "number",
                    "description": (
                        "Number of lines to show before and after each match (default: 0)"
                    ),
                },
                "limit": {
                    "type": "number",
                    "description": "Maximum number of matches to return (default: 100)",
                },
            },
            "required": ["pattern"],
        },
        execute=execute,
    )

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnusedCallResult=false, reportCallIssue=false, reportReturnType=false, reportMissingTypeArgument=false
"""Ls tool for listing directory contents.

Mechanical port of ``core/tools/ls.ts`` (execute path). TUI rendering is not
ported.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from cortex.agent.types import AgentTool, AgentToolResult
from cortex.ai.types import TextContent
from cortex.code.tools.native_search import minimatch
from cortex.code.tools.path_utils import resolve_to_cwd
from cortex.code.tools.truncate import (
    DEFAULT_MAX_BYTES,
    TruncationOptions,
    TruncationResult,
    format_size,
    truncate_head,
)

_DEFAULT_LIMIT = 500
_MAX_SAFE = 2**53 - 1


@dataclass
class LsToolDetails:
    truncation: TruncationResult | None = None
    entry_limit_reached: int | None = None


@dataclass
class _DirEntry:
    name: str
    is_directory: bool


class LsOperations(Protocol):
    """Pluggable operations for the ls tool."""

    def exists(self, absolute_path: str) -> bool: ...
    def is_dir(self, absolute_path: str) -> bool: ...
    def readdir_entries(self, absolute_path: str) -> list[_DirEntry]: ...


class _DefaultLsOperations:
    def exists(self, absolute_path: str) -> bool:
        return os.path.exists(absolute_path)

    def is_dir(self, absolute_path: str) -> bool:
        return os.path.isdir(absolute_path)

    def readdir_entries(self, absolute_path: str) -> list[_DirEntry]:
        return [_DirEntry(name=e.name, is_directory=e.is_dir()) for e in os.scandir(absolute_path)]


class LsToolOptions:
    """Options for the ls tool."""

    def __init__(self, operations: LsOperations | None = None) -> None:
        self.operations = operations


def create_ls_tool(
    cwd: str,
    options: LsToolOptions | None = None,
) -> AgentTool[dict[str, Any], LsToolDetails | None]:
    """Create an ls tool."""
    ops: LsOperations = (
        options.operations if options and options.operations else _DefaultLsOperations()
    )

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
        on_update: Callable[..., Any] | None = None,
    ) -> AgentToolResult[LsToolDetails | None]:
        if signal is not None and getattr(signal, "aborted", False):
            raise RuntimeError("Operation aborted")

        path = params.get("path")
        limit = params.get("limit")
        ignore = params.get("ignore")

        dir_path = resolve_to_cwd(path or ".", cwd)
        effective_limit = limit if limit is not None else _DEFAULT_LIMIT

        if not ops.exists(dir_path):
            raise RuntimeError(f"Path not found: {dir_path}")

        if not ops.is_dir(dir_path):
            raise RuntimeError(f"Not a directory: {dir_path}")

        ignore_patterns = [p for p in (ignore or []) if isinstance(p, str) and len(p) > 0]

        try:
            dirents = ops.readdir_entries(dir_path)
        except OSError as e:
            raise RuntimeError(f"Cannot read directory: {dir_path}") from e

        filtered = dirents
        if ignore_patterns:
            filtered = [
                d
                for d in dirents
                if not any(minimatch(d.name, pattern, dot=True) for pattern in ignore_patterns)
            ]
        filtered.sort(key=lambda d: d.name.lower())

        results: list[str] = []
        entry_limit_reached = False
        for entry in filtered:
            if len(results) >= effective_limit:
                entry_limit_reached = True
                break
            results.append(entry.name + ("/" if entry.is_directory else ""))

        if len(results) == 0:
            return AgentToolResult(content=[TextContent(text="(empty directory)")], details=None)

        raw_output = "\n".join(results)
        truncation = truncate_head(raw_output, TruncationOptions(max_lines=_MAX_SAFE))
        output = truncation.content
        details = LsToolDetails()
        has_detail = False
        notices: list[str] = []
        if entry_limit_reached:
            notices.append(
                f"{effective_limit} entries limit reached. Use limit={effective_limit * 2} for more"
            )
            details.entry_limit_reached = effective_limit
            has_detail = True
        if truncation.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
            details.truncation = truncation
            has_detail = True
        if notices:
            output += f"\n\n[{'. '.join(notices)}]"

        return AgentToolResult(
            content=[TextContent(text=output)],
            details=details if has_detail else None,
        )

    return AgentTool(
        name="ls",
        label="ls",
        description=(
            "List directory contents. Returns entries sorted alphabetically, with '/' "
            "suffix for directories. Includes dotfiles. Lists a single directory (not "
            "recursive) and shows everything on disk; pass 'ignore' glob patterns to skip "
            "entries like node_modules or .git. Output is truncated to "
            f"{_DEFAULT_LIMIT} entries or {DEFAULT_MAX_BYTES // 1024}KB (whichever is hit "
            "first)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to list (default: current directory)",
                },
                "limit": {
                    "type": "number",
                    "description": "Maximum number of entries to return (default: 500)",
                },
                "ignore": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Glob patterns matched against entry names to exclude, e.g. "
                        "['node_modules', '*.log', '.git']. Matching is on the entry name "
                        "only (ls is non-recursive); dotfiles are matched."
                    ),
                },
            },
        },
        execute=execute,
    )

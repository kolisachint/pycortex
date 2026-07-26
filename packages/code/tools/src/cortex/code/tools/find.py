# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnusedCallResult=false, reportCallIssue=false, reportReturnType=false, reportMissingTypeArgument=false
"""Find tool for glob-based file search.

Mechanical port of ``core/tools/find.ts`` (execute path). The TS drives the fast
path with ``fd`` and falls back to a pure-JS walk; this port always uses the
native walk (:mod:`cortex.code.tools.native_search`), which reproduces fd's
.gitignore handling and hidden-file inclusion. TUI rendering is not ported.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cortex.agent.types import AgentTool, AgentToolResult
from cortex.ai.types import TextContent
from cortex.code.tools.native_search import NativeFindOptions, native_find
from cortex.code.tools.path_utils import resolve_to_cwd
from cortex.code.tools.truncate import (
    DEFAULT_MAX_BYTES,
    TruncationOptions,
    TruncationResult,
    format_size,
    truncate_head,
)

_DEFAULT_LIMIT = 1000
_NO_RESULTS_MESSAGE = "No files found matching pattern"
_MAX_SAFE = 2**53 - 1


@dataclass
class FindToolDetails:
    truncation: TruncationResult | None = None
    result_limit_reached: int | None = None


def _compress_paths(paths: list[str]) -> str:
    """Group files in the same directory to shorten output."""
    if len(paths) == 0:
        return ""

    sorted_paths = sorted(paths)
    dir_map: dict[str, list[str]] = {}
    for p in sorted_paths:
        directory = os.path.dirname(p)
        base = os.path.basename(p)
        dir_map.setdefault(directory, []).append(base)

    result: list[str] = []

    for directory, files in dir_map.items():
        if len(files) <= 2:
            for f in files:
                result.append(f if directory == "." else f"{directory}/{f}")
            continue

        ext_map: dict[str, list[str]] = {}
        for f in files:
            stem, ext = os.path.splitext(f)
            ext_map.setdefault(ext, []).append(stem)

        if len(ext_map) == 1 and len(files) >= 3:
            ext, stems = next(iter(ext_map.items()))
            display_dir = "" if directory == "." else f"{directory}/"
            result.append(f"{display_dir}{{{','.join(stems)}}}{ext}")
            continue

        any_folded = False
        sub_results: list[str] = []
        for ext, stems in ext_map.items():
            if len(stems) >= 3:
                display_dir = "" if directory == "." else f"{directory}/"
                sub_results.append(f"{display_dir}{{{','.join(stems)}}}{ext}")
                any_folded = True
            else:
                for stem in stems:
                    sub_results.append(
                        f"{stem}{ext}" if directory == "." else f"{directory}/{stem}{ext}"
                    )

        if any_folded or len(files) > 6:
            result.extend(sub_results)
        else:
            for f in files:
                result.append(f if directory == "." else f"{directory}/{f}")

    return "\n".join(result)


def create_find_tool(
    cwd: str,
    options: Any = None,
) -> AgentTool[dict[str, Any], FindToolDetails | None]:
    """Create a find tool."""

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
        on_update: Callable[..., Any] | None = None,
    ) -> AgentToolResult[FindToolDetails | None]:
        if signal is not None and getattr(signal, "aborted", False):
            raise RuntimeError("Operation aborted")

        raw_pattern = params.get("pattern", "")
        search_dir = params.get("path")
        raw_exclude = params.get("exclude")
        type_ = params.get("type")
        depth = params.get("depth")
        limit = params.get("limit")
        compress = bool(params.get("compress", False))

        search_path = resolve_to_cwd(search_dir or ".", cwd)
        effective_limit = max(1, limit if limit is not None else _DEFAULT_LIMIT)

        patterns = raw_pattern if isinstance(raw_pattern, list) else [raw_pattern]
        if raw_exclude:
            exclude_patterns = raw_exclude if isinstance(raw_exclude, list) else [raw_exclude]
        else:
            exclude_patterns = []
        all_ignore = ["**/node_modules/**", "**/.git/**", *exclude_patterns]
        type_filter = "d" if type_ == "d" else "l" if type_ == "l" else "f"

        def emit(relativized: list[str]) -> AgentToolResult[FindToolDetails | None]:
            if len(relativized) == 0:
                return AgentToolResult(
                    content=[TextContent(text=_NO_RESULTS_MESSAGE)], details=None
                )
            unique = sorted(set(relativized))
            result_limit_reached = len(unique) > effective_limit
            truncated = unique[:effective_limit]
            raw_output = _compress_paths(truncated) if compress else "\n".join(truncated)
            truncation = truncate_head(raw_output, TruncationOptions(max_lines=_MAX_SAFE))
            result_output = truncation.content
            details = FindToolDetails()
            has_detail = False
            notices: list[str] = []
            if result_limit_reached:
                notices.append(
                    f"{effective_limit} result{'' if effective_limit == 1 else 's'} limit "
                    f"reached. Use limit={effective_limit * 2} for more, or refine pattern"
                )
                details.result_limit_reached = effective_limit
                has_detail = True
            if truncation.truncated:
                notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
                details.truncation = truncation
                has_detail = True
            if notices:
                result_output += f"\n\n[{'. '.join(notices)}]"
            return AgentToolResult(
                content=[TextContent(text=result_output)],
                details=details if has_detail else None,
            )

        if not os.path.exists(search_path):
            raise RuntimeError(f"Path not found: {search_path}")

        native_results = native_find(
            search_path,
            NativeFindOptions(
                patterns=patterns,
                type=type_filter,
                exclude_globs=all_ignore,
                max_depth=depth,
                always_skip_dirs={".git", "node_modules"},
                signal=signal,
            ),
        )
        if signal is not None and getattr(signal, "aborted", False):
            raise RuntimeError("Operation aborted")
        return emit(native_results)

    return AgentTool(
        name="find",
        label="find",
        description=(
            "Search for files by one or more glob patterns (OR logic across an array). "
            "Optionally filter by entry type (files/dirs/symlinks), directory depth, and "
            "extra exclusions. Returns matching paths relative to the search directory. "
            f"Respects .gitignore. Output is truncated to {_DEFAULT_LIMIT} results or "
            f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "description": (
                        "Glob pattern(s) to match files. Pass one pattern or an array for "
                        "OR logic, e.g. '*.ts', 'src/**/*.spec.ts', or ['src/**/*.ts', "
                        "'test/**/*.ts']."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: current directory)",
                },
                "exclude": {
                    "description": (
                        "Additional exclusion glob(s), e.g. '**/*.test.ts' or "
                        "['**/dist/**', '**/build/**']."
                    ),
                },
                "type": {
                    "description": (
                        "Filter by entry type: 'f' files, 'd' directories, 'l' symlinks "
                        "(default: 'f')."
                    ),
                },
                "depth": {"type": "number", "description": "Maximum directory depth to search."},
                "limit": {
                    "type": "number",
                    "description": "Maximum number of results (default: 1000).",
                },
                "compress": {
                    "type": "boolean",
                    "description": (
                        "Group files in the same directory to shorten output (default: false)."
                    ),
                },
            },
            "required": ["pattern"],
        },
        execute=execute,
    )

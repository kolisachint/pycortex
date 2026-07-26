"""Cortex code tools package.

Mechanical port of ``core/tools/{read,bash,edit,write,grep,find,ls}.ts`` and
their shared helpers (execute-level parity). The ``search`` tool, TUI rendering
(``renderCall``/``renderResult``), the file-mutation queue, and image
auto-resize are out of scope for this leaf.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from cortex.agent.types import AgentTool, AgentToolResult
from cortex.ai.types import TextContent
from cortex.code.tools.bash import (
    BashExecResult,
    BashOperations,
    BashToolDetails,
    BashToolOptions,
    create_bash_tool,
    create_local_bash_operations,
)
from cortex.code.tools.edit import (
    EditOperations,
    EditToolDetails,
    EditToolOptions,
    create_edit_tool,
)
from cortex.code.tools.edit_diff import (
    Edit,
    EditDiffError,
    EditDiffResult,
    apply_edits_to_normalized_content,
    compute_edit_diff,
    compute_edits_diff,
    generate_diff_string,
)
from cortex.code.tools.find import FindToolDetails, create_find_tool
from cortex.code.tools.grep import (
    GrepOperations,
    GrepToolDetails,
    GrepToolOptions,
    create_grep_tool,
)
from cortex.code.tools.ls import LsOperations, LsToolDetails, LsToolOptions, create_ls_tool
from cortex.code.tools.read import (
    ReadOperations,
    ReadToolDetails,
    ReadToolOptions,
    create_read_tool,
)
from cortex.code.tools.render_utils import get_text_output, shorten_path, str_value
from cortex.code.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    GREP_MAX_LINE_LENGTH,
    TruncationOptions,
    TruncationResult,
    format_size,
    truncate_head,
    truncate_line,
    truncate_tail,
)
from cortex.code.tools.write import WriteOperations, WriteToolOptions, create_write_tool

# Default limits surfaced for callers/tests.
GREP_DEFAULT_LIMIT = 100
FIND_DEFAULT_LIMIT = 1000
LS_DEFAULT_LIMIT = 500


@dataclass
class ToolsOptions:
    """Options for the built-in tool bundles.

    ``cwd`` is the working directory the tools operate in. Per-tool option keys
    (``read``/``bash``/...) mirror the TS ``ToolsOptions`` and are passed through
    to the corresponding factory.
    """

    cwd: str | None = None
    read: ReadToolOptions | None = None
    bash: BashToolOptions | None = None
    write: WriteToolOptions | None = None
    edit: EditToolOptions | None = None
    grep: GrepToolOptions | None = None
    find: Any | None = None
    ls: LsToolOptions | None = None


_CODING_TOOL_NAMES = ["read", "bash", "edit", "write", "grep", "find", "ls"]
_READ_ONLY_TOOL_NAMES = ["read", "grep", "find", "ls"]

_TOOL_FACTORIES = {
    "read": create_read_tool,
    "bash": create_bash_tool,
    "edit": create_edit_tool,
    "write": create_write_tool,
    "grep": create_grep_tool,
    "find": create_find_tool,
    "ls": create_ls_tool,
}


def _resolve_cwd(options: ToolsOptions | None) -> str:
    if options and options.cwd:
        return str(os.path.abspath(options.cwd))
    return os.getcwd()


def _create_tool(name: str, cwd: str, options: ToolsOptions | None) -> AgentTool[Any, Any]:
    factory = _TOOL_FACTORIES[name]
    tool_options = getattr(options, name, None) if options else None
    return factory(cwd, tool_options)


def create_coding_tools(
    cwd: str | None = None,
    options: ToolsOptions | None = None,
) -> list[AgentTool[Any, Any]]:
    """The default coding bundle (read/bash/edit/write/grep/find/ls)."""
    resolved = str(os.path.abspath(cwd)) if cwd else _resolve_cwd(options)
    return [_create_tool(name, resolved, options) for name in _CODING_TOOL_NAMES]


def create_read_only_tools(
    cwd: str | None = None,
    options: ToolsOptions | None = None,
) -> list[AgentTool[Any, Any]]:
    """Read-only exploration bundle (read/grep/find/ls)."""
    resolved = str(os.path.abspath(cwd)) if cwd else _resolve_cwd(options)
    return [_create_tool(name, resolved, options) for name in _READ_ONLY_TOOL_NAMES]


def _text_result(text: str) -> AgentToolResult[None]:
    """Create a text-only tool result (helper used by tests)."""
    return AgentToolResult(content=[TextContent(text=text)], details=None)


def _count_occurrences(haystack: str, needle: str) -> int:
    """Count non-overlapping occurrences of ``needle`` in ``haystack``."""
    if not needle:
        return 0
    count = 0
    index = haystack.find(needle)
    while index != -1:
        count += 1
        index = haystack.find(needle, index + len(needle))
    return count


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "FIND_DEFAULT_LIMIT",
    "GREP_DEFAULT_LIMIT",
    "GREP_MAX_LINE_LENGTH",
    "LS_DEFAULT_LIMIT",
    "BashExecResult",
    "BashOperations",
    "BashToolDetails",
    "BashToolOptions",
    "Edit",
    "EditDiffError",
    "EditDiffResult",
    "EditOperations",
    "EditToolDetails",
    "EditToolOptions",
    "FindToolDetails",
    "GrepOperations",
    "GrepToolDetails",
    "GrepToolOptions",
    "LsOperations",
    "LsToolDetails",
    "LsToolOptions",
    "ReadOperations",
    "ReadToolDetails",
    "ReadToolOptions",
    "ToolsOptions",
    "TruncationOptions",
    "TruncationResult",
    "WriteOperations",
    "WriteToolOptions",
    "apply_edits_to_normalized_content",
    "compute_edit_diff",
    "compute_edits_diff",
    "create_bash_tool",
    "create_coding_tools",
    "create_edit_tool",
    "create_find_tool",
    "create_grep_tool",
    "create_local_bash_operations",
    "create_ls_tool",
    "create_read_only_tools",
    "create_read_tool",
    "create_write_tool",
    "format_size",
    "generate_diff_string",
    "get_text_output",
    "shorten_path",
    "str_value",
    "truncate_head",
    "truncate_line",
    "truncate_tail",
    "_count_occurrences",
    "_text_result",
]

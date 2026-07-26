"""Default tool bundle for headless agent execution.

Mechanical port of hoocode's ``packages/agent/src/tools/default-tools.ts``.

Provides the same built-in tools (bash/read/edit/write/grep/find/ls) without
CLI or TUI dependencies so they can run in a separate process.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cortex.agent.types import AgentTool, AgentToolResult
from cortex.ai.types import TextContent

# ============================================================================
# Types
# ============================================================================


@dataclass
class DefaultToolsOptions:
    """Options for creating default tools."""

    cwd: str | None = None
    """Working directory the tools operate in. Defaults to os.getcwd()."""


class ExecutionEnv(Protocol):
    """Protocol for execution environment."""

    def exec(
        self,
        command: str,
        timeout: int | None = None,
        signal: Any = None,
    ) -> dict[str, Any]:
        """Execute a shell command."""
        ...

    def read_text_file(self, path: str) -> str:
        """Read a text file."""
        ...

    def write_file(self, path: str, content: str) -> None:
        """Write content to a file."""
        ...

    def list_dir(self, path: str) -> list[dict[str, str]]:
        """List directory contents."""
        ...

    def file_info(self, path: str) -> dict[str, str]:
        """Get file info."""
        ...


# ============================================================================
# Helpers
# ============================================================================

DEFAULT_MAX_LINES = 800
"""Maximum number of lines to return from a tool."""

DEFAULT_MAX_BYTES = 32 * 1024
"""Maximum bytes to return from a tool (32KB)."""


def _text_result(text: str) -> AgentToolResult[None]:
    """Create a text result."""
    return AgentToolResult(
        content=[TextContent(text=text)],
        details=None,
    )


def _resolve_to_cwd(cwd: str, path: str) -> str:
    """Resolve a path relative to cwd."""
    if os.path.isabs(path):
        return path
    return str(Path(cwd) / path)


def _format_size(size: int) -> str:
    """Format byte size as human-readable string."""
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    else:
        return f"{size / (1024 * 1024):.1f}MB"


def _truncate_line(text: str, max_len: int = 500) -> str:
    """Truncate a line to max_len characters."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


# ============================================================================
# Bash Tool
# ============================================================================


def create_bash_tool(env: Any, cwd: str) -> AgentTool[dict[str, Any], None]:
    """Create a bash tool."""

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
    ) -> AgentToolResult[None]:
        command = params.get("command", "")
        timeout = params.get("timeout")

        try:
            result = env.exec(command, timeout=timeout, signal=signal)
            combined = result.get("stdout", "") + result.get("stderr", "")
            exit_code = result.get("exit_code", 0)
        except Exception as e:
            msg = str(e)
            if msg.startswith("timeout:"):
                raise RuntimeError(f"Command timed out after {timeout}s") from e
            raise

        # Truncate output
        lines = combined.split("\n")
        if len(lines) > DEFAULT_MAX_LINES:
            lines = lines[-DEFAULT_MAX_LINES:]
            combined = "\n".join(lines)
            combined = f"[Output truncated: showing last {DEFAULT_MAX_LINES} lines]\n{combined}"

        text = combined
        if exit_code != 0:
            text = f"{text}\nExit code: {exit_code}" if text else f"Exit code: {exit_code}"

        return _text_result(text if text else "(no output)")

    return AgentTool(
        name="bash",
        label="bash",
        description=(
            "Execute a bash command in the current working directory. "
            "Returns stdout and stderr. Output is truncated to last "
            f"{DEFAULT_MAX_LINES} lines or {DEFAULT_MAX_BYTES // 1024}KB."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Bash command to execute"},
                "timeout": {"type": "number", "description": "Timeout in seconds (optional)"},
            },
            "required": ["command"],
        },
        execute=execute,
    )


# ============================================================================
# Read Tool
# ============================================================================


def create_read_tool(env: Any, cwd: str) -> AgentTool[dict[str, Any], None]:
    """Create a read tool."""

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
    ) -> AgentToolResult[None]:
        path = _resolve_to_cwd(cwd, params.get("path", ""))
        offset = params.get("offset", 1)
        limit = params.get("limit")

        content = env.read_text_file(path)
        lines = content.split("\n")
        total_lines = len(lines)

        offset = max(1, int(offset))
        if offset > total_lines:
            raise ValueError(f"Offset {offset} is past the end of the file ({total_lines} lines)")

        lines = lines[offset - 1 :]
        if limit is not None:
            lines = lines[: max(0, int(limit))]

        text = "\n".join(lines)
        if len(lines) < total_lines - offset + 1:
            last_shown = offset - 1 + len(lines)
            text = (
                f"{text}\n[Truncated: showing lines {offset}-{last_shown} "
                f"of {total_lines}. Continue with offset={last_shown + 1}]"
            )

        return _text_result(text)

    return AgentTool(
        name="read",
        label="read",
        description=(
            "Read the contents of a text file. Output is truncated to "
            f"{DEFAULT_MAX_LINES} lines or {DEFAULT_MAX_BYTES // 1024}KB."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to read"},
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


# ============================================================================
# Edit Tool
# ============================================================================


def _count_occurrences(haystack: str, needle: str) -> int:
    """Count occurrences of needle in haystack."""
    if not needle:
        return 0
    count = 0
    index = haystack.find(needle)
    while index != -1:
        count += 1
        index = haystack.find(needle, index + len(needle))
    return count


def create_edit_tool(env: Any, cwd: str) -> AgentTool[dict[str, Any], None]:
    """Create an edit tool."""

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
    ) -> AgentToolResult[None]:
        path = _resolve_to_cwd(cwd, params.get("path", ""))
        edits = params.get("edits", [])

        if not edits:
            raise ValueError("No edits provided")

        original = env.read_text_file(path)
        content = original

        for i, edit in enumerate(edits):
            old_text = edit.get("oldText", "")
            new_text = edit.get("newText", "")

            occurrences = _count_occurrences(original, old_text)
            if occurrences == 0:
                raise ValueError(f"edits[{i}].oldText not found in {path}")
            if occurrences > 1:
                raise ValueError(
                    f"edits[{i}].oldText matches {occurrences} locations in {path}; "
                    "add surrounding context to make it unique"
                )
            if old_text not in content:
                raise ValueError(
                    f"edits[{i}].oldText overlaps with an earlier edit in the same call"
                )

            content = content.replace(old_text, new_text, 1)

        env.write_file(path, content)
        return _text_result(f"Applied {len(edits)} edit{'s' if len(edits) != 1 else ''} to {path}")

    return AgentTool(
        name="edit",
        label="edit",
        description=(
            "Edit a file by replacing exact text. Each edit's "
            "oldText must appear exactly once in the file."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to edit"},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "oldText": {"type": "string", "description": "Exact text to replace"},
                            "newText": {"type": "string", "description": "Replacement text"},
                        },
                        "required": ["oldText", "newText"],
                    },
                    "description": "One or more targeted replacements",
                },
            },
            "required": ["path", "edits"],
        },
        execute=execute,
    )


# ============================================================================
# Write Tool
# ============================================================================


def create_write_tool(env: Any, cwd: str) -> AgentTool[dict[str, Any], None]:
    """Create a write tool."""

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
    ) -> AgentToolResult[None]:
        path = _resolve_to_cwd(cwd, params.get("path", ""))
        content = params.get("content", "")

        # Create parent directories if needed
        Path(path).parent.mkdir(parents=True, exist_ok=True)

        env.write_file(path, content)
        size = len(content.encode("utf-8"))
        return _text_result(f"Wrote {_format_size(size)} to {path}")

    return AgentTool(
        name="write",
        label="write",
        description=(
            "Write content to a file, creating parent directories "
            "as needed. Overwrites existing files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to write"},
                "content": {"type": "string", "description": "Content to write to the file"},
            },
            "required": ["path", "content"],
        },
        execute=execute,
    )


# ============================================================================
# Grep Tool
# ============================================================================

GREP_DEFAULT_LIMIT = 100


def create_grep_tool(env: Any, cwd: str) -> AgentTool[dict[str, Any], None]:
    """Create a grep tool."""

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
    ) -> AgentToolResult[None]:
        pattern = params.get("pattern", "")
        search_path = params.get("path", ".")
        glob_pattern = params.get("glob")
        ignore_case = params.get("ignoreCase", False)
        literal = params.get("literal", False)
        limit = params.get("limit", GREP_DEFAULT_LIMIT)

        source = re.escape(pattern) if literal else pattern
        regex = re.compile(source, re.IGNORECASE if ignore_case else 0)

        search_root = _resolve_to_cwd(cwd, search_path)

        # Collect files
        files: list[str] = []
        if os.path.isfile(search_root):
            files = [os.path.basename(search_root)]
        elif os.path.isdir(search_root):
            for root, dirs, filenames in os.walk(search_root):
                # Skip .git and node_modules
                dirs[:] = [d for d in dirs if d not in (".git", "node_modules")]
                for filename in filenames:
                    rel_path = os.path.relpath(os.path.join(root, filename), search_root)
                    files.append(rel_path.replace("\\", "/"))
                    if len(files) >= 50000:
                        break
                if len(files) >= 50000:
                    break

        matches: list[str] = []
        limit_reached = False

        for file in files:
            if glob_pattern and not fnmatch.fnmatch(file, glob_pattern):
                continue

            abs_path = (
                os.path.join(search_root, file) if os.path.isdir(search_root) else search_root
            )
            try:
                with open(abs_path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            # Skip binary files
            if "\0" in content:
                continue

            display_path = os.path.relpath(abs_path, cwd).replace("\\", "/")

            for i, line in enumerate(content.split("\n")):
                if regex.search(line):
                    matches.append(f"{display_path}:{i + 1}: {_truncate_line(line)}")
                    if len(matches) >= limit:
                        limit_reached = True
                        break
            if limit_reached:
                break

        if not matches:
            return _text_result("No matches found")

        text = "\n".join(matches)
        if limit_reached:
            text = f"{text}\n[Match limit of {limit} reached; refine the pattern or raise limit]"

        return _text_result(text)

    return AgentTool(
        name="grep",
        label="grep",
        description=(
            f"Search file contents for a pattern. Returns matching lines "
            f"with file paths and line numbers. Output is truncated to "
            f"{GREP_DEFAULT_LIMIT} matches by default."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (regex or literal string)",
                },
                "path": {"type": "string", "description": "Directory or file to search"},
                "glob": {"type": "string", "description": "Filter files by glob pattern"},
                "ignoreCase": {"type": "boolean", "description": "Case-insensitive search"},
                "literal": {"type": "boolean", "description": "Treat pattern as literal string"},
                "limit": {"type": "number", "description": "Maximum number of matches"},
            },
            "required": ["pattern"],
        },
        execute=execute,
    )


# ============================================================================
# Find Tool
# ============================================================================

FIND_DEFAULT_LIMIT = 1000


def create_find_tool(env: Any, cwd: str) -> AgentTool[dict[str, Any], None]:
    """Create a find tool."""

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
    ) -> AgentToolResult[None]:
        pattern = params.get("pattern", "")
        search_path = params.get("path", ".")
        limit = params.get("limit", FIND_DEFAULT_LIMIT)

        search_root = _resolve_to_cwd(cwd, search_path)

        # Collect files
        files: list[str] = []
        if os.path.isdir(search_root):
            for root, dirs, filenames in os.walk(search_root):
                dirs[:] = [d for d in dirs if d not in (".git", "node_modules")]
                for filename in filenames:
                    rel_path = os.path.relpath(os.path.join(root, filename), search_root)
                    files.append(rel_path.replace("\\", "/"))
                    if len(files) >= 50000:
                        break
                if len(files) >= 50000:
                    break

        matched: list[str] = []
        limit_reached = False

        for file in files:
            if fnmatch.fnmatch(file, pattern):
                matched.append(file)
                if len(matched) >= limit:
                    limit_reached = True
                    break

        if not matched:
            return _text_result("No files found")

        text = "\n".join(matched)
        if limit_reached:
            text = f"{text}\n[Result limit of {limit} reached]"

        return _text_result(text)

    return AgentTool(
        name="find",
        label="find",
        description=(
            f"Search for files by glob pattern. Returns matching file paths. "
            f"Output is truncated to {FIND_DEFAULT_LIMIT} results by default."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern to match files"},
                "path": {"type": "string", "description": "Directory to search in"},
                "limit": {"type": "number", "description": "Maximum number of results"},
            },
            "required": ["pattern"],
        },
        execute=execute,
    )


# ============================================================================
# Ls Tool
# ============================================================================

LS_DEFAULT_LIMIT = 500


def create_ls_tool(env: Any, cwd: str) -> AgentTool[dict[str, Any], None]:
    """Create an ls tool."""

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
    ) -> AgentToolResult[None]:
        path = params.get("path", ".")
        limit = params.get("limit", LS_DEFAULT_LIMIT)

        target = _resolve_to_cwd(cwd, path)

        if not os.path.isdir(target):
            raise ValueError(f"Not a directory: {target}")

        entries = env.list_dir(target)

        def _get_name(e: Any) -> str:
            if isinstance(e, dict):
                return e.get("name", "")
            return ""

        entries.sort(key=_get_name)

        shown = entries[:limit]
        lines = [
            f"{entry['name']}/" if entry.get("kind") == "directory" else entry["name"]
            for entry in shown
        ]

        text = "\n".join(lines)
        if len(entries) > limit:
            text = f"{text}\n[Entry limit of {limit} reached; {len(entries) - limit} more entries]"

        return _text_result(text if text else "(empty directory)")

    return AgentTool(
        name="ls",
        label="ls",
        description=(
            f"List directory contents. Returns entries sorted alphabetically, "
            f"with '/' suffix for directories. Output is truncated to "
            f"{LS_DEFAULT_LIMIT} entries by default."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to list"},
                "limit": {"type": "number", "description": "Maximum number of entries to return"},
            },
        },
        execute=execute,
    )


# ============================================================================
# Bundle
# ============================================================================


def get_default_tools(
    opts: DefaultToolsOptions | None = None,
) -> list[AgentTool[dict[str, Any], None]]:
    """Build the default headless tool bundle.

    Args:
        opts: Options for creating the tools.

    Returns:
        List of default tools bound to the given working directory.
    """
    cwd = opts.cwd if opts and opts.cwd else os.getcwd()
    cwd = str(Path(cwd).resolve())

    # Create a simple execution environment
    env = _DefaultExecutionEnv(cwd)

    return [
        create_bash_tool(env, cwd),
        create_read_tool(env, cwd),
        create_edit_tool(env, cwd),
        create_write_tool(env, cwd),
        create_grep_tool(env, cwd),
        create_find_tool(env, cwd),
        create_ls_tool(env, cwd),
    ]


class _DefaultExecutionEnv:
    """Default execution environment using subprocess."""

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd

    def exec(
        self,
        command: str,
        timeout: int | None = None,
        signal: Any = None,
    ) -> dict[str, Any]:
        """Execute a shell command."""
        import subprocess

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self._cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            }
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"timeout: Command timed out after {timeout}s") from e

    def read_text_file(self, path: str) -> str:
        """Read a text file."""
        return Path(path).read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        """Write content to a file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")

    def list_dir(self, path: str) -> list[dict[str, str]]:
        """List directory contents."""
        entries: list[dict[str, str]] = []
        for entry in Path(path).iterdir():
            kind = "directory" if entry.is_dir() else "file"
            entries.append({"name": entry.name, "kind": kind})
        return entries

    def file_info(self, path: str) -> dict[str, str]:
        """Get file info."""
        p = Path(path)
        if p.is_dir():
            return {"kind": "directory", "name": p.name}
        elif p.is_file():
            return {"kind": "file", "name": p.name}
        else:
            return {"kind": "unknown", "name": p.name}

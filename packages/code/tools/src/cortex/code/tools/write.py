# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnusedCallResult=false, reportCallIssue=false, reportReturnType=false, reportMissingTypeArgument=false
"""Write tool for creating/overwriting files.

Mechanical port of ``core/tools/write.ts`` (execute path). The file-mutation
queue and TUI rendering are not ported in this leaf.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from cortex.agent.types import AgentTool, AgentToolResult
from cortex.ai.types import TextContent
from cortex.code.tools.path_utils import resolve_to_cwd


class WriteOperations(Protocol):
    """Pluggable operations for the write tool."""

    def write_file(self, absolute_path: str, content: str) -> None: ...
    def mkdir(self, directory: str) -> None: ...


class _DefaultWriteOperations:
    def write_file(self, absolute_path: str, content: str) -> None:
        Path(absolute_path).write_text(content, encoding="utf-8")

    def mkdir(self, directory: str) -> None:
        Path(directory).mkdir(parents=True, exist_ok=True)


class WriteToolOptions:
    """Options for the write tool."""

    def __init__(self, operations: WriteOperations | None = None) -> None:
        self.operations = operations


def create_write_tool(
    cwd: str,
    options: WriteToolOptions | None = None,
) -> AgentTool[dict[str, Any], None]:
    """Create a write tool."""
    ops: WriteOperations = (
        options.operations if options and options.operations else _DefaultWriteOperations()
    )

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
        on_update: Callable[..., Any] | None = None,
    ) -> AgentToolResult[None]:
        if signal is not None and getattr(signal, "aborted", False):
            raise RuntimeError("Operation aborted")

        path = params.get("path", "")
        content = params.get("content", "")

        absolute_path = resolve_to_cwd(path, cwd)
        directory = os.path.dirname(absolute_path)

        ops.mkdir(directory)
        ops.write_file(absolute_path, content)

        return AgentToolResult(
            content=[
                TextContent(
                    text=f"Successfully wrote {len(content.encode('utf-8'))} bytes to {path}"
                )
            ],
            details=None,
        )

    return AgentTool(
        name="write",
        label="write",
        description=(
            "Write content to a file. Creates the file if it doesn't exist, overwrites "
            "if it does. Automatically creates parent directories."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to write (relative or absolute)",
                },
                "content": {"type": "string", "description": "Content to write to the file"},
            },
            "required": ["path", "content"],
        },
        execute=execute,
    )

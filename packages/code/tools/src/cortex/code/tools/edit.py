# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnusedCallResult=false, reportCallIssue=false, reportReturnType=false, reportMissingTypeArgument=false
"""Edit tool for exact-text replacement.

Mechanical port of ``core/tools/edit.ts`` (execute path). The file-mutation
queue, legacy-argument shim, and TUI rendering are not ported in this leaf.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Protocol

from cortex.agent.types import AgentTool, AgentToolResult
from cortex.ai.types import TextContent
from cortex.code.tools.edit_diff import (
    Edit,
    apply_edits_to_normalized_content,
    detect_line_ending,
    generate_diff_string,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)
from cortex.code.tools.path_utils import resolve_to_cwd

# Maximum characters for diff output sent to the LLM.
_MAX_DIFF_CHARS = 4000


class EditToolDetails:
    """Structured details for the edit result."""

    def __init__(self, diff: str, first_changed_line: int | None = None) -> None:
        self.diff = diff
        self.first_changed_line = first_changed_line


class EditOperations(Protocol):
    """Pluggable operations for the edit tool."""

    def read_file(self, absolute_path: str) -> bytes: ...
    def write_file(self, absolute_path: str, content: str) -> None: ...
    def access(self, absolute_path: str) -> None: ...


class _DefaultEditOperations:
    def read_file(self, absolute_path: str) -> bytes:
        with open(absolute_path, "rb") as fh:
            return fh.read()

    def write_file(self, absolute_path: str, content: str) -> None:
        with open(absolute_path, "w", encoding="utf-8") as fh:
            fh.write(content)

    def access(self, absolute_path: str) -> None:
        # Read + write access check; raises on failure (mirrors fs R_OK | W_OK).
        if not os.path.exists(absolute_path):
            raise FileNotFoundError(absolute_path)
        if not os.access(absolute_path, os.R_OK | os.W_OK):
            raise PermissionError(absolute_path)


class EditToolOptions:
    """Options for the edit tool."""

    def __init__(self, operations: EditOperations | None = None) -> None:
        self.operations = operations


def _access_error_code(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return "Error code: ENOENT"
    if isinstance(error, PermissionError):
        return "Error code: EACCES"
    # Mirror JS `String(error)` for a plain Error → "Error: <message>".
    return f"Error: {error}"


def _validate_edit_input(params: dict[str, Any]) -> tuple[str, list[Edit]]:
    raw_edits = params.get("edits")
    if not isinstance(raw_edits, list) or len(raw_edits) == 0:
        raise RuntimeError(
            "Edit tool input is invalid. edits must contain at least one replacement."
        )
    edits = [
        Edit(
            old_text=e.get("oldText", ""),
            new_text=e.get("newText", ""),
            replace_all=bool(e.get("replaceAll", False)),
        )
        for e in raw_edits
    ]
    return params.get("path", ""), edits


def create_edit_tool(
    cwd: str,
    options: EditToolOptions | None = None,
) -> AgentTool[dict[str, Any], EditToolDetails | None]:
    """Create an edit tool."""
    ops: EditOperations = (
        options.operations if options and options.operations else _DefaultEditOperations()
    )

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
        on_update: Callable[..., Any] | None = None,
    ) -> AgentToolResult[EditToolDetails | None]:
        path, edits = _validate_edit_input(params)
        absolute_path = resolve_to_cwd(path, cwd)

        if signal is not None and getattr(signal, "aborted", False):
            raise RuntimeError("Operation aborted")

        try:
            ops.access(absolute_path)
        except Exception as error:  # noqa: BLE001 - reformat as tool error
            raise RuntimeError(
                f"Could not edit file: {path}. {_access_error_code(error)}."
            ) from error

        buffer = ops.read_file(absolute_path)
        raw_content = buffer.decode("utf-8")

        bom, content = strip_bom(raw_content)
        original_ending = detect_line_ending(content)
        normalized_content = normalize_to_lf(content)
        applied = apply_edits_to_normalized_content(normalized_content, edits, path)

        final_content = bom + restore_line_endings(applied.new_content, original_ending)
        ops.write_file(absolute_path, final_content)

        diff_result = generate_diff_string(applied.base_content, applied.new_content)
        diff = diff_result.diff
        if len(diff) > _MAX_DIFF_CHARS:
            diff = f"{diff[:_MAX_DIFF_CHARS]}\n\n[diff truncated for brevity]"

        return AgentToolResult(
            content=[TextContent(text=f"Successfully replaced {len(edits)} block(s) in {path}.")],
            details=EditToolDetails(diff=diff, first_changed_line=diff_result.first_changed_line),
        )

    return AgentTool(
        name="edit",
        label="edit",
        description=(
            "Edit a single file using exact text replacement. Every edits[].oldText "
            "must match a unique, non-overlapping region of the original file, unless "
            "that edit sets replaceAll: true to replace all of its occurrences. If two "
            "changes affect the same block or nearby lines, merge them into one edit "
            "instead of emitting overlapping edits. Do not include large unchanged "
            "regions just to connect distant changes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit (relative or absolute)",
                },
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "oldText": {
                                "type": "string",
                                "description": (
                                    "Exact text for one targeted replacement. It must be "
                                    "unique in the original file (unless replaceAll is true) "
                                    "and must not overlap with any other edits[].oldText in "
                                    "the same call."
                                ),
                            },
                            "newText": {
                                "type": "string",
                                "description": "Replacement text for this targeted edit.",
                            },
                            "replaceAll": {
                                "type": "boolean",
                                "description": (
                                    "When true, replace every occurrence of oldText instead "
                                    "of requiring it to be unique. Use for renaming a "
                                    "symbol/string throughout the file. Default false."
                                ),
                            },
                        },
                        "required": ["oldText", "newText"],
                        "additionalProperties": False,
                    },
                    "description": (
                        "One or more targeted replacements. Each edit is matched against "
                        "the original file, not incrementally. Do not include overlapping "
                        "or nested edits. If two changes touch the same block or nearby "
                        "lines, merge them into one edit instead."
                    ),
                },
            },
            "required": ["path", "edits"],
            "additionalProperties": False,
        },
        execute=execute,
    )

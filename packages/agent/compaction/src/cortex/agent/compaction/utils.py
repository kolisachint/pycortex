# pyright: reportAttributeAccessIssue=false, reportMissingImports=false, reportUnnecessaryIsInstance=false
"""Shared utilities for compaction and branch summarization.

Mechanical port of hoocode's ``packages/agent/src/harness/compaction/utils.ts``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from cortex.agent.types import AgentMessage
from cortex.ai.types import Message

# ============================================================================
# File Operation Tracking
# ============================================================================


@dataclass
class FileOperations:
    """Tracks file operations across tool calls."""

    read: set[str] = field(default_factory=set)
    written: set[str] = field(default_factory=set)
    edited: set[str] = field(default_factory=set)


def create_file_ops() -> FileOperations:
    """Create a new FileOperations instance."""
    return FileOperations()


def extract_file_ops_from_message(message: AgentMessage, file_ops: FileOperations) -> None:
    """Extract file operations from tool calls in an assistant message."""
    if not hasattr(message, "role") or message.role != "assistant":
        return
    if not hasattr(message, "content") or not isinstance(message.content, list):
        return

    for block in message.content:
        # Handle both dict and pydantic model
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if block_type != "toolCall":
            continue
        if isinstance(block, dict):
            args = block.get("arguments")
        else:
            args = getattr(block, "arguments", None)
        if not isinstance(args, dict):
            continue
        path = args.get("path")
        if not isinstance(path, str):
            continue

        name = block.get("name", "") if isinstance(block, dict) else getattr(block, "name", "")
        if name == "read":
            file_ops.read.add(path)
        elif name == "write":
            file_ops.written.add(path)
        elif name == "edit":
            file_ops.edited.add(path)


def compute_file_lists(file_ops: FileOperations) -> tuple[list[str], list[str]]:
    """Compute final file lists from file operations.

    Returns:
        Tuple of (readFiles, modifiedFiles) where readFiles are files only read,
        not modified.
    """
    modified = file_ops.edited | file_ops.written
    read_only = sorted(f for f in file_ops.read if f not in modified)
    modified_files = sorted(modified)
    return read_only, modified_files


def format_file_operations(read_files: list[str], modified_files: list[str]) -> str:
    """Format file operations as XML tags for summary."""
    sections: list[str] = []
    if read_files:
        sections.append(f"<read-files>\n{chr(10).join(read_files)}\n</read-files>")
    if modified_files:
        sections.append(f"<modified-files>\n{chr(10).join(modified_files)}\n</modified-files>")
    if not sections:
        return ""
    return "\n\n" + "\n\n".join(sections)


# ============================================================================
# Message Serialization
# ============================================================================

TOOL_RESULT_MAX_CHARS = 2000
"""Maximum characters for a tool result in serialized summaries."""


def _truncate_for_summary(text: str, max_chars: int) -> str:
    """Truncate text to a maximum character length for summarization."""
    # Apply lossless compression if available
    try:
        from cortex.agent.utils.output_compression import compress_general

        compressed = compress_general(text)
    except ImportError:
        compressed = text

    if len(compressed) <= max_chars:
        return compressed
    truncated_chars = len(compressed) - max_chars
    return f"{compressed[:max_chars]}\n\n[... {truncated_chars} more characters truncated]"


def serialize_conversation(messages: list[Message]) -> str:
    """Serialize LLM messages to text for summarization.

    This prevents the model from treating it as a conversation to continue.
    Call convert_to_llm() first to handle custom message types.

    Tool results are truncated to keep the summarization request within
    reasonable token budgets.
    """
    parts: list[str] = []

    for msg in messages:
        if msg.role == "user":
            if isinstance(msg.content, str):
                content = msg.content
            elif isinstance(msg.content, list):
                content = "".join(
                    c.text for c in msg.content if hasattr(c, "text") and c.type == "text"
                )
            else:
                content = ""
            if content:
                parts.append(f"[User]: {content}")
        elif msg.role == "assistant":
            text_parts: list[str] = []
            thinking_parts: list[str] = []
            tool_calls: list[str] = []

            if isinstance(msg.content, list):
                for block in msg.content:
                    if hasattr(block, "type"):
                        if block.type == "text" and hasattr(block, "text"):
                            text_parts.append(block.text)
                        elif block.type == "thinking" and hasattr(block, "thinking"):
                            thinking_parts.append(block.thinking)
                        elif block.type == "toolCall" and hasattr(block, "arguments"):
                            args = block.arguments
                            if isinstance(args, dict):
                                args_str = ", ".join(
                                    f"{k}={json.dumps(v)}" for k, v in args.items()
                                )
                                tool_calls.append(f"{block.name}({args_str})")

            if thinking_parts:
                parts.append(f"[Assistant thinking]: {chr(10).join(thinking_parts)}")
            if text_parts:
                parts.append(f"[Assistant]: {chr(10).join(text_parts)}")
            if tool_calls:
                parts.append(f"[Assistant tool calls]: {'; '.join(tool_calls)}")
        elif msg.role == "toolResult":
            if isinstance(msg.content, list):
                content = "".join(
                    c.text for c in msg.content if hasattr(c, "text") and c.type == "text"
                )
                if content:
                    truncated = _truncate_for_summary(content, TOOL_RESULT_MAX_CHARS)
                    parts.append(f"[Tool result]: {truncated}")

    return "\n\n".join(parts)


# ============================================================================
# Summarization System Prompt
# ============================================================================

SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. "
    "Your task is to read a conversation between a user and an "
    "AI coding assistant, then produce a structured summary "
    "following the exact format specified.\n\n"
    "Do NOT continue the conversation. "
    "Do NOT respond to any questions in the conversation. "
    "ONLY output the structured summary."
)

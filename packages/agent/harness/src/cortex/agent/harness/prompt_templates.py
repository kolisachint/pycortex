# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOperatorIssue=false, reportUnnecessaryIsInstance=false, reportUnusedFunction=false
"""Prompt template loading and formatting.

Mechanical port of hoocode's ``packages/agent/src/harness/prompt-templates.ts``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, TypeVar

import yaml

from .types import ExecutionEnv, FileInfo, PromptTemplate

__all__ = [
    "PromptTemplateDiagnostic",
    "format_prompt_template_invocation",
    "load_prompt_templates",
    "load_sourced_prompt_templates",
    "parse_command_args",
    "substitute_args",
]


# Type variables for generic types
TSource = TypeVar("TSource")


@dataclass
class PromptTemplateDiagnostic:
    """Warning produced while loading prompt templates."""

    type: str = "warning"
    """Diagnostic severity. Currently only warnings are emitted."""

    message: str = ""
    """Human-readable diagnostic message."""

    path: str = ""
    """Path associated with the diagnostic."""


@dataclass
class PromptTemplateFrontmatter:
    """Frontmatter for prompt template files."""

    description: str | None = None
    argument_hint: str | None = None


async def load_prompt_templates(
    env: ExecutionEnv,
    paths: str | list[str],
) -> tuple[list[PromptTemplate], list[PromptTemplateDiagnostic]]:
    """Load prompt templates from one or more paths.

    Directory inputs load direct ``.md`` children non-recursively.
    File inputs load explicit ``.md`` files.
    Missing paths and non-markdown files are skipped.
    Read and parse failures are returned as diagnostics.

    Returns:
        Tuple of (prompt_templates, diagnostics)
    """
    prompt_templates: list[PromptTemplate] = []
    diagnostics: list[PromptTemplateDiagnostic] = []

    for path in paths if isinstance(paths, list) else [paths]:
        info = await _safe_file_info(env, path)
        if info is None:
            continue

        kind = await _resolve_kind(env, info)
        if kind == "directory":
            templates, diags = await _load_templates_from_dir(env, info.path)
            prompt_templates.extend(templates)
            diagnostics.extend(diags)
        elif kind == "file" and info.name.endswith(".md"):
            template, diags = await _load_template_from_file(env, info.path)
            if template is not None:
                prompt_templates.append(template)
            diagnostics.extend(diags)

    return prompt_templates, diagnostics


async def load_sourced_prompt_templates(
    env: ExecutionEnv,
    inputs: list[dict[str, Any]],
    map_prompt_template: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load prompt templates from source-tagged paths.

    Source values are preserved exactly and attached to every loaded prompt
    template and diagnostic.

    Returns:
        Tuple of (prompt_templates_with_sources, diagnostics_with_sources)
    """
    prompt_templates: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    for input_item in inputs:
        path = input_item["path"]
        source = input_item["source"]
        templates, diags = await load_prompt_templates(env, path)

        for template in templates:
            mapped = map_prompt_template(template, source) if map_prompt_template else template
            prompt_templates.append({"prompt_template": mapped, "source": source})

        for diag in diags:
            diagnostics.append({**diag.__dict__, "source": source})

    return prompt_templates, diagnostics


async def _load_templates_from_dir(
    env: ExecutionEnv,
    dir_path: str,
) -> tuple[list[PromptTemplate], list[PromptTemplateDiagnostic]]:
    """Load templates from a directory."""
    prompt_templates: list[PromptTemplate] = []
    diagnostics: list[PromptTemplateDiagnostic] = []

    try:
        entries = await env.list_dir(dir_path)
    except Exception as e:
        diagnostics.append(
            PromptTemplateDiagnostic(
                type="warning",
                message=str(e)
                if isinstance(e, Exception)
                else "failed to list prompt template directory",
                path=dir_path,
            )
        )
        return prompt_templates, diagnostics

    # Sort entries by name
    entries.sort(key=lambda x: x.name)

    for entry in entries:
        kind = await _resolve_kind(env, entry)
        if kind != "file" or not entry.name.endswith(".md"):
            continue

        template, diags = await _load_template_from_file(env, entry.path)
        if template is not None:
            prompt_templates.append(template)
        diagnostics.extend(diags)

    return prompt_templates, diagnostics


async def _load_template_from_file(
    env: ExecutionEnv,
    file_path: str,
) -> tuple[PromptTemplate | None, list[PromptTemplateDiagnostic]]:
    """Load a template from a file."""
    diagnostics: list[PromptTemplateDiagnostic] = []

    try:
        raw_content = await env.read_text_file(file_path)
        frontmatter, body = _parse_frontmatter(raw_content)

        # Get first non-empty line for description
        first_line = ""
        for line in body.split("\n"):
            if line.strip():
                first_line = line.strip()
                break

        description = frontmatter.get("description", "")
        if not description and first_line:
            description = first_line[:60]
            if len(first_line) > 60:
                description += "..."

        return PromptTemplate(
            name=_basename_env_path(file_path).removesuffix(".md").removesuffix(".MD"),
            description=description or "",
            content=body,
        ), diagnostics
    except Exception as e:
        diagnostics.append(
            PromptTemplateDiagnostic(
                type="warning",
                message=str(e) if isinstance(e, Exception) else "failed to load prompt template",
                path=file_path,
            )
        )
        return None, diagnostics


async def _safe_file_info(env: ExecutionEnv, path: str) -> FileInfo | None:
    """Safely get file info, returning None on error."""
    try:
        return await env.file_info(path)
    except Exception:
        return None


async def _resolve_kind(
    env: ExecutionEnv,
    info: FileInfo,
) -> str | None:
    """Resolve the kind of a file (file or directory)."""
    if info.kind in ("file", "directory"):
        return info.kind

    try:
        real_path = await env.real_path(info.path)
        target = await env.file_info(real_path)
        if target.kind in ("file", "directory"):
            return target.kind
    except Exception:
        pass

    return None


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from content."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")

    if not normalized.startswith("---"):
        return {}, normalized

    end_index = normalized.find("\n---", 3)
    if end_index == -1:
        return {}, normalized

    yaml_string = normalized[4:end_index]
    body = normalized[end_index + 4 :].strip()

    try:
        frontmatter = yaml.safe_load(yaml_string) or {}
    except yaml.YAMLError:
        frontmatter = {}

    return frontmatter, body


def _basename_env_path(path: str) -> str:
    """Get the basename of a path."""
    normalized = path.rstrip("/")
    slash_index = normalized.rfind("/")
    return normalized if slash_index == -1 else normalized[slash_index + 1 :]


def _error_message(error: Exception, fallback: str) -> str:
    """Get error message from exception."""
    return str(error) if isinstance(error, Exception) else fallback


def parse_command_args(args_string: str) -> list[str]:
    """Parse an argument string using simple shell-style single and double quotes."""
    args: list[str] = []
    current = ""
    in_quote: str | None = None

    for char in args_string:
        if in_quote:
            if char == in_quote:
                in_quote = None
            else:
                current += char
        elif char in ('"', "'"):
            in_quote = char
        elif char in (" ", "\t"):
            if current:
                args.append(current)
                current = ""
        else:
            current += char

    if current:
        args.append(current)

    return args


def substitute_args(content: str, args: list[str]) -> str:
    """Substitute prompt template placeholders with command arguments.

    Supported placeholders:
    - ``$1``, ``$2``, etc. - positional arguments
    - ``$@`` - all arguments joined by space
    - ``$ARGUMENTS`` - all arguments joined by space
    - ``${@:N}`` - arguments from N onwards joined by space
    - ``${@:N:L}`` - L arguments from N onwards joined by space
    """
    result = content

    # Replace $1, $2, etc.
    def replace_positional(match: re.Match[str]) -> str:
        num = int(match.group(1))
        if 1 <= num <= len(args):
            return args[num - 1]
        return ""

    result = re.sub(r"\$(\d+)", replace_positional, result)

    # Replace ${@:N} and ${@:N:L}
    def replace_slice(match: re.Match[str]) -> str:
        start_str = match.group(1)
        length_str = match.group(2)
        start = int(start_str) - 1
        if start < 0:
            start = 0
        if length_str:
            end = start + int(length_str)
            return " ".join(args[start:end])
        return " ".join(args[start:])

    result = re.sub(r"\$\{@:(\d+)(?::(\d+))?\}", replace_slice, result)

    # Replace $ARGUMENTS and $@
    all_args = " ".join(args)
    result = result.replace("$ARGUMENTS", all_args)
    result = result.replace("$@", all_args)

    return result


def format_prompt_template_invocation(
    template: PromptTemplate,
    args: list[str] | None = None,
) -> str:
    """Format a prompt template invocation with positional arguments."""
    return substitute_args(template.content, args or [])

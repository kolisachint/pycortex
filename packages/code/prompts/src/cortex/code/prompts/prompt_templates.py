"""Prompt template loading and expansion.

Port of ``prompt-templates.ts`` from ``packages/coding-agent/src/core/``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .source_info import SourceInfo, create_synthetic_source_info
from .types import PromptTemplate, PromptTemplateExpansion


def parse_command_args(args_string: str) -> list[str]:
    """Parse command arguments respecting quoted strings (bash-style).

    Returns array of arguments.
    """
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
    """Substitute argument placeholders in template content.

    Supports:
    - ``$1``, ``$2``, ... for positional args
    - ``$@`` and ``$ARGUMENTS`` for all args
    - ``${@:N}`` for args from Nth onwards (bash-style slicing)
    - ``${@:N:L}`` for L args starting from Nth

    Note: Replacement happens on the template string only. Argument values
    containing patterns like ``$1``, ``$@``, or ``$ARGUMENTS`` are NOT
    recursively substituted.
    """
    result = content

    # Replace $1, $2, etc. with positional args FIRST (before wildcards)
    # This prevents wildcard replacement values containing $<digit> patterns
    # from being re-substituted
    def replace_positional(match: re.Match[str]) -> str:
        num = int(match.group(1))
        index = num - 1
        if 0 <= index < len(args):
            return args[index]
        return ""

    result = re.sub(r"\$(\d+)", replace_positional, result)

    # Replace ${@:start} or ${@:start:length} with sliced args (bash-style)
    # Process BEFORE simple $@ to avoid conflicts
    def replace_slice(match: re.Match[str]) -> str:
        start_str = match.group(1)
        length_str = match.group(2)
        start = int(start_str) - 1  # Convert to 0-indexed (user provides 1-indexed)
        # Treat 0 as 1 (bash convention: args start at 1)
        if start < 0:
            start = 0

        if length_str:
            length = int(length_str)
            return " ".join(args[start : start + length])
        return " ".join(args[start:])

    result = re.sub(r"\$\{@:(\d+)(?::(\d+))?\}", replace_slice, result)

    # Pre-compute all args joined (optimization)
    all_args = " ".join(args)

    # Replace $ARGUMENTS with all args joined (new syntax, aligns with Claude, Codex, OpenCode)
    result = result.replace("$ARGUMENTS", all_args)

    # Replace $@ with all args joined (existing syntax)
    result = result.replace("$@", all_args)

    return result


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


def _load_template_from_file(
    file_path: str,
    source_info: SourceInfo,
) -> PromptTemplate | None:
    """Load a template from a file."""
    try:
        raw_content = Path(file_path).read_text(encoding="utf-8")
        frontmatter, body = _parse_frontmatter(raw_content)

        # Strip a Copilot `.prompt.md` suffix as well as a plain `.md`, so both
        # `greet.prompt.md` (prompt files) and `greet.md` yield the command `greet`.
        basename = Path(file_path).name
        name = re.sub(r"\.(prompt\.)?md$", "", basename)

        # Get description from frontmatter or first non-empty line
        description = frontmatter.get("description", "")
        if not description:
            for line in body.split("\n"):
                if line.strip():
                    description = line.strip()[:60]
                    if len(line.strip()) > 60:
                        description += "..."
                    break

        # Get type from frontmatter
        raw_type = frontmatter.get("type", "user")
        if raw_type in ("system", "context", "user"):
            template_type = raw_type
        else:
            template_type = "user"

        # Get argument-hint
        argument_hint = frontmatter.get("argument-hint")
        if argument_hint is not None and not str(argument_hint).strip():
            argument_hint = None

        return PromptTemplate(
            name=name,
            description=description or "",
            type=template_type,
            content=body,
            source_path=source_info.path,
            base_dir=source_info.base_dir or str(Path(file_path).parent),
            file_path=file_path,
            argument_hint=str(argument_hint) if argument_hint else None,
        )
    except Exception:
        return None


def _load_templates_from_dir(
    dir_path: str,
    source_info_fn: Any,
) -> list[PromptTemplate]:
    """Scan a directory for .md files (non-recursive) and load them as prompt templates."""
    templates: list[PromptTemplate] = []

    if not os.path.exists(dir_path):
        return templates

    try:
        for entry in os.scandir(dir_path):
            # For symlinks, check if they point to a file
            is_file = entry.is_file()
            if entry.is_symlink():
                try:
                    is_file = os.path.isfile(entry.path)
                except OSError:
                    # Broken symlink, skip it
                    continue

            if is_file and entry.name.endswith(".md"):
                template = _load_template_from_file(entry.path, source_info_fn(entry.path))
                if template:
                    templates.append(template)
    except OSError:
        return []

    return templates


@dataclass
class LoadPromptTemplatesOptions:
    """Options for loading prompt templates."""

    # Working directory for project-local templates.
    cwd: str = ""
    # Agent config directory for global templates.
    agent_dir: str = ""
    # Explicit prompt template paths (files or directories).
    prompt_paths: list[str] = field(default_factory=list)
    # Explicit slash-command paths (files or directories).
    slash_command_paths: list[str] = field(default_factory=list)
    # Include default prompt directories.
    include_defaults: bool = True


def _normalize_path(input_path: str) -> str:
    """Normalize a path, expanding ~ to home directory."""
    trimmed = input_path.strip()
    if trimmed == "~":
        return str(Path.home())
    if trimmed.startswith("~/"):
        return str(Path.home() / trimmed[2:])
    if trimmed.startswith("~"):
        return str(Path.home() / trimmed[1:])
    return trimmed


def _resolve_prompt_path(p: str, cwd: str) -> str:
    """Resolve a prompt path relative to cwd."""
    normalized = _normalize_path(p)
    if os.path.isabs(normalized):
        return normalized
    return str(Path(cwd) / normalized)


def _is_under_path(target: str, root: str) -> bool:
    """Check if target path is under root path."""
    normalized_root = str(Path(root).resolve())
    if target == normalized_root:
        return True
    prefix = normalized_root if normalized_root.endswith(os.sep) else f"{normalized_root}{os.sep}"
    return target.startswith(prefix)


def load_prompt_templates(options: LoadPromptTemplatesOptions) -> list[PromptTemplate]:
    """Load all prompt templates from configured locations.

    Loads from:
    1. Global: agentDir/prompts/
    2. Project: cwd/.hoocode/prompts/
    3. Explicit prompt paths
    """
    resolved_cwd = options.cwd
    resolved_agent_dir = options.agent_dir
    prompt_paths = options.prompt_paths
    slash_command_paths = options.slash_command_paths
    include_defaults = options.include_defaults

    templates: list[PromptTemplate] = []

    # Config directory name
    config_dir_name = ".hoocode"

    global_prompts_dir = (
        str(Path(resolved_agent_dir) / "prompts") if resolved_agent_dir else resolved_agent_dir
    )
    project_prompts_dir = str(Path(resolved_cwd) / config_dir_name / "prompts")
    global_slash_commands_dir = (
        str(Path(resolved_agent_dir) / "commands") if resolved_agent_dir else resolved_agent_dir
    )
    project_slash_commands_dir = str(Path(resolved_cwd) / config_dir_name / "commands")

    def get_source_info(resolved_path: str) -> SourceInfo:
        if os.path.isdir(resolved_path):
            base_dir = resolved_path
        else:
            base_dir = str(Path(resolved_path).parent)

        if _is_under_path(resolved_path, global_prompts_dir) or _is_under_path(
            resolved_path, global_slash_commands_dir
        ):
            return create_synthetic_source_info(
                resolved_path,
                source="local",
                scope="user",
                base_dir=(
                    global_prompts_dir
                    if _is_under_path(resolved_path, global_prompts_dir)
                    else global_slash_commands_dir
                ),
            )
        if _is_under_path(resolved_path, project_prompts_dir) or _is_under_path(
            resolved_path, project_slash_commands_dir
        ):
            return create_synthetic_source_info(
                resolved_path,
                source="local",
                scope="project",
                base_dir=(
                    project_prompts_dir
                    if _is_under_path(resolved_path, project_prompts_dir)
                    else project_slash_commands_dir
                ),
            )
        return create_synthetic_source_info(
            resolved_path,
            source="local",
            base_dir=base_dir,
        )

    if include_defaults:
        # Load slash command dirs first so they win over prompt dirs on name collision
        if global_slash_commands_dir:
            templates.extend(_load_templates_from_dir(global_slash_commands_dir, get_source_info))
        templates.extend(_load_templates_from_dir(project_slash_commands_dir, get_source_info))
        if global_prompts_dir:
            templates.extend(_load_templates_from_dir(global_prompts_dir, get_source_info))
        templates.extend(_load_templates_from_dir(project_prompts_dir, get_source_info))

    # Load explicit slash-command paths (before prompt paths so commands win on collision)
    for raw_path in slash_command_paths:
        resolved_path = _resolve_prompt_path(raw_path, resolved_cwd)
        if not os.path.exists(resolved_path):
            continue

        try:
            if os.path.isdir(resolved_path):
                templates.extend(_load_templates_from_dir(resolved_path, get_source_info))
            elif os.path.isfile(resolved_path) and resolved_path.endswith(".md"):
                template = _load_template_from_file(resolved_path, get_source_info(resolved_path))
                if template:
                    templates.append(template)
        except OSError:
            pass

    # Load explicit prompt paths
    for raw_path in prompt_paths:
        resolved_path = _resolve_prompt_path(raw_path, resolved_cwd)
        if not os.path.exists(resolved_path):
            continue

        try:
            if os.path.isdir(resolved_path):
                templates.extend(_load_templates_from_dir(resolved_path, get_source_info))
            elif os.path.isfile(resolved_path) and resolved_path.endswith(".md"):
                template = _load_template_from_file(resolved_path, get_source_info(resolved_path))
                if template:
                    templates.append(template)
        except OSError:
            pass

    return templates


def try_expand_prompt_template(
    text: str, templates: list[PromptTemplate]
) -> PromptTemplateExpansion:
    """Try to expand a prompt template if it matches a template name.

    Returns expansion metadata so callers can handle ``type: "system"`` or
    ``type: "context"``.
    """
    if not text.startswith("/"):
        return PromptTemplateExpansion(text=text, args=[], args_string="")

    space_index = text.find(" ")
    if space_index == -1:
        template_name = text[1:]
        args_string = ""
    else:
        template_name = text[1:space_index]
        args_string = text[space_index + 1 :]

    template = next((t for t in templates if t.name == template_name), None)
    if template:
        args = parse_command_args(args_string)
        return PromptTemplateExpansion(
            text=substitute_args(template.content, args),
            template=template,
            args=args,
            args_string=args_string,
        )

    return PromptTemplateExpansion(text=text, args=[], args_string="")


def expand_prompt_template(text: str, templates: list[PromptTemplate]) -> str:
    """Expand a prompt template if it matches a template name.

    Returns the expanded content or the original text if not a template.
    """
    return try_expand_prompt_template(text, templates).text

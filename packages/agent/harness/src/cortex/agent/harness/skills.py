# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOperatorIssue=false, reportUnnecessaryIsInstance=false, reportUnusedFunction=false
"""Skill loading and formatting.

Mechanical port of hoocode's ``packages/agent/src/harness/skills.ts``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, TypeVar

import yaml

from .types import ExecutionEnv, FileInfo, Skill

__all__ = [
    "SkillDiagnostic",
    "format_skill_invocation",
    "load_skills",
    "load_sourced_skills",
]

# Type variables for generic types
TSource = TypeVar("TSource")

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
IGNORE_FILE_NAMES = [".gitignore", ".ignore", ".fdignore"]


@dataclass
class SkillDiagnostic:
    """Warning produced while loading skills."""

    type: str = "warning"
    """Diagnostic severity. Currently only warnings are emitted."""

    message: str = ""
    """Human-readable diagnostic message."""

    path: str = ""
    """Path associated with the diagnostic."""


def format_skill_invocation(
    skill: Skill,
    additional_instructions: str | None = None,
) -> str:
    """Format a skill invocation prompt, optionally appending additional user instructions."""
    skill_dir = _dirname_env_path(skill.file_path)
    skill_block = (
        f'<skill name="{skill.name}" location="{skill.file_path}">\n'
        f"References are relative to {skill_dir}.\n\n"
        f"{skill.content}\n"
        f"</skill>"
    )
    if additional_instructions:
        return f"{skill_block}\n\n{additional_instructions}"
    return skill_block


async def load_skills(
    env: ExecutionEnv,
    dirs: str | list[str],
) -> tuple[list[Skill], list[SkillDiagnostic]]:
    """Load skills from one or more directories.

    Traverses directories recursively, loads ``SKILL.md`` files, loads direct
    root ``.md`` files as skills, honors ignore files, and returns diagnostics
    for invalid skill files. Missing input directories are skipped.

    Returns:
        Tuple of (skills, diagnostics)
    """
    skills: list[Skill] = []
    diagnostics: list[SkillDiagnostic] = []

    for dir_path in dirs if isinstance(dirs, list) else [dirs]:
        root_info = await _safe_file_info(env, dir_path)
        if root_info is None:
            continue
        kind = await _resolve_kind(env, root_info)
        if kind != "directory":
            continue

        result = await _load_skills_from_dir_internal(
            env, root_info.path, True, set(), root_info.path
        )
        skills.extend(result[0])
        diagnostics.extend(result[1])

    return skills, diagnostics


async def load_sourced_skills(
    env: ExecutionEnv,
    inputs: list[dict[str, Any]],
    map_skill: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load skills from source-tagged directories.

    Source values are preserved exactly and attached to every loaded skill
    and diagnostic.

    Returns:
        Tuple of (skills_with_sources, diagnostics_with_sources)
    """
    skills: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    for input_item in inputs:
        path = input_item["path"]
        source = input_item["source"]
        loaded_skills, loaded_diags = await load_skills(env, path)

        for skill in loaded_skills:
            mapped = map_skill(skill, source) if map_skill else skill
            skills.append({"skill": mapped, "source": source})

        for diag in loaded_diags:
            diagnostics.append({**diag.__dict__, "source": source})

    return skills, diagnostics


async def _load_skills_from_dir_internal(
    env: ExecutionEnv,
    dir_path: str,
    include_root_files: bool,
    ignore_patterns: set[str],
    root_dir: str,
) -> tuple[list[Skill], list[SkillDiagnostic]]:
    """Internal function to load skills from a directory."""
    skills: list[Skill] = []
    diagnostics: list[SkillDiagnostic] = []

    if not await env.exists(dir_path):
        return skills, diagnostics

    dir_info = await _safe_file_info(env, dir_path)
    if dir_info is None:
        return skills, diagnostics

    kind = await _resolve_kind(env, dir_info)
    if kind != "directory":
        return skills, diagnostics

    # Add ignore rules
    await _add_ignore_rules(env, ignore_patterns, dir_path, root_dir)

    try:
        entries = await env.list_dir(dir_path)
    except Exception:
        return skills, diagnostics

    # First, check for SKILL.md in this directory
    for entry in entries:
        if entry.name != "SKILL.md":
            continue

        full_path = entry.path
        entry_kind = await _resolve_kind(env, entry)
        if entry_kind != "file":
            continue

        rel_path = _relative_env_path(root_dir, full_path)
        if rel_path in ignore_patterns or f"{rel_path}/" in ignore_patterns:
            continue

        skill, diags = await _load_skill_from_file(env, full_path)
        if skill is not None:
            skills.append(skill)
        diagnostics.extend(diags)
        return skills, diagnostics

    # Process entries
    sorted_entries = sorted(entries, key=lambda x: x.name)

    for entry in sorted_entries:
        if entry.name.startswith(".") or entry.name == "node_modules":
            continue

        full_path = entry.path
        entry_kind = await _resolve_kind(env, entry)
        if entry_kind is None:
            continue

        rel_path = _relative_env_path(root_dir, full_path)
        ignore_path = f"{rel_path}/" if entry_kind == "directory" else rel_path

        if ignore_path in ignore_patterns or f"{ignore_path}/" in ignore_patterns:
            continue

        if entry_kind == "directory":
            result = await _load_skills_from_dir_internal(
                env, full_path, False, ignore_patterns, root_dir
            )
            skills.extend(result[0])
            diagnostics.extend(result[1])
            continue

        if entry_kind != "file" or not include_root_files or not entry.name.endswith(".md"):
            continue

        skill, diags = await _load_skill_from_file(env, full_path)
        if skill is not None:
            skills.append(skill)
        diagnostics.extend(diags)

    return skills, diagnostics


async def _add_ignore_rules(
    env: ExecutionEnv,
    ignore_patterns: set[str],
    dir_path: str,
    root_dir: str,
) -> None:
    """Add ignore rules from ignore files in the directory."""
    relative_dir = _relative_env_path(root_dir, dir_path)
    prefix = f"{relative_dir}/" if relative_dir else ""

    for filename in IGNORE_FILE_NAMES:
        ignore_path = _join_env_path(dir_path, filename)
        info = await _safe_file_info(env, ignore_path)
        if info is None or info.kind != "file":
            continue

        try:
            content = await env.read_text_file(ignore_path)
            patterns = [
                p
                for line in content.splitlines()
                if (p := _prefix_ignore_pattern(line, prefix)) is not None
            ]
            for pattern in patterns:
                ignore_patterns.add(pattern)
        except Exception:
            pass


def _prefix_ignore_pattern(line: str, prefix: str) -> str | None:
    """Prefix an ignore pattern with the directory prefix."""
    trimmed = line.strip()
    if not trimmed:
        return None
    if trimmed.startswith("#") and not trimmed.startswith("\\#"):
        return None

    pattern = line
    negated = False
    if pattern.startswith("!"):
        negated = True
        pattern = pattern[1:]
    elif pattern.startswith("\\!"):
        pattern = pattern[1:]

    if pattern.startswith("/"):
        pattern = pattern[1:]

    prefixed = f"{prefix}{pattern}" if prefix else pattern
    return f"!{prefixed}" if negated else prefixed


async def _load_skill_from_file(
    env: ExecutionEnv,
    file_path: str,
) -> tuple[Skill | None, list[SkillDiagnostic]]:
    """Load a skill from a file."""
    diagnostics: list[SkillDiagnostic] = []

    try:
        raw_content = await env.read_text_file(file_path)
        frontmatter, body = _parse_frontmatter(raw_content)
        skill_dir = _dirname_env_path(file_path)
        parent_dir_name = _basename_env_path(skill_dir)

        # Validate description
        description = frontmatter.get("description")
        if not description or (isinstance(description, str) and not description.strip()):
            diagnostics.append(
                SkillDiagnostic(type="warning", message="description is required", path=file_path)
            )
            return None, diagnostics

        # Get name
        name = frontmatter.get("name", parent_dir_name)
        if not isinstance(name, str):
            name = parent_dir_name

        # Validate name
        name_errors = _validate_name(name, parent_dir_name)
        for error in name_errors:
            diagnostics.append(SkillDiagnostic(type="warning", message=error, path=file_path))

        # Validate description length
        desc_errors = _validate_description(description)
        for error in desc_errors:
            diagnostics.append(SkillDiagnostic(type="warning", message=error, path=file_path))

        disable_model_invocation = frontmatter.get("disable-model-invocation", False)

        return (
            Skill(
                name=name,
                description=description,
                content=body,
                file_path=file_path,
                disable_model_invocation=bool(disable_model_invocation),
            ),
            diagnostics,
        )
    except Exception as e:
        message = str(e) if isinstance(e, Exception) else "failed to parse skill file"
        diagnostics.append(SkillDiagnostic(type="warning", message=message, path=file_path))
        return None, diagnostics


def _validate_name(name: str, parent_dir_name: str) -> list[str]:
    """Validate a skill name."""
    errors: list[str] = []
    if name != parent_dir_name:
        errors.append(f'name "{name}" does not match parent directory "{parent_dir_name}"')
    if len(name) > MAX_NAME_LENGTH:
        errors.append(f"name exceeds {MAX_NAME_LENGTH} characters ({len(name)})")
    if not re.match(r"^[a-z0-9-]+$", name):
        errors.append("name contains invalid characters (must be lowercase a-z, 0-9, hyphens only)")
    if name.startswith("-") or name.endswith("-"):
        errors.append("name must not start or end with a hyphen")
    if "--" in name:
        errors.append("name must not contain consecutive hyphens")
    return errors


def _validate_description(description: str | None) -> list[str]:
    """Validate a skill description."""
    errors: list[str] = []
    if not description or not description.strip():
        errors.append("description is required")
    elif len(description) > MAX_DESCRIPTION_LENGTH:
        errors.append(
            f"description exceeds {MAX_DESCRIPTION_LENGTH} characters ({len(description)})"
        )
    return errors


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


async def _safe_file_info(env: ExecutionEnv, path: str) -> FileInfo | None:
    """Safely get file info, returning None on error."""
    try:
        return await env.file_info(path)
    except Exception:
        return None


async def _resolve_kind(env: ExecutionEnv, info: FileInfo) -> str | None:
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


def _join_env_path(base: str, child: str) -> str:
    """Join two path components."""
    return f"{base.rstrip('/')}/{child.lstrip('/')}"


def _dirname_env_path(path: str) -> str:
    """Get the directory name of a path."""
    normalized = path.rstrip("/")
    slash_index = normalized.rfind("/")
    if slash_index <= 0:
        return "/"
    return normalized[:slash_index]


def _basename_env_path(path: str) -> str:
    """Get the basename of a path."""
    normalized = path.rstrip("/")
    slash_index = normalized.rfind("/")
    return normalized if slash_index == -1 else normalized[slash_index + 1 :]


def _relative_env_path(root: str, path: str) -> str:
    """Get the relative path from root to path."""
    normalized_root = root.rstrip("/")
    normalized_path = path.rstrip("/")
    if normalized_path == normalized_root:
        return ""
    if normalized_path.startswith(f"{normalized_root}/"):
        return normalized_path[len(normalized_root) + 1 :]
    return normalized_path.lstrip("/")

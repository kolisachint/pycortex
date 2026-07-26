"""Skill-block parsing and inline skill-command expansion.

Port of ``agent-session-skills.ts`` from ``packages/coding-agent/src/core/``.
``parseSkillBlock`` recognizes the ``<skill>`` envelope embedded in user
messages; ``expandSkillCommand`` turns a ``/skill:name args`` invocation into
that envelope by reading the skill file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ParsedSkillBlock:
    """Parsed skill block from a user message."""

    name: str
    location: str
    content: str
    user_message: str | None = None


@dataclass
class Skill:
    """Skill definition."""

    name: str
    file_path: str
    base_dir: str
    description: str = ""


@dataclass
class SkillExpansionError:
    """Error reported while reading a skill file during expansion."""

    file_path: str
    error: str


_SKILL_BLOCK_PATTERN = re.compile(
    r'^<skill name="([^"]+)" location="([^"]+)">\n'
    r"([\s\S]*?)\n"
    r"</skill>"
    r"(?:\n\n([\s\S]+))?$"
)


def parse_skill_block(text: str) -> ParsedSkillBlock | None:
    """Parse a skill block from message text.

    Returns None if the text doesn't contain a skill block.
    """
    match = _SKILL_BLOCK_PATTERN.match(text)
    if not match:
        return None

    return ParsedSkillBlock(
        name=match.group(1),
        location=match.group(2),
        content=match.group(3),
        user_message=match.group(4).strip() if match.group(4) else None,
    )


def expand_skill_command(
    text: str,
    skills: list[Skill],
    on_error: Any = None,
) -> str:
    """Expand a skill command (``/skill:name args``) to its full skill-block content.

    Returns the expanded text, or the original text when it is not a skill command
    or the named skill is unknown.
    """
    if not text.startswith("/skill:"):
        return text

    space_index = text.find(" ")
    skill_name = text[7:] if space_index == -1 else text[7:space_index]
    args = "" if space_index == -1 else text[space_index + 1 :].strip()

    skill = next((s for s in skills if s.name == skill_name), None)
    if not skill:
        return text  # Unknown skill, pass through

    try:
        content = Path(skill.file_path).read_text(encoding="utf-8")
        # Strip frontmatter (simple implementation)
        body = _strip_frontmatter(content).strip()
        skill_block = (
            f'<skill name="{skill.name}" location="{skill.file_path}">\n'
            f"References are relative to {skill.base_dir}.\n\n"
            f"{body}\n"
            f"</skill>"
        )
        return f"{skill_block}\n\n{args}" if args else skill_block
    except Exception as err:
        if on_error:
            on_error(
                SkillExpansionError(
                    file_path=skill.file_path,
                    error=str(err),
                )
            )
        return text  # Return original on error


def _strip_frontmatter(content: str) -> str:
    """Strip YAML frontmatter from content."""
    if not content.startswith("---"):
        return content

    # Find the closing ---
    end_index = content.find("---", 3)
    if end_index == -1:
        return content

    # Skip the closing --- and any following whitespace
    return content[end_index + 3 :].lstrip("\n")

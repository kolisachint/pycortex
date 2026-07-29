"""Colouring for the diffs an edit tool reports.

Port of ``components/diff.ts``. The input is the diff string
:func:`cortex.code.tools.generate_diff_string` produces — one line per change,
each already carrying its marker and right-aligned line number::

    -12 old line
    +12 new line
     13 unchanged line

so this module never computes a *line* diff; it parses that shape back apart and
paints it: context dim, removals red, additions green. The one thing it does
compute is the **intra-line** diff — when a hunk is exactly one removed line
against one added line, the changed words inside them are inverted, which is
what turns "these two long lines differ somewhere" into "this identifier
changed".

The TS reaches for the ``diff`` package's ``diffWords``. There is no such
dependency here, so :func:`_diff_words` is that function's shape over
:mod:`difflib`: tokens are runs of whitespace and non-whitespace, and whitespace
tokens compare equal to each other, which is how ``diffWords`` keeps a re-indent
from lighting up the whole line.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from cortex.code.interactive.theme import get_theme

__all__ = ["parse_diff_line", "render_diff"]

#: ``"+123 content"`` / ``"-123 content"`` / ``" 123 content"``. The line number
#: group is optional so a marker-only line still parses.
_DIFF_LINE = re.compile(r"^([+\-\s])(\s*\d*)\s(.*)$")

_LEADING_WHITESPACE = re.compile(r"^(\s*)")

#: A token is a run of whitespace or a run of non-whitespace, as ``diffWords``
#: splits.
_TOKEN = re.compile(r"\s+|\S+")


@dataclass(frozen=True)
class DiffLineParts:
    """The three fields of a diff line: its marker, its number, its text."""

    prefix: str
    line_num: str
    content: str


def parse_diff_line(line: str) -> DiffLineParts | None:
    """Split a diff line into prefix, line number and content, or ``None``."""
    match = _DIFF_LINE.match(line)
    if match is None:
        return None
    return DiffLineParts(prefix=match.group(1), line_num=match.group(2), content=match.group(3))


def _replace_tabs(text: str) -> str:
    """Replace tabs with spaces for consistent rendering."""
    return text.replace("\t", "   ")


@dataclass(frozen=True)
class _WordPart:
    value: str
    added: bool
    removed: bool


def _diff_words(old_content: str, new_content: str) -> list[_WordPart]:
    """Word-level diff in the shape of the JS ``diff`` package's output.

    Whitespace tokens all compare equal, so a line that only changed its
    indentation produces no highlighted parts — the behaviour ``diffWords`` has
    and ``diffWordsWithSpace`` does not.
    """
    old_tokens = _TOKEN.findall(old_content)
    new_tokens = _TOKEN.findall(new_content)

    def key(token: str) -> str:
        return " " if token.isspace() else token

    matcher = difflib.SequenceMatcher(
        a=[key(t) for t in old_tokens], b=[key(t) for t in new_tokens], autojunk=False
    )
    parts: list[_WordPart] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            parts.append(_WordPart("".join(old_tokens[i1:i2]), added=False, removed=False))
        elif tag == "delete":
            parts.append(_WordPart("".join(old_tokens[i1:i2]), added=False, removed=True))
        elif tag == "insert":
            parts.append(_WordPart("".join(new_tokens[j1:j2]), added=True, removed=False))
        elif tag == "replace":
            parts.append(_WordPart("".join(old_tokens[i1:i2]), added=False, removed=True))
            parts.append(_WordPart("".join(new_tokens[j1:j2]), added=True, removed=False))
    return parts


def render_intra_line_diff(old_content: str, new_content: str) -> tuple[str, str]:
    """Invert the changed words of a one-for-one line replacement.

    Returns ``(removed_line, added_line)``. Leading whitespace of the first
    changed part is left outside the inverse: highlighting indentation paints a
    block of background over an edit the user did not make.
    """
    parts = _diff_words(old_content, new_content)
    theme = get_theme()

    removed_line = ""
    added_line = ""
    is_first_removed = True
    is_first_added = True

    for part in parts:
        if part.removed:
            value = part.value
            if is_first_removed:
                leading = _LEADING_WHITESPACE.match(value)
                leading_ws = leading.group(1) if leading else ""
                value = value[len(leading_ws) :]
                removed_line += leading_ws
                is_first_removed = False
            if value:
                removed_line += theme.inverse(value)
        elif part.added:
            value = part.value
            if is_first_added:
                leading = _LEADING_WHITESPACE.match(value)
                leading_ws = leading.group(1) if leading else ""
                value = value[len(leading_ws) :]
                added_line += leading_ws
                is_first_added = False
            if value:
                added_line += theme.inverse(value)
        else:
            removed_line += part.value
            added_line += part.value

    return removed_line, added_line


def render_diff(diff_text: str, file_path: str | None = None) -> str:  # noqa: ARG001 - API parity
    """Colour a diff string. ``file_path`` is unused, as in the TS.

    Removals and additions are collected in runs so a hunk can be recognised:
    exactly one line out and one line in is a single-line modification and gets
    the intra-line treatment; anything wider is shown as-is, because word-diffing
    across a block reads as noise.
    """
    theme = get_theme()
    lines = diff_text.split("\n")
    result: list[str] = []

    i = 0
    while i < len(lines):
        parsed = parse_diff_line(lines[i])

        if parsed is None:
            result.append(theme.fg("toolDiffContext", lines[i]))
            i += 1
            continue

        if parsed.prefix == "-":
            removed_lines: list[DiffLineParts] = []
            while i < len(lines):
                part = parse_diff_line(lines[i])
                if part is None or part.prefix != "-":
                    break
                removed_lines.append(part)
                i += 1

            added_lines: list[DiffLineParts] = []
            while i < len(lines):
                part = parse_diff_line(lines[i])
                if part is None or part.prefix != "+":
                    break
                added_lines.append(part)
                i += 1

            if len(removed_lines) == 1 and len(added_lines) == 1:
                removed = removed_lines[0]
                added = added_lines[0]
                removed_line, added_line = render_intra_line_diff(
                    _replace_tabs(removed.content), _replace_tabs(added.content)
                )
                result.append(
                    theme.fg("toolDiffRemoved", f"-{removed.line_num} {removed_line}"),
                )
                result.append(theme.fg("toolDiffAdded", f"+{added.line_num} {added_line}"))
            else:
                for removed in removed_lines:
                    result.append(
                        theme.fg(
                            "toolDiffRemoved",
                            f"-{removed.line_num} {_replace_tabs(removed.content)}",
                        )
                    )
                for added in added_lines:
                    result.append(
                        theme.fg(
                            "toolDiffAdded", f"+{added.line_num} {_replace_tabs(added.content)}"
                        )
                    )
        elif parsed.prefix == "+":
            result.append(
                theme.fg("toolDiffAdded", f"+{parsed.line_num} {_replace_tabs(parsed.content)}")
            )
            i += 1
        else:
            result.append(
                theme.fg("toolDiffContext", f" {parsed.line_num} {_replace_tabs(parsed.content)}")
            )
            i += 1

    return "\n".join(result)

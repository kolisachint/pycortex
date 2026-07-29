"""Reading ``CHANGELOG.md``. Port of ``utils/changelog.ts``.

``/changelog`` prints every entry, newest first; :func:`get_new_entries` is what
the startup notice uses to show only what has landed since the version the user
last ran. The parse is deliberately loose — a ``##`` line whose version cannot be
read closes the current entry and starts nothing, so a hand-edited heading drops
one section rather than corrupting the rest.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

__all__ = [
    "ChangelogEntry",
    "compare_versions",
    "get_new_entries",
    "parse_changelog",
]

_VERSION_RE = re.compile(r"##\s+\[?(\d+)\.(\d+)\.(\d+)\]?")


@dataclass(frozen=True)
class ChangelogEntry:
    """One ``## x.y.z`` section, heading line included."""

    major: int
    minor: int
    patch: int
    content: str


def parse_changelog(changelog_path: str) -> list[ChangelogEntry]:
    """Parse entries from a ``CHANGELOG.md``; an unreadable file yields none."""
    if not os.path.exists(changelog_path):
        return []

    try:
        with open(changelog_path, encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        # The TS logs a warning here; a print would land in the middle of the
        # TUI's frame, so the empty list is the whole answer.
        return []

    entries: list[ChangelogEntry] = []
    current_lines: list[str] = []
    current_version: tuple[int, int, int] | None = None

    for line in content.split("\n"):
        if line.startswith("## "):
            if current_version is not None and current_lines:
                entries.append(
                    ChangelogEntry(*current_version, content="\n".join(current_lines).strip())
                )

            match = _VERSION_RE.match(line)
            if match:
                current_version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
                current_lines = [line]
            else:
                current_version = None
                current_lines = []
        elif current_version is not None:
            current_lines.append(line)

    if current_version is not None and current_lines:
        entries.append(ChangelogEntry(*current_version, content="\n".join(current_lines).strip()))

    return entries


def compare_versions(v1: ChangelogEntry, v2: ChangelogEntry) -> int:
    """``-1``/``0``/``1`` by version, ignoring content. The TS returns the raw
    differences rather than the sign; only its sign is ever read."""
    if v1.major != v2.major:
        return v1.major - v2.major
    if v1.minor != v2.minor:
        return v1.minor - v2.minor
    return v1.patch - v2.patch


def get_new_entries(entries: list[ChangelogEntry], last_version: str) -> list[ChangelogEntry]:
    """Entries strictly newer than ``last_version`` (``"1.2.3"``).

    A component that is not a number reads as 0, as ``Number("x") || 0`` does,
    so a malformed version shows the whole changelog rather than none of it.
    """
    parts = last_version.split(".")

    def component(index: int) -> int:
        if index >= len(parts):
            return 0
        try:
            return int(parts[index])
        except ValueError:
            return 0

    last = ChangelogEntry(component(0), component(1), component(2), "")
    return [entry for entry in entries if compare_versions(entry, last) > 0]

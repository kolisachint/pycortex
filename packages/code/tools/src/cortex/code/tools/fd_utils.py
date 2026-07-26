"""Shared helpers for the fd-backed ``find`` tool.

Mechanical port of ``core/tools/fd-utils.ts``. ``to_posix_path`` is reused for
stable forward-slash output elsewhere (native-search).
"""

from __future__ import annotations

import os


def to_posix_path(value: str) -> str:
    """Convert a platform path to forward-slash (POSIX) form."""
    return value.replace(os.sep, "/")


def relativize_fd_line(line: str, search_path: str) -> str:
    """Relativize an fd result line against the search root, preserving a
    trailing slash that marked a directory, in POSIX form.
    """
    had_trailing_slash = line.endswith("/") or line.endswith("\\")
    if line.startswith(search_path):
        relative_path = line[len(search_path) + 1 :]
    else:
        relative_path = os.path.relpath(line, search_path)
    if had_trailing_slash and not relative_path.endswith("/"):
        relative_path += "/"
    return to_posix_path(relative_path)


def apply_fd_glob_pattern(args: list[str], pattern: str) -> str:
    """Append a glob pattern (and ``--full-path`` if needed) to an fd arg list.

    Returns the effective pattern to pass to fd.
    """
    effective_pattern = pattern
    if "/" in pattern:
        args.append("--full-path")
        if not pattern.startswith("/") and not pattern.startswith("**/") and pattern != "**":
            effective_pattern = f"**/{pattern}"
    return effective_pattern

"""Path utilities for tool operations.

Mechanical port of ``core/tools/path-utils.ts``.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname


def is_file_url(file_path: str) -> bool:
    """Check if a string is a file URL (starts with "file:///")."""
    return file_path.startswith("file:///")


def normalize_file_url(file_path: str) -> str:
    """Convert a file URL to a file path.

    Returns the original path if it's not a file URL.
    """
    if not is_file_url(file_path):
        return file_path
    try:
        parsed = urlparse(file_path)
        return url2pathname(unquote(parsed.path))
    except (ValueError, OSError):
        return re.sub(r"^file://", "", file_path)


_UNICODE_SPACES = re.compile("[\u00a0\u2000-\u200a\u202f\u205f\u3000]")
_NARROW_NO_BREAK_SPACE = "\u202f"


def _normalize_unicode_spaces(value: str) -> str:
    return _UNICODE_SPACES.sub(" ", value)


def _try_macos_screenshot_path(file_path: str) -> str:
    return re.sub(r" (AM|PM)\.", rf"{_NARROW_NO_BREAK_SPACE}\1.", file_path, flags=re.IGNORECASE)


def _try_nfd_variant(file_path: str) -> str:
    # macOS stores filenames in NFD (decomposed) form.
    return unicodedata.normalize("NFD", file_path)


def _try_curly_quote_variant(file_path: str) -> str:
    # macOS uses U+2019 in screenshot names; users type U+0027.
    return file_path.replace("'", "\u2019")


def _file_exists(file_path: str) -> bool:
    return os.path.exists(file_path)


def _normalize_at_prefix(file_path: str) -> str:
    return file_path[1:] if file_path.startswith("@") else file_path


def expand_path(file_path: str) -> str:
    """Expand ~, file URLs, @ prefixes, and Unicode spaces in a path."""
    normalized_file_url = normalize_file_url(file_path)
    normalized = _normalize_unicode_spaces(_normalize_at_prefix(normalized_file_url))
    if normalized == "~":
        return str(Path.home())
    if normalized.startswith("~/"):
        return str(Path.home()) + normalized[1:]
    return normalized


def resolve_to_cwd(file_path: str, cwd: str) -> str:
    """Resolve a path relative to the given cwd.

    Handles ~ expansion and absolute paths.
    """
    expanded = expand_path(file_path)
    if os.path.isabs(expanded):
        return expanded
    return os.path.normpath(os.path.join(cwd, expanded))


def resolve_read_path(file_path: str, cwd: str) -> str:
    """Resolve a read path, trying macOS filename encoding variants on miss."""
    resolved = resolve_to_cwd(file_path, cwd)

    if _file_exists(resolved):
        return resolved

    am_pm_variant = _try_macos_screenshot_path(resolved)
    if am_pm_variant != resolved and _file_exists(am_pm_variant):
        return am_pm_variant

    nfd_variant = _try_nfd_variant(resolved)
    if nfd_variant != resolved and _file_exists(nfd_variant):
        return nfd_variant

    curly_variant = _try_curly_quote_variant(resolved)
    if curly_variant != resolved and _file_exists(curly_variant):
        return curly_variant

    nfd_curly_variant = _try_curly_quote_variant(nfd_variant)
    if nfd_curly_variant != resolved and _file_exists(nfd_curly_variant):
        return nfd_curly_variant

    return resolved

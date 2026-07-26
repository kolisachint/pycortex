"""Render helpers for tool outputs.

Mechanical port of ``core/tools/render-utils.ts``. The TUI-specific rendering
(``renderCall``/``renderResult``) is not ported in this leaf, so the terminal
capability + image-fallback branch of ``get_text_output`` is omitted; the text
extraction path is preserved.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def shorten_path(path: Any) -> str:
    """Replace the home-directory prefix of a path with ``~``."""
    if not isinstance(path, str):
        return ""
    home = str(Path.home())
    if path.startswith(home):
        return "~" + path[len(home) :]
    return path


def str_value(value: Any) -> str | None:
    """Return the string value, "" for None, or None for a non-string."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return None


def replace_tabs(text: str) -> str:
    """Replace tabs with three spaces."""
    return text.replace("\t", "   ")


def normalize_display_text(text: str) -> str:
    """Strip carriage returns for display."""
    return text.replace("\r", "")


def get_text_output(result: Any, show_images: bool = True) -> str:
    """Extract text content blocks from a tool result, joined with newlines."""
    if not result:
        return ""
    if isinstance(result, dict):
        content = result.get("content", [])
    else:
        content = getattr(result, "content", [])
    texts: list[str] = []
    for c in content:
        c_type = c.get("type") if isinstance(c, dict) else getattr(c, "type", None)
        if c_type == "text":
            text = c.get("text", "") if isinstance(c, dict) else getattr(c, "text", "")
            texts.append(_strip_ansi(text or "").replace("\r", ""))
    return "\n".join(texts)


def invalid_arg_text(theme: Any = None) -> str:
    """Placeholder for an invalid argument value."""
    if theme is not None:
        return theme.fg("error", "[invalid arg]")
    return "[invalid arg]"

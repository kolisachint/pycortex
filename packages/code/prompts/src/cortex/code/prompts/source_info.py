"""Source info types for tracking the origin of prompts and templates.

Port of ``source-info.ts`` from ``packages/coding-agent/src/core/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SourceScope = Literal["user", "project", "temporary"]
SourceOrigin = Literal["package", "top-level", "claude-code"]


@dataclass
class SourceInfo:
    """Tracks where a prompt template or skill originated."""

    path: str
    source: str
    scope: SourceScope
    origin: SourceOrigin
    base_dir: str | None = None


def create_synthetic_source_info(
    path: str,
    source: str,
    scope: SourceScope = "temporary",
    origin: SourceOrigin = "top-level",
    base_dir: str | None = None,
) -> SourceInfo:
    """Create a SourceInfo with explicit values."""
    return SourceInfo(
        path=path,
        source=source,
        scope=scope,
        origin=origin,
        base_dir=base_dir,
    )

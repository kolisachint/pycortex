"""Test infrastructure for the TUI port — the authoritative rendering surface.

Never published, never imported by shipped code. See `docs/02-target-architecture.md`
§3.1 for why the TUI leaves are verified against a cell grid rather than by reading
the diff against the TypeScript.
"""

from cortex.tui.testkit._capture import CaptureTerminal
from cortex.tui.testkit._parity import (
    Result,
    Verdict,
    evaluate_all,
    evaluate_component,
    evaluate_renderer,
    surface_from_lines,
)
from cortex.tui.testkit._scene import (
    Corpus,
    Unported,
    build_component,
    load_corpus,
    load_golden,
)
from cortex.tui.testkit._snapshot import CellDiff, SurfaceDiff, diff_surfaces, snapshot
from cortex.tui.testkit._surface import Cell, Style, Surface, grapheme_clusters

__all__ = [
    "CaptureTerminal",
    "Cell",
    "CellDiff",
    "Corpus",
    "Result",
    "Style",
    "Surface",
    "SurfaceDiff",
    "Unported",
    "Verdict",
    "build_component",
    "diff_surfaces",
    "evaluate_all",
    "evaluate_component",
    "evaluate_renderer",
    "grapheme_clusters",
    "load_corpus",
    "load_golden",
    "snapshot",
    "surface_from_lines",
]

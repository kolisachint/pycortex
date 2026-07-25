"""Cross-validate `Surface` against `@xterm/headless`.

Without this, the surface would just be a second opinion I wrote myself, and any
mistake in it would silently redefine "correct" for every TUI parity test that
builds on it. Every ANSI scenario in the corpus is replayed through a production
terminal emulator, and the grids must agree cell for cell.
"""

from __future__ import annotations

from typing import Any

import pytest
from cortex.tui.testkit import Surface, load_corpus, load_golden, snapshot

CORPUS = load_corpus()
GOLDEN = load_golden("xterm-grids.json")
GRIDS: dict[str, Any] = {s["id"]: s for s in GOLDEN["scenarios"]}
SCENARIOS = [(s["id"], s) for s in CORPUS.ansi]


def _xterm_cell(golden_row: dict[str, Any], col: int) -> tuple[str, bool]:
    """Normalise one xterm cell to the surface's (char, is_wide_continuation).

    xterm returns `""` for both an untouched cell and the trailing half of a wide
    character; the two are told apart by `getWidth()` — 0 means continuation.
    """
    chars: str = golden_row["chars"][col]
    width: int = golden_row["widths"][col]
    if width == 0:
        return "", True
    return (chars or " "), False


def _xterm_style_matches(expected: dict[str, Any], style: Any) -> list[str]:
    problems: list[str] = []
    for flag in ("bold", "dim", "italic", "underline", "inverse", "strike"):
        if bool(expected[flag]) != bool(getattr(style, flag)):
            problems.append(f"{flag}: xterm={expected[flag]} surface={getattr(style, flag)}")
    for key in ("fg", "bg"):
        want = expected[key]
        got = getattr(style, key)
        want_norm = tuple(want) if isinstance(want, list) else want
        if want_norm != got:
            problems.append(f"{key}: xterm={want_norm!r} surface={got!r}")
    return problems


@pytest.mark.parametrize(("scenario_id", "spec"), SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_surface_matches_xterm(scenario_id: str, spec: dict[str, Any]) -> None:
    golden = GRIDS.get(scenario_id)
    assert golden is not None, (
        f"no xterm golden for {scenario_id} — run `uv run scripts/tui_goldens.py --refresh`"
    )

    surface = Surface.render(spec["data"], cols=spec["cols"], rows=spec["rows"])

    problems: list[str] = []
    for row in range(spec["rows"]):
        golden_row = golden["grid"][row]
        for col in range(spec["cols"]):
            cell = surface.grid[row][col]
            want_char, want_continuation = _xterm_cell(golden_row, col)
            if (want_char, want_continuation) != (cell.char, cell.wide_continuation):
                problems.append(
                    f"({row},{col}) cell xterm={(want_char, want_continuation)!r} "
                    f"surface={(cell.char, cell.wide_continuation)!r}"
                )
                continue
            if cell.char in ("", " "):
                continue  # blank cells carry no observable style beyond background
            for problem in _xterm_style_matches(golden_row["styles"][col], cell.style):
                problems.append(f"({row},{col}) {problem}")

    assert not problems, (
        f"{scenario_id}: surface disagrees with @xterm/headless\n"
        + "\n".join(f"  {p}" for p in problems[:20])
        + f"\n\n--- surface ---\n{snapshot(surface)}"
    )


@pytest.mark.parametrize(("scenario_id", "spec"), SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_cursor_matches_xterm(scenario_id: str, spec: dict[str, Any]) -> None:
    golden = GRIDS[scenario_id]
    surface = Surface.render(spec["data"], cols=spec["cols"], rows=spec["rows"])
    assert (surface.cursor_row, surface.cursor_x) == (
        golden["cursor"]["row"],
        golden["cursor"]["col"],
    ), f"{scenario_id}: cursor position diverges\n{snapshot(surface)}"


def test_no_unhandled_sequences_in_corpus() -> None:
    """An escape the surface silently ignores is a hole in the authority."""
    unhandled: dict[str, list[str]] = {}
    for spec in CORPUS.ansi:
        surface = Surface.render(spec["data"], cols=spec["cols"], rows=spec["rows"])
        if surface.unhandled:
            unhandled[spec["id"]] = sorted(set(surface.unhandled))
    assert not unhandled, f"unhandled escape sequences: {unhandled}"

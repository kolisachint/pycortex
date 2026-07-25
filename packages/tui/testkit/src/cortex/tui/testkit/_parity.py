"""Evaluate the Python port against the captured hoocode TS goldens.

One evaluation pass, three consumers: the parity tests assert on it, the parity
report records it, and `migrate_next.py --status` reads the report so a TUI step
cannot be ticked while scenarios are still failing.

Verdicts are deliberately three-valued. `MATCH` and `MISMATCH` are the usual
pass/fail; `UNPORTED` means the scenario asks for something that does not exist
in pycortex yet, which is a tracked gap rather than a bug — it names the plan
step that closes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from cortex.tui.testkit._scene import Unported, build_component, load_corpus, load_golden
from cortex.tui.testkit._snapshot import diff_surfaces, snapshot
from cortex.tui.testkit._surface import Surface

__all__ = [
    "Result",
    "Verdict",
    "evaluate_all",
    "evaluate_component",
    "evaluate_renderer",
    "surface_from_lines",
]


class Verdict(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNPORTED = "unported"


@dataclass(frozen=True)
class Result:
    id: str
    kind: str
    verdict: Verdict
    detail: str = ""
    blocked_by: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.MATCH


def surface_from_lines(lines: list[str], width: int) -> Surface:
    """Paint a component's `render()` output onto a surface, one line per row.

    Comparing raw line strings would be the wrong test: `\\x1b[1m\\x1b[31m` and
    `\\x1b[31;1m` are different strings and the same screen. Painting both sides
    onto a surface compares what the user sees.
    """
    rows = max(1, len(lines))
    return Surface.render("\r\n".join(lines), cols=width, rows=rows)


def evaluate_component(spec: dict[str, Any], golden: dict[str, Any]) -> Result:
    width = golden["width"]
    expected = surface_from_lines(golden["lines"], width)
    try:
        component = build_component(spec)
    except Unported as exc:
        return Result(spec["id"], "component", Verdict.UNPORTED, str(exc), exc.blocked_by)

    actual_lines = component.render(width)
    actual = surface_from_lines(actual_lines, width)

    if len(golden["lines"]) != len(actual_lines):
        detail = (
            f"line count {len(golden['lines'])} != {len(actual_lines)}\n"
            f"  TS: {golden['lines']!r}\n  py: {actual_lines!r}"
        )
        return Result(spec["id"], "component", Verdict.MISMATCH, detail, "1.7")

    diff = diff_surfaces(expected, actual)
    if diff:
        return Result(
            spec["id"], "component", Verdict.MISMATCH, diff.report(expected, actual), "1.7"
        )

    # Caching bug class: a second render at the same width must not change.
    if component.render(width) != actual_lines:
        return Result(
            spec["id"],
            "component",
            Verdict.MISMATCH,
            "second render at the same width returned different lines (stale cache)",
            "1.7",
        )
    return Result(spec["id"], "component", Verdict.MATCH)


def _overlay_options(raw: dict[str, Any]) -> dict[str, Any]:
    """Corpus overlay options are TS-shaped (camelCase); the port is snake_case.

    Translated here rather than in either implementation so the one corpus keeps
    driving both sides.
    """
    renames = {
        "minWidth": "min_width",
        "maxHeight": "max_height",
        "offsetX": "offset_x",
        "offsetY": "offset_y",
        "nonCapturing": "non_capturing",
    }
    return {renames.get(key, key): value for key, value in raw.items()}


def _drive_python_renderer(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Run a renderer scenario against the Python TUI, returning captured frames."""
    # Imported late so a scenario needing something unported reports as a gap
    # rather than breaking collection.
    from cortex.tui.render import TUI
    from cortex.tui.testkit._capture import CaptureTerminal

    terminal = CaptureTerminal(spec["cols"], spec["rows"])
    tui = TUI(terminal)  # pyright: ignore[reportArgumentType]
    children: list[Any] = []
    overlay_handles: list[Any] = []
    frames: list[dict[str, Any]] = []

    for step in spec["steps"]:
        op = step["op"]
        if op == "add":
            component = build_component(step)
            children.append(component)
            tui.add_child(component)  # pyright: ignore[reportArgumentType]
        elif op == "setText":
            children[step["target"]].set_text(step["text"])
        elif op == "setLines":
            children[step["target"]].set_lines(step["lines"])
        elif op == "removeChild":
            tui.remove_child(children[step["target"]])
        elif op == "clearOnShrink":
            tui.set_clear_on_shrink(step["enabled"])
        elif op == "resize":
            terminal.resize(step["cols"], step["rows"])
        elif op == "overlayHandle":
            handle = overlay_handles[step["target"]]
            action = step["action"]
            if action == "setHidden":
                handle.set_hidden(step["hidden"])
            elif action == "hide":
                handle.hide()
            elif action == "focus":
                handle.focus()
            terminal.writes.clear()
        elif op == "overlay":
            show = getattr(tui, "show_overlay", None)
            if show is None:
                raise Unported("TUI.show_overlay", "1.5")
            overlay_handles.append(
                show(build_component(step), _overlay_options(step.get("options", {})))
            )
            # show_overlay hides the cursor; that is not part of the frame.
            terminal.writes.clear()
        elif op == "hideOverlay":
            hide = getattr(tui, "hide_overlay", None)
            if hide is None:
                raise Unported("TUI.hide_overlay", "1.5")
            hide()
            terminal.writes.clear()
        elif op == "render":
            # The public path is debounced through timers; reach for the frame
            # method directly, exactly as reference/dump.ts calls `doRender`.
            render_now = getattr(tui, "_do_render", None)
            if render_now is None:
                raise Unported("TUI._do_render", "1.5")
            render_now()
            frames.append(
                {
                    "cols": terminal.columns,
                    "rows": terminal.rows,
                    "stream": terminal.stream,
                }
            )
            terminal.writes.clear()
        else:
            raise Unported(f"renderer op {op}", "1.5")
    return frames


def evaluate_renderer(spec: dict[str, Any], golden: dict[str, Any]) -> Result:
    try:
        actual_frames = _drive_python_renderer(spec)
    except Unported as exc:
        return Result(spec["id"], "renderer", Verdict.UNPORTED, str(exc), exc.blocked_by)
    except Exception as exc:  # noqa: BLE001 — any failure here is a reportable gap
        return Result(
            spec["id"],
            "renderer",
            Verdict.MISMATCH,
            f"{type(exc).__name__}: {exc}",
            "1.5",
        )

    expected_frames = golden["frames"]
    if len(expected_frames) != len(actual_frames):
        detail = f"frame count {len(expected_frames)} != {len(actual_frames)}"
        return Result(spec["id"], "renderer", Verdict.MISMATCH, detail, "1.5")

    # Frames are cumulative: a differential renderer's Nth write only makes sense
    # applied on top of the first N-1, so replay into one long-lived surface.
    expected_surface = Surface(cols=spec["cols"], rows=spec["rows"])
    actual_surface = Surface(cols=spec["cols"], rows=spec["rows"])
    for index, (exp_frame, act_frame) in enumerate(
        zip(expected_frames, actual_frames, strict=True)
    ):
        if exp_frame["cols"] != expected_surface.cols or exp_frame["rows"] != expected_surface.rows:
            expected_surface = Surface(cols=exp_frame["cols"], rows=exp_frame["rows"])
            actual_surface = Surface(cols=exp_frame["cols"], rows=exp_frame["rows"])
        expected_surface.feed(exp_frame["stream"])
        actual_surface.feed(act_frame["stream"])
        diff = diff_surfaces(expected_surface, actual_surface, compare_cursor=False)
        if diff:
            detail = f"frame {index} diverges\n" + diff.report(expected_surface, actual_surface)
            return Result(spec["id"], "renderer", Verdict.MISMATCH, detail, "1.5")

    return Result(spec["id"], "renderer", Verdict.MATCH)


def evaluate_all() -> list[Result]:
    corpus = load_corpus()
    results: list[Result] = []

    components = {s["id"]: s for s in load_golden("ts-components.json")["scenarios"]}
    for spec in corpus.components:
        golden = components.get(spec["id"])
        if golden is None:
            results.append(
                Result(spec["id"], "component", Verdict.UNPORTED, "no golden captured", "1.9")
            )
            continue
        results.append(evaluate_component(spec, golden))

    renderers = {s["id"]: s for s in load_golden("ts-renderer.json")["scenarios"]}
    for spec in corpus.renderer:
        golden = renderers.get(spec["id"])
        if golden is None:
            results.append(
                Result(spec["id"], "renderer", Verdict.UNPORTED, "no golden captured", "1.9")
            )
            continue
        results.append(evaluate_renderer(spec, golden))

    return results


def expected_surface_snapshot(golden: dict[str, Any]) -> str:
    """The TS reference screen for a component golden, for eyeballing."""
    return snapshot(surface_from_lines(golden["lines"], golden["width"]))

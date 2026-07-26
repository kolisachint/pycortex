# pyright: reportPrivateUsage=false
# These deliberately exercise renderer internals (memo identity, patch
# reporting, overlay layout maths) that have no public surface — that is the
# point of the file, so the private-usage rule is off here rather than muted
# fifty times inline.
"""Unit tests for `cortex.tui.render`.

Scope note: these cover the parts of `tui.ts` that are *not* observable as a
screen — memoization identity, patch reporting, scheduling, the crash guard.
Everything the user can actually see is verified by diffing whole screens
against the real TypeScript implementation in `packages/tui/testkit`
(`renderer/*` scenarios); duplicating that here would just be a second opinion
written by the same hand that wrote the port.

The previous version of this file was the cautionary case: it asserted against
a renderer that had invented its own algorithm, and passed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from cortex.tui.images import (
    CellDimensions,
    TerminalCapabilities,
    get_cell_dimensions,
    reset_capabilities_cache,
    set_capabilities,
    set_cell_dimensions,
)
from cortex.tui.render import CURSOR_MARKER, TUI, Container, is_focusable
from cortex.tui.render._render import (
    OverlayOptions,
    _extract_kitty_image_ids,
    _parse_size_value,
)
from cortex.tui.testkit import CaptureTerminal, Surface


@pytest.fixture(autouse=True)
def _reset_image_capabilities() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Capabilities and cell size are process-global in `terminal-image.ts`."""
    yield
    reset_capabilities_cache()
    set_cell_dimensions(CellDimensions(width_px=9, height_px=18))


class InputRecorder:
    """Records every key it is handed — the TS test's own stub."""

    def __init__(self) -> None:
        self.inputs: list[str] = []

    def render(self, width: int) -> list[str]:
        return [""]

    def handle_input(self, data: str) -> None:
        self.inputs.append(data)

    def invalidate(self) -> None:
        pass


class Static:
    """Identity-stable fixed lines — see `StaticLines` in the testkit."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self.render_count = 0

    def set_lines(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def render(self, width: int) -> list[str]:
        self.render_count += 1
        return self._lines

    def invalidate(self) -> None:
        pass


class Unstable:
    """Returns an equal but *fresh* list every render."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def render(self, width: int) -> list[str]:
        return list(self._lines)

    def invalidate(self) -> None:
        pass


def make_tui(cols: int = 20, rows: int = 6) -> tuple[TUI, CaptureTerminal]:
    terminal = CaptureTerminal(cols, rows)
    return TUI(terminal), terminal  # pyright: ignore[reportArgumentType]


def screen(terminal: CaptureTerminal) -> Surface:
    return terminal.surface()


class TestContainer:
    def test_flattens_children_in_order(self) -> None:
        container = Container()
        container.add_child(Static(["a"]))
        container.add_child(Static(["b", "c"]))
        assert container.render(10) == ["a", "b", "c"]

    def test_returns_the_same_list_object_when_nothing_changed(self) -> None:
        container = Container()
        container.add_child(Static(["a"]))
        first = container.render(10)
        assert container.render(10) is first, "identity is what the root diff keys on"

    def test_children_are_still_rendered_when_the_memo_hits(self) -> None:
        container = Container()
        child = Static(["a"])
        container.add_child(child)
        container.render(10)
        container.render(10)
        # Side effects and per-child caches must still run every frame.
        assert child.render_count == 2

    def test_memo_misses_when_a_child_returns_a_fresh_list(self) -> None:
        container = Container()
        container.add_child(Unstable(["a"]))
        assert container.render(10) is not container.render(10)

    def test_memo_misses_on_width_change(self) -> None:
        container = Container()
        container.add_child(Static(["a"]))
        assert container.render(10) is not container.render(11)

    def test_memo_is_dropped_when_the_child_list_changes(self) -> None:
        container = Container()
        container.add_child(Static(["a"]))
        first = container.render(10)
        container.add_child(Static(["b"]))
        assert container.render(10) is not first

    def test_invalidate_recurses(self) -> None:
        class Recording:
            def __init__(self) -> None:
                self.invalidated = False

            def render(self, width: int) -> list[str]:
                return []

            def invalidate(self) -> None:
                self.invalidated = True

        container = Container()
        child = Recording()
        container.add_child(child)
        container.invalidate()
        assert child.invalidated is True

    def test_remove_child_of_an_unknown_component_is_a_noop(self) -> None:
        container = Container()
        container.add_child(Static(["a"]))
        container.remove_child(Static(["other"]))
        assert len(container.children) == 1


class TestPatchReporting:
    """`TUI.render` tells `_do_render` which rows it touched."""

    def test_first_render_reports_full(self) -> None:
        tui, _ = make_tui()
        tui.add_child(Static(["a"]))
        tui.render(20)
        assert tui._last_patch == "full"

    def test_unchanged_frame_reports_nothing(self) -> None:
        tui, _ = make_tui()
        tui.add_child(Static(["a"]))
        tui.render(20)
        tui.render(20)
        assert tui._last_patch is None

    def test_in_place_change_reports_the_touched_row(self) -> None:
        tui, _ = make_tui()
        first = Static(["a1", "a2"])
        second = Static(["b1", "b2"])
        tui.add_child(first)
        tui.add_child(second)
        tui.render(20)
        second.set_lines(["b1", "CHANGED"])
        tui.render(20)
        patch = tui._last_patch
        assert patch is not None and patch != "full"
        assert (patch.low, patch.high) == (3, 3)

    def test_length_change_marks_everything_below_dirty(self) -> None:
        tui, _ = make_tui()
        first = Static(["a1"])
        tui.add_child(first)
        tui.add_child(Static(["b1"]))
        tui.render(20)
        first.set_lines(["a1", "a2"])
        tui.render(20)
        patch = tui._last_patch
        assert patch is not None and patch != "full"
        # Rows below the splice all shifted, so the dirty range runs to the end.
        assert patch.low == 1
        assert patch.high == 2
        assert patch.prev_length == 2

    def test_overlays_disable_the_patch_path(self) -> None:
        tui, _ = make_tui()
        tui.add_child(Static(["a"]))
        tui.render(20)
        tui.show_overlay(Static(["o"]))
        tui.render(20)
        # Kitty bookkeeping and compositing need the true previous content.
        assert tui._last_patch == "full"


class TestCursorMarker:
    def test_marker_is_stripped_and_its_column_reported(self) -> None:
        tui, _ = make_tui()
        lines = [f"ab{CURSOR_MARKER}cd"]
        assert tui._extract_cursor_position(lines, 6) == (0, 2)
        assert lines == ["abcd"], "the marker must never reach the terminal"

    def test_the_last_marker_in_the_viewport_wins(self) -> None:
        tui, _ = make_tui()
        lines = [f"a{CURSOR_MARKER}", f"bb{CURSOR_MARKER}"]
        assert tui._extract_cursor_position(lines, 6) == (1, 2)

    def test_markers_above_the_viewport_are_ignored(self) -> None:
        tui, _ = make_tui()
        lines = [f"x{CURSOR_MARKER}", "a", "b"]
        assert tui._extract_cursor_position(lines, 2) is None

    def test_column_is_measured_in_display_columns(self) -> None:
        tui, _ = make_tui()
        lines = [f"你好{CURSOR_MARKER}"]
        assert tui._extract_cursor_position(lines, 6) == (0, 4)

    def test_no_marker_returns_none(self) -> None:
        tui, _ = make_tui()
        assert tui._extract_cursor_position(["plain"], 6) is None


class TestOverlayLayout:
    @pytest.mark.parametrize(
        ("value", "reference", "expected"),
        [
            (10, 80, 10),
            ("50%", 80, 40),
            ("33.5%", 100, 33),
            ("nonsense", 80, None),
            (None, 80, None),
        ],
    )
    def test_parse_size_value(self, value: object, reference: int, expected: int | None) -> None:
        assert _parse_size_value(value, reference) == expected  # pyright: ignore[reportArgumentType]

    def test_negative_margins_are_clamped_to_zero(self) -> None:
        tui, _ = make_tui(80, 24)
        layout = tui._resolve_overlay_layout(
            OverlayOptions(
                anchor="top-left",
                width=12,
                margin={"top": -5, "left": -10, "right": 0, "bottom": 0},
            ),
            1,
            80,
            24,
        )
        assert (layout.row, layout.col) == (0, 0)

    def test_margin_as_a_number_applies_to_all_sides(self) -> None:
        tui, _ = make_tui(80, 24)
        layout = tui._resolve_overlay_layout(
            OverlayOptions(anchor="top-left", width=10, margin=5), 1, 80, 24
        )
        assert (layout.row, layout.col) == (5, 5)

    def test_min_width_beats_a_small_percentage(self) -> None:
        tui, _ = make_tui(100, 24)
        layout = tui._resolve_overlay_layout(OverlayOptions(width="10%", min_width=30), 1, 100, 24)
        assert layout.width == 30

    def test_width_is_clamped_to_the_space_left_by_margins(self) -> None:
        tui, _ = make_tui(40, 24)
        layout = tui._resolve_overlay_layout(OverlayOptions(width=100, margin=4), 1, 40, 24)
        assert layout.width == 32

    def test_default_width_is_80_or_the_terminal(self) -> None:
        tui, _ = make_tui(200, 24)
        assert tui._resolve_overlay_layout(OverlayOptions(), 1, 200, 24).width == 80
        assert tui._resolve_overlay_layout(OverlayOptions(), 1, 30, 24).width == 30

    @pytest.mark.parametrize(
        ("anchor", "expected"),
        [
            ("top-left", (0, 0)),
            ("top-right", (0, 70)),
            ("bottom-left", (23, 0)),
            ("bottom-right", (23, 70)),
            ("center", (11, 35)),
            ("top-center", (0, 35)),
            ("bottom-center", (23, 35)),
            ("left-center", (11, 0)),
            ("right-center", (11, 70)),
        ],
    )
    def test_anchors(self, anchor: str, expected: tuple[int, int]) -> None:
        tui, _ = make_tui(80, 24)
        layout = tui._resolve_overlay_layout(
            OverlayOptions(anchor=anchor, width=10),  # pyright: ignore[reportArgumentType]
            1,
            80,
            24,
        )
        assert (layout.row, layout.col) == expected

    def test_offsets_shift_from_the_anchor(self) -> None:
        tui, _ = make_tui(80, 24)
        layout = tui._resolve_overlay_layout(
            OverlayOptions(anchor="top-left", width=10, offset_x=10, offset_y=5), 1, 80, 24
        )
        assert (layout.row, layout.col) == (5, 10)

    def test_position_is_clamped_inside_the_terminal(self) -> None:
        tui, _ = make_tui(80, 24)
        layout = tui._resolve_overlay_layout(
            OverlayOptions(anchor="top-left", width=10, offset_x=999, offset_y=999), 1, 80, 24
        )
        assert (layout.row, layout.col) == (23, 70)

    def test_an_invalid_percentage_falls_back_to_centre(self) -> None:
        tui, _ = make_tui(80, 24)
        layout = tui._resolve_overlay_layout(
            OverlayOptions(width=10, row="abc", col="abc"), 1, 80, 24
        )
        assert (layout.row, layout.col) == (11, 35)


class TestKittyImageIds:
    def test_extracts_the_image_id(self) -> None:
        assert _extract_kitty_image_ids("\x1b_Ga=T,i=42,f=100;payload\x1b\\") == [42]

    def test_ignores_lines_without_a_kitty_sequence(self) -> None:
        assert _extract_kitty_image_ids("plain text") == []

    def test_ignores_a_sequence_without_an_id(self) -> None:
        assert _extract_kitty_image_ids("\x1b_Ga=T,f=100;payload\x1b\\") == []

    @pytest.mark.parametrize("bad", ["0", "4294967296", "notanumber", ""])
    def test_rejects_out_of_range_or_malformed_ids(self, bad: str) -> None:
        assert _extract_kitty_image_ids(f"\x1b_Ga=T,i={bad},f=100;x\x1b\\") == []


class TestRenderScheduling:
    def test_request_render_never_paints_synchronously(self) -> None:
        # Load-bearing: show_overlay and friends all call request_render, and
        # painting inline would emit a frame per call instead of coalescing.
        tui, terminal = make_tui()
        tui.add_child(Static(["a"]))
        tui.request_render()
        assert terminal.writes == []

    def test_render_now_flushes_a_pending_frame(self) -> None:
        tui, terminal = make_tui()
        tui.add_child(Static(["hello"]))
        tui.request_render()
        tui.render_now()
        assert screen(terminal).line(0) == "hello"

    async def test_a_running_loop_paints_without_render_now(self) -> None:
        tui, terminal = make_tui()
        tui.add_child(Static(["async"]))
        tui.request_render()
        await asyncio.sleep(0.05)
        assert screen(terminal).line(0) == "async"

    async def test_repeated_requests_coalesce_into_one_frame(self) -> None:
        tui, terminal = make_tui()
        child = Static(["v0"])
        tui.add_child(child)
        for i in range(1, 6):
            child.set_lines([f"v{i}"])
            tui.request_render()
        await asyncio.sleep(0.08)
        assert screen(terminal).line(0) == "v5"
        # Only the initial paint, which `fullRender` counts in the TS too — the
        # five updates coalesced into one differential frame.
        assert tui.full_redraws == 1

    def test_a_stopped_tui_does_not_render(self) -> None:
        tui, terminal = make_tui()
        tui.add_child(Static(["a"]))
        tui.render_now()
        tui.stop()
        # stop() parks the cursor past the content; that write is expected.
        terminal.writes.clear()
        tui.request_render()
        tui.render_now()
        assert terminal.writes == []

    def test_force_resets_the_diff_state(self) -> None:
        tui, _ = make_tui()
        tui.add_child(Static(["a"]))
        tui.render_now()
        tui.request_render(force=True)
        assert tui._previous_lines == []
        assert tui._previous_width == -1


class TestCrashGuard:
    def test_an_overlong_line_raises_rather_than_corrupting_the_screen(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOOCODE_CODING_AGENT_DIR", str(tmp_path))
        tui, _ = make_tui(10, 4)
        child = Static(["short"])
        tui.add_child(child)
        tui.render_now()

        child.set_lines(["x" * 40])
        with pytest.raises(RuntimeError, match="exceeds terminal width"):
            tui.render_now()

        crash_log = tmp_path / "hoocode-crash.log"
        assert crash_log.is_file()
        assert "All rendered lines" in crash_log.read_text()


class TestFocusable:
    def test_is_focusable_detects_the_attribute(self) -> None:
        class WithFocus:
            focused = False

        class WithoutFocus:
            pass

        assert is_focusable(WithFocus()) is True
        assert is_focusable(WithoutFocus()) is False
        assert is_focusable(None) is False

    def test_set_focus_toggles_the_flag_on_both_components(self) -> None:
        class WithFocus:
            def __init__(self) -> None:
                self.focused = False

            def render(self, width: int) -> list[str]:
                return []

            def invalidate(self) -> None:
                pass

        tui, _ = make_tui()
        first = WithFocus()
        second = WithFocus()
        tui.set_focus(first)
        assert first.focused is True
        tui.set_focus(second)
        assert first.focused is False
        assert second.focused is True


class TestCellSizeQuery:
    """Ports `test/tui-cell-size-input.test.ts`.

    The renderer asks the terminal for its cell size at `start()` and swallows
    the reply, which only matters because `terminal-image.ts` measures images
    against it. Before step 1.15 the capability lookup was a soft dependency
    that always answered "no images", so neither half of this ran.
    """

    def test_no_query_without_an_image_protocol(self) -> None:
        set_capabilities(TerminalCapabilities(images=None, true_color=True, hyperlinks=False))
        tui, terminal = make_tui()
        tui.start()
        assert "\x1b[16t" not in terminal.stream
        tui.stop()

    def test_queries_the_cell_size_on_an_image_capable_terminal(self) -> None:
        set_capabilities(TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True))
        tui, terminal = make_tui()
        tui.start()
        assert "\x1b[16t" in terminal.stream
        tui.stop()

    def test_forwards_a_bare_escape_even_though_a_query_was_sent(self) -> None:
        set_capabilities(TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True))
        tui, terminal = make_tui()
        recorder = InputRecorder()
        tui.set_focus(recorder)
        tui.start()

        terminal.send_input("\x1b")

        assert recorder.inputs == ["\x1b"]
        tui.stop()

    def test_consumes_cell_size_responses_and_still_forwards_later_input(self) -> None:
        set_capabilities(TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True))
        set_cell_dimensions(CellDimensions(width_px=9, height_px=18))
        tui, terminal = make_tui()
        recorder = InputRecorder()
        tui.set_focus(recorder)
        tui.start()

        # CSI 6 ; height ; width t
        terminal.send_input("\x1b[6;20;10t")
        assert recorder.inputs == []
        assert get_cell_dimensions() == CellDimensions(width_px=10, height_px=20)

        terminal.send_input("q")
        assert recorder.inputs == ["q"]
        tui.stop()

    def test_a_zero_sized_reply_is_consumed_but_ignored(self) -> None:
        set_capabilities(TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True))
        set_cell_dimensions(CellDimensions(width_px=9, height_px=18))
        tui, terminal = make_tui()
        recorder = InputRecorder()
        tui.set_focus(recorder)
        tui.start()

        terminal.send_input("\x1b[6;0;10t")
        assert recorder.inputs == []
        assert get_cell_dimensions() == CellDimensions(width_px=9, height_px=18)
        tui.stop()

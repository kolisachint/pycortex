# pyright: reportPrivateUsage=false
# `focused` is set on duck-typed test doubles, which pyright cannot see
# through the `Component` protocol.
"""Overlay focus semantics — ported from hoocode's `overlay-non-capturing.test.ts`.

Focus is the part of the overlay stack the parity harness cannot see: it never
reaches the screen, so it needs assertions of its own. Everything that *is*
visible (placement, sizing, compositing) is covered by the `renderer/overlay-*`
scenarios in `packages/tui/testkit`, which diff whole screens against the real
TypeScript implementation.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from cortex.tui.render import TUI
from cortex.tui.testkit import CaptureTerminal


class StaticOverlay:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def render(self, width: int) -> list[str]:
        return self._lines

    def invalidate(self) -> None:
        pass


class EmptyContent:
    def render(self, width: int) -> list[str]:
        return []

    def invalidate(self) -> None:
        pass


class FocusableOverlay:
    def __init__(self, lines: list[str]) -> None:
        self.focused = False
        self.inputs: list[str] = []
        self._lines = lines

    def handle_input(self, data: str) -> None:
        self.inputs.append(data)

    def render(self, width: int) -> list[str]:
        return self._lines

    def invalidate(self) -> None:
        pass


@pytest.fixture
def tui() -> Iterator[TUI]:
    terminal = CaptureTerminal(80, 24)
    instance = TUI(terminal)  # pyright: ignore[reportArgumentType]
    instance.add_child(EmptyContent())
    # start() is what wires the terminal's input callback through to the
    # focused component, so the input assertions below need it.
    instance.start()
    yield instance
    instance.stop()


def test_non_capturing_overlay_preserves_focus_on_creation(tui: TUI) -> None:
    editor = FocusableOverlay(["EDITOR"])
    overlay = FocusableOverlay(["OVERLAY"])
    tui.set_focus(editor)
    tui.show_overlay(overlay, {"non_capturing": True})

    assert editor.focused is True
    assert overlay.focused is False


def test_focus_transfers_focus_to_the_overlay(tui: TUI) -> None:
    editor = FocusableOverlay(["EDITOR"])
    overlay = FocusableOverlay(["OVERLAY"])
    tui.set_focus(editor)
    handle = tui.show_overlay(overlay, {"non_capturing": True})

    handle.focus()

    assert editor.focused is False
    assert overlay.focused is True
    assert handle.is_focused() is True


def test_unfocus_restores_previous_focus(tui: TUI) -> None:
    editor = FocusableOverlay(["EDITOR"])
    overlay = FocusableOverlay(["OVERLAY"])
    tui.set_focus(editor)
    handle = tui.show_overlay(overlay, {"non_capturing": True})
    handle.focus()

    handle.unfocus()

    assert editor.focused is True
    assert overlay.focused is False
    assert handle.is_focused() is False


def test_set_hidden_false_does_not_auto_focus_a_non_capturing_overlay(tui: TUI) -> None:
    editor = FocusableOverlay(["EDITOR"])
    overlay = FocusableOverlay(["OVERLAY"])
    tui.set_focus(editor)
    handle = tui.show_overlay(overlay, {"non_capturing": True})

    handle.set_hidden(True)
    handle.set_hidden(False)

    assert editor.focused is True
    assert overlay.focused is False


def test_hide_when_not_focused_leaves_focus_alone(tui: TUI) -> None:
    editor = FocusableOverlay(["EDITOR"])
    overlay = FocusableOverlay(["OVERLAY"])
    tui.set_focus(editor)
    handle = tui.show_overlay(overlay, {"non_capturing": True})

    handle.hide()

    assert editor.focused is True


def test_hide_when_focused_restores_focus(tui: TUI) -> None:
    editor = FocusableOverlay(["EDITOR"])
    overlay = FocusableOverlay(["OVERLAY"])
    tui.set_focus(editor)
    handle = tui.show_overlay(overlay)

    assert overlay.focused is True
    handle.hide()

    assert editor.focused is True
    assert overlay.focused is False


def test_removing_capturing_overlay_skips_non_capturing_below_it(tui: TUI) -> None:
    editor = FocusableOverlay(["EDITOR"])
    non_capturing = FocusableOverlay(["NC"])
    capturing = FocusableOverlay(["CAP"])
    tui.set_focus(editor)

    tui.show_overlay(non_capturing, {"non_capturing": True})
    handle = tui.show_overlay(capturing)
    assert capturing.focused is True

    handle.hide()

    # The non-capturing overlay is still on the stack but must not take focus.
    assert editor.focused is True
    assert non_capturing.focused is False


def test_sub_overlay_cleanup_then_hide_overlay_restores_focus_and_input(tui: TUI) -> None:
    editor = FocusableOverlay(["EDITOR"])
    timer = FocusableOverlay(["TIMER"])
    controller = FocusableOverlay(["CTRL"])
    tui.set_focus(editor)

    timer_handle = tui.show_overlay(timer, {"non_capturing": True})
    tui.show_overlay(controller)
    assert controller.focused is True
    assert editor.focused is False

    timer_handle.hide()
    tui.hide_overlay()

    assert editor.focused is True
    assert controller.focused is False
    assert timer.focused is False

    tui.terminal.send_input("x")  # pyright: ignore[reportAttributeAccessIssue]
    assert editor.inputs == ["x"]
    assert controller.inputs == []
    assert timer.inputs == []


def test_has_overlay_ignores_hidden_entries(tui: TUI) -> None:
    handle = tui.show_overlay(StaticOverlay(["x"]))
    assert tui.has_overlay() is True

    handle.set_hidden(True)
    assert tui.has_overlay() is False

    handle.set_hidden(False)
    assert tui.has_overlay() is True


def test_visible_callback_controls_visibility(tui: TUI) -> None:
    # A `visible` predicate is re-evaluated against the live terminal size.
    def wide_enough(term_width: int, term_height: int) -> bool:
        return term_width >= 100

    tui.show_overlay(StaticOverlay(["x"]), {"visible": wide_enough})
    assert tui.has_overlay() is False

    tui.terminal.resize(120, 24)  # pyright: ignore[reportAttributeAccessIssue]
    assert tui.has_overlay() is True


def test_hide_is_idempotent(tui: TUI) -> None:
    handle = tui.show_overlay(StaticOverlay(["x"]))
    handle.hide()
    handle.hide()
    assert tui.has_overlay() is False


def test_input_goes_to_the_focused_overlay_not_the_base(tui: TUI) -> None:
    editor = FocusableOverlay(["EDITOR"])
    overlay = FocusableOverlay(["OVERLAY"])
    tui.set_focus(editor)
    tui.show_overlay(overlay)

    tui.terminal.send_input("k")  # pyright: ignore[reportAttributeAccessIssue]

    assert overlay.inputs == ["k"]
    assert editor.inputs == []

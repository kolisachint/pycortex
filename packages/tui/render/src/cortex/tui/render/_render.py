"""Minimal TUI implementation with differential rendering.

Mechanical port of hoocode's ``packages/tui/src/tui.ts`` (1545 lines), kept in
one module to stay diffable against it.

Based on code from OpenTUI (https://github.com/anomalyco/opentui)
MIT License - Copyright (c) 2025 opentui

Verified against the real TypeScript implementation by
``packages/tui/testkit``: every renderer scenario in the shared corpus is
rendered by both sides and the resulting terminal *screens* are diffed. Reading
this against `tui.ts` is not enough — the previous version of this file looked
reasonable and produced a different screen.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypeGuard, runtime_checkable

from cortex.tui.keys import is_key_release, matches_key
from cortex.tui.util import (
    extract_segments,
    is_image_line,
    normalize_terminal_output,
    slice_by_column,
    slice_with_width,
    visible_width,
)

__all__ = [
    "CURSOR_MARKER",
    "Component",
    "Container",
    "Focusable",
    "OverlayHandle",
    "OverlayOptions",
    "TUI",
    "Terminal",
    "is_focusable",
    "visible_width",
]

KITTY_SEQUENCE_PREFIX = "\x1b_G"

# Cursor position marker — an APC (Application Program Command) sequence.
# A zero-width escape terminals ignore. Components emit it at the cursor
# position when focused; the TUI finds it, strips it, and puts the hardware
# cursor there so IME candidate windows land in the right place.
CURSOR_MARKER = "\x1b_pi:c\x07"

_PERCENT_RE = re.compile(r"^(\d+(?:\.\d+)?)%$")
_CELL_SIZE_RESPONSE_RE = re.compile(r"^\x1b\[6;(\d+);(\d+)t$")

MIN_RENDER_INTERVAL_MS = 16
SEGMENT_RESET = "\x1b[0m\x1b]8;;\x07"

OverlayAnchor = Literal[
    "center",
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
    "top-center",
    "bottom-center",
    "left-center",
    "right-center",
]

# Absolute (int) or percentage ("50%").
SizeValue = int | str


def _extract_kitty_image_ids(line: str) -> list[int]:
    sequence_start = line.find(KITTY_SEQUENCE_PREFIX)
    if sequence_start == -1:
        return []

    params_start = sequence_start + len(KITTY_SEQUENCE_PREFIX)
    params_end = line.find(";", params_start)
    if params_end == -1:
        return []

    params = line[params_start:params_end]
    for param in params.split(","):
        key, _, value = param.partition("=")
        if key != "i" or not value:
            continue
        try:
            image_id = int(value)
        except ValueError:
            continue
        if 0 < image_id <= 0xFFFFFFFF:
            return [image_id]
    return []


def _images_symbol(name: str) -> Any:
    """Look up a `cortex.tui.images` symbol, or None while step 1.15 is pending.

    Resolved dynamically because the leaf exists but is empty: a static import
    of a name it does not export yet would not type-check, and stubbing the
    functions here would duplicate 1.15's scope.
    """
    try:
        module = importlib.import_module("cortex.tui.images")
    except ImportError:
        return None
    return getattr(module, name, None)


def _delete_kitty_image(image_id: int) -> str:
    """Escape sequence that frees one kitty image.

    Owned by ``terminal-image.ts`` → the images leaf (step 1.15). Inlined here
    so the kitty bookkeeping below is a real port rather than a stub, and so it
    is testable today; 1.15 replaces this with an import.
    """
    return f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\"


def _terminal_supports_images() -> bool:
    """Whether ``queryCellSize`` should ask the terminal for its cell size.

    The TS calls ``getCapabilities().images`` from ``terminal-image.ts``. That
    module is step 1.15; until it lands this reports "no image support", which
    is a real runtime state (the TS takes the same branch on a terminal without
    an image protocol) rather than an invented one.
    """
    get_capabilities = _images_symbol("get_capabilities")
    if get_capabilities is None:
        return False
    return bool(get_capabilities().images)


def _set_cell_dimensions(width_px: int, height_px: int) -> None:
    """Record the terminal's cell size. See ``_terminal_supports_images``."""
    set_cell_dimensions = _images_symbol("set_cell_dimensions")
    if set_cell_dimensions is None:
        return
    set_cell_dimensions({"widthPx": width_px, "heightPx": height_px})


@runtime_checkable
class Terminal(Protocol):
    """The subset of ``cortex.tui.terminal.Terminal`` the renderer uses."""

    @property
    def columns(self) -> int: ...

    @property
    def rows(self) -> int: ...

    def write(self, data: str) -> None: ...

    def start(self, on_input: Callable[[str], None], on_resize: Callable[[], None]) -> None: ...

    def stop(self) -> None: ...

    def hide_cursor(self) -> None: ...

    def show_cursor(self) -> None: ...


@runtime_checkable
class Component(Protocol):
    """All components implement this."""

    def render(self, width: int) -> list[str]:
        """Render to lines for the given viewport width."""
        ...

    def invalidate(self) -> None:
        """Drop any cached rendering state (theme change, forced re-render)."""
        ...


class Focusable(Protocol):
    """A component that can hold focus and show a hardware cursor.

    When focused it should emit ``CURSOR_MARKER`` at the cursor position in its
    render output; the TUI finds the marker and positions the hardware cursor.
    """

    focused: bool


def is_focusable(component: object | None) -> TypeGuard[Focusable]:
    """Whether a component participates in hardware-cursor positioning."""
    return component is not None and hasattr(component, "focused")


def _parse_size_value(value: SizeValue | None, reference_size: int) -> int | None:
    """Resolve an absolute or percentage size against a reference."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    match = _PERCENT_RE.match(value)
    if match:
        return int(reference_size * float(match.group(1)) / 100)
    return None


def _is_termux_session() -> bool:
    return bool(os.environ.get("TERMUX_VERSION"))


@dataclass
class OverlayOptions:
    """Positioning and sizing for an overlay. Sizes may be absolute or "50%"."""

    # Sizing
    width: SizeValue | None = None
    min_width: int | None = None
    max_height: SizeValue | None = None

    # Anchor-based positioning
    anchor: OverlayAnchor | None = None
    offset_x: int | None = None
    offset_y: int | None = None

    # Explicit positioning (absolute or percentage)
    row: SizeValue | None = None
    col: SizeValue | None = None

    # Margin from the terminal edges; an int applies to all sides.
    margin: dict[str, int] | int | None = None

    # Visibility
    visible: Callable[[int, int], bool] | None = None
    non_capturing: bool = False

    @classmethod
    def coerce(cls, options: OverlayOptions | dict[str, Any] | None) -> OverlayOptions:
        if options is None:
            return cls()
        if isinstance(options, OverlayOptions):
            return options
        return cls(**options)


@dataclass
class _OverlayEntry:
    component: Component
    options: OverlayOptions
    pre_focus: Component | None
    hidden: bool
    focus_order: int


@dataclass
class _Layout:
    width: int
    row: int
    col: int
    max_height: int | None


@dataclass
class _Patch:
    """Dirty row range reported by ``TUI.render`` for one frame."""

    low: int
    high: int
    prev_length: int


# `TUI.render` reports either a patch, `None` (nothing changed), or "full"
# (unknown — the legacy whole-buffer diff must run).
_PatchReport = _Patch | None | Literal["full"]


@dataclass
class OverlayHandle:
    """Controls one overlay. Returned by :meth:`TUI.show_overlay`.

    A bundle of closures rather than a class holding a `TUI` reference, mirroring
    the object literal the TS returns. That keeps every mutation of the overlay
    stack inside `TUI` where it belongs, instead of a sibling class reaching
    into its internals.
    """

    #: Permanently remove the overlay; it cannot be shown again.
    hide: Callable[[], None]
    #: Temporarily hide or re-show the overlay.
    set_hidden: Callable[[bool], None]
    is_hidden: Callable[[], bool]
    #: Focus this overlay and bring it to the visual front.
    focus: Callable[[], None]
    #: Release focus to the previous target.
    unfocus: Callable[[], None]
    is_focused: Callable[[], bool]


class Container:
    """A component that contains other components."""

    def __init__(self) -> None:
        self.children: list[Component] = []
        # Flatten memo: children are always render()ed (side effects and their
        # own caches must run), but when every child returns the same list
        # *object* as last time, the previously flattened list is returned
        # as-is. Unchanged subtrees thus stay identity-stable all the way up,
        # which lets the TUI root diff whole regions by identity instead of
        # re-flattening the world.
        self._render_memo: tuple[int, list[list[str]], list[str]] | None = None

    def add_child(self, component: Component) -> None:
        self.children.append(component)
        self._render_memo = None

    def remove_child(self, component: Component) -> None:
        try:
            self.children.remove(component)
        except ValueError:
            return
        self._render_memo = None

    def clear(self) -> None:
        self.children = []
        self._render_memo = None

    def invalidate(self) -> None:
        self._render_memo = None
        for child in self.children:
            child.invalidate()

    def render(self, width: int) -> list[str]:
        n = len(self.children)
        memo = self._render_memo
        refs: list[list[str]] = [[] for _ in range(n)]
        unchanged = memo is not None and memo[0] == width and len(memo[1]) == n
        for i in range(n):
            refs[i] = self.children[i].render(width)
            # Identity, not equality: equal-but-fresh lists mean the child
            # re-rendered, and the root's patch tracking depends on knowing that.
            if unchanged and memo is not None and refs[i] is not memo[1][i]:
                unchanged = False
        if unchanged and memo is not None:
            return memo[2]
        lines: list[str] = []
        for child_lines in refs:
            lines.extend(child_lines)
        self._render_memo = (width, refs, lines)
        return lines


class TUI(Container):
    """Manages a terminal UI with differential rendering."""

    def __init__(self, terminal: Terminal, show_hardware_cursor: bool | None = None) -> None:
        super().__init__()
        self.terminal = terminal
        self._previous_lines: list[str] = []
        # Root flat-line cache (see the render() override): per-child line
        # lists, their offsets into the flat buffer, and the flat buffer itself.
        # Active only when no overlays are up and no image has been drawn;
        # otherwise the legacy full-flatten + full-diff path runs.
        self._flat_cache: tuple[int, list[list[str]], list[int]] | None = None
        self._flat_lines: list[str] | None = None
        self._last_patch: _PatchReport = "full"
        # Cursor position extracted on the last frame; reused when the dirty
        # range shows the marker's row untouched (it was already stripped).
        self._last_cursor_pos: tuple[int, int] | None = None
        self._has_cursor_pos = False  # distinguishes "None" from "never extracted"
        # Set by render() when this frame's patches invalidate the cached
        # cursor: its row was overwritten, or a patched-in line carries a marker.
        self._cursor_row_overwritten = False
        self._previous_kitty_image_ids: set[int] = set()
        # Flips true the first time an image line is emitted. While false no
        # image has ever been drawn, so there are no kitty ids on screen and the
        # per-frame full-buffer scan is skipped — the common pure-text case.
        self._saw_image_line = False
        self._previous_width = 0
        self._previous_height = 0
        self._focused_component: Component | None = None
        self._input_listeners: list[Callable[[str], dict[str, Any] | None]] = []

        # Called before input reaches the focused component (Shift+Ctrl+D).
        self.on_debug: Callable[[], None] | None = None
        self._render_requested = False
        self._render_timer: asyncio.TimerHandle | None = None
        self._last_render_at = 0.0
        self._cursor_row = 0  # logical cursor row (end of rendered content)
        self._hardware_cursor_row = 0  # actual terminal row (may differ for IME)
        self._show_hardware_cursor = os.environ.get("HOOCODE_HARDWARE_CURSOR") == "1"
        # Clear empty rows when content shrinks (default off).
        self._clear_on_shrink = os.environ.get("HOOCODE_CLEAR_ON_SHRINK") == "1"
        self._max_lines_rendered = 0  # terminal working area high-water mark
        self._previous_viewport_top = 0  # for resize-aware cursor moves
        self._full_redraw_count = 0
        self._stopped = False

        self._focus_order_counter = 0
        self._overlay_stack: list[_OverlayEntry] = []

        if show_hardware_cursor is not None:
            self._show_hardware_cursor = show_hardware_cursor

    # ---- configuration ----------------------------------------------------

    @property
    def full_redraws(self) -> int:
        return self._full_redraw_count

    def get_show_hardware_cursor(self) -> bool:
        return self._show_hardware_cursor

    def set_show_hardware_cursor(self, enabled: bool) -> None:
        if self._show_hardware_cursor == enabled:
            return
        self._show_hardware_cursor = enabled
        if not enabled:
            self.terminal.hide_cursor()
        self.request_render()

    def get_clear_on_shrink(self) -> bool:
        return self._clear_on_shrink

    def set_clear_on_shrink(self, enabled: bool) -> None:
        """Whether shrinking content triggers a full re-render to clear rows.

        Off by default: on slower terminals the extra redraws cost more than the
        stale rows do.
        """
        self._clear_on_shrink = enabled

    def set_focus(self, component: Component | None) -> None:
        if is_focusable(self._focused_component):
            self._focused_component.focused = False
        self._focused_component = component
        if is_focusable(component):
            component.focused = True

    # ---- overlays ---------------------------------------------------------

    def show_overlay(
        self,
        component: Component,
        options: OverlayOptions | dict[str, Any] | None = None,
    ) -> OverlayHandle:
        """Show an overlay on top of the base content. Returns its handle."""
        resolved = OverlayOptions.coerce(options)
        self._focus_order_counter += 1
        entry = _OverlayEntry(
            component=component,
            options=resolved,
            pre_focus=self._focused_component,
            hidden=False,
            focus_order=self._focus_order_counter,
        )
        self._overlay_stack.append(entry)
        if not resolved.non_capturing and self._is_overlay_visible(entry):
            self.set_focus(component)
        self.terminal.hide_cursor()
        self.request_render()

        def hide() -> None:
            if entry not in self._overlay_stack:
                return
            self._overlay_stack.remove(entry)
            if self._focused_component is entry.component:
                top_visible = self._topmost_visible_overlay()
                self.set_focus(top_visible.component if top_visible else entry.pre_focus)
            if not self._overlay_stack:
                self.terminal.hide_cursor()
            self.request_render()

        def set_hidden(hidden: bool) -> None:
            if entry.hidden == hidden:
                return
            entry.hidden = hidden
            if hidden:
                if self._focused_component is entry.component:
                    top_visible = self._topmost_visible_overlay()
                    self.set_focus(top_visible.component if top_visible else entry.pre_focus)
            elif not resolved.non_capturing and self._is_overlay_visible(entry):
                self._focus_order_counter += 1
                entry.focus_order = self._focus_order_counter
                self.set_focus(entry.component)
            self.request_render()

        def focus() -> None:
            if entry not in self._overlay_stack or not self._is_overlay_visible(entry):
                return
            if self._focused_component is not entry.component:
                self.set_focus(entry.component)
            self._focus_order_counter += 1
            entry.focus_order = self._focus_order_counter
            self.request_render()

        def unfocus() -> None:
            if self._focused_component is not entry.component:
                return
            top_visible = self._topmost_visible_overlay()
            if top_visible is not None and top_visible is not entry:
                self.set_focus(top_visible.component)
            else:
                self.set_focus(entry.pre_focus)
            self.request_render()

        return OverlayHandle(
            hide=hide,
            set_hidden=set_hidden,
            is_hidden=lambda: entry.hidden,
            focus=focus,
            unfocus=unfocus,
            is_focused=lambda: self._focused_component is entry.component,
        )

    def hide_overlay(self) -> None:
        """Hide the topmost overlay and restore the previous focus."""
        if not self._overlay_stack:
            return
        overlay = self._overlay_stack.pop()
        if self._focused_component is overlay.component:
            top_visible = self._topmost_visible_overlay()
            self.set_focus(top_visible.component if top_visible else overlay.pre_focus)
        if not self._overlay_stack:
            self.terminal.hide_cursor()
        self.request_render()

    def has_overlay(self) -> bool:
        return any(self._is_overlay_visible(entry) for entry in self._overlay_stack)

    def _is_overlay_visible(self, entry: _OverlayEntry) -> bool:
        if entry.hidden:
            return False
        if entry.options.visible is not None:
            return entry.options.visible(self.terminal.columns, self.terminal.rows)
        return True

    def _topmost_visible_overlay(self) -> _OverlayEntry | None:
        """The topmost visible *capturing* overlay, if any."""
        for entry in reversed(self._overlay_stack):
            if entry.options.non_capturing:
                continue
            if self._is_overlay_visible(entry):
                return entry
        return None

    def invalidate(self) -> None:
        super().invalidate()
        for overlay in self._overlay_stack:
            overlay.component.invalidate()

    # ---- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._stopped = False
        self.terminal.start(self._handle_input, self.request_render)
        self.terminal.hide_cursor()
        self._query_cell_size()
        self.request_render()

    def stop(self) -> None:
        self._stopped = True
        if self._render_timer is not None:
            self._render_timer.cancel()
            self._render_timer = None
        # Park the cursor past the content so the shell prompt does not
        # overwrite the last frame.
        if self._previous_lines:
            target_row = len(self._previous_lines)
            line_diff = target_row - self._hardware_cursor_row
            if line_diff > 0:
                self.terminal.write(f"\x1b[{line_diff}B")
            elif line_diff < 0:
                self.terminal.write(f"\x1b[{-line_diff}A")
            self.terminal.write("\r\n")

        self.terminal.show_cursor()
        self.terminal.stop()

    def add_input_listener(
        self, listener: Callable[[str], dict[str, Any] | None]
    ) -> Callable[[], None]:
        self._input_listeners.append(listener)

        def dispose() -> None:
            self.remove_input_listener(listener)

        return dispose

    def remove_input_listener(self, listener: Callable[[str], dict[str, Any] | None]) -> None:
        if listener in self._input_listeners:
            self._input_listeners.remove(listener)

    def _query_cell_size(self) -> None:
        # Cell size is only used for image rendering.
        if not _terminal_supports_images():
            return
        # CSI 16 t — the reply is CSI 6 ; height ; width t
        self.terminal.write("\x1b[16t")

    # ---- render scheduling ------------------------------------------------

    def request_render(self, force: bool = False) -> None:
        """Ask for a frame. Never renders synchronously.

        Coalesced to one frame per ``MIN_RENDER_INTERVAL_MS`` via the running
        event loop. The "never synchronous" part is load-bearing, not incidental:
        ``show_overlay`` and friends all call this, and painting inline would
        make each of them emit its own frame instead of coalescing into one.
        With no event loop running the request simply stays pending — call
        :meth:`render_now` to flush it.
        """
        if force:
            self._previous_lines = []
            self._previous_width = -1  # -1 forces a full clear via widthChanged
            self._previous_height = -1
            self._cursor_row = 0
            self._hardware_cursor_row = 0
            self._max_lines_rendered = 0
            self._previous_viewport_top = 0
            if self._render_timer is not None:
                self._render_timer.cancel()
                self._render_timer = None
            self._render_requested = True
            self._soon(self._render_forced)
            return
        if self._render_requested:
            return
        self._render_requested = True
        self._soon(self._schedule_render)

    def _soon(self, callback: Callable[[], None]) -> None:
        """The TS `process.nextTick`. With no loop, the request stays pending."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.call_soon(callback)

    def render_now(self) -> None:
        """Render a pending frame immediately, bypassing the coalescing delay.

        The escape hatch for callers without a running event loop, and the entry
        point the parity harness drives (it is the counterpart of reaching for
        `doRender` in the TS tests).
        """
        if self._stopped:
            return
        if self._render_timer is not None:
            self._render_timer.cancel()
            self._render_timer = None
        self._render_requested = False
        self._last_render_at = time.monotonic() * 1000
        self._do_render()

    def _render_forced(self) -> None:
        if self._stopped or not self._render_requested:
            return
        self._render_requested = False
        self._last_render_at = time.monotonic() * 1000
        self._do_render()

    def _schedule_render(self) -> None:
        if self._stopped or self._render_timer is not None or not self._render_requested:
            return
        elapsed = time.monotonic() * 1000 - self._last_render_at
        delay = max(0.0, MIN_RENDER_INTERVAL_MS - elapsed)

        def fire() -> None:
            self._render_timer = None
            if self._stopped or not self._render_requested:
                return
            self._render_requested = False
            self._last_render_at = time.monotonic() * 1000
            self._do_render()
            if self._render_requested:
                self._schedule_render()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            fire()
            return
        self._render_timer = loop.call_later(delay / 1000, fire)

    def _expedite_render(self) -> None:
        """Render a pending frame now, skipping the coalescing delay.

        Used for input-driven frames, where echo latency matters more than
        batching with spinner/streaming frames.
        """
        if self._stopped or not self._render_requested:
            return
        self.render_now()

    # ---- input ------------------------------------------------------------

    def _handle_input(self, data: str) -> None:
        if self._input_listeners:
            current = data
            for listener in list(self._input_listeners):
                result = listener(current)
                if result is not None and result.get("consume"):
                    return
                if result is not None and result.get("data") is not None:
                    current = result["data"]
            if not current:
                return
            data = current

        # Consume cell-size replies without blocking unrelated input.
        if self._consume_cell_size_response(data):
            return

        if matches_key(data, "shift+ctrl+d") and self.on_debug is not None:
            self.on_debug()
            return

        # A focused overlay may have become invisible (resize, visible callback).
        focused_overlay = next(
            (o for o in self._overlay_stack if o.component is self._focused_component), None
        )
        if focused_overlay is not None and not self._is_overlay_visible(focused_overlay):
            top_visible = self._topmost_visible_overlay()
            if top_visible is not None:
                self.set_focus(top_visible.component)
            else:
                self.set_focus(focused_overlay.pre_focus)

        # Ctrl+C included: the focused component decides what to do with it.
        focused = self._focused_component
        handle_input = getattr(focused, "handle_input", None)
        if focused is not None and handle_input is not None:
            if is_key_release(data) and not getattr(focused, "wants_key_release", False):
                return
            handle_input(data)
            self.request_render()
            self._expedite_render()

    def _consume_cell_size_response(self, data: str) -> bool:
        match = _CELL_SIZE_RESPONSE_RE.match(data)
        if not match:
            return False
        height_px = int(match.group(1))
        width_px = int(match.group(2))
        if height_px <= 0 or width_px <= 0:
            return True
        _set_cell_dimensions(width_px, height_px)
        # Re-render every component so images pick up the real dimensions.
        self.invalidate()
        self.request_render()
        return True

    # ---- overlay layout ---------------------------------------------------

    def _resolve_overlay_layout(
        self,
        options: OverlayOptions,
        overlay_height: int,
        term_width: int,
        term_height: int,
    ) -> _Layout:
        margin = options.margin
        if isinstance(margin, int):
            margin_map = {"top": margin, "right": margin, "bottom": margin, "left": margin}
        else:
            margin_map = margin or {}
        margin_top = max(0, margin_map.get("top", 0))
        margin_right = max(0, margin_map.get("right", 0))
        margin_bottom = max(0, margin_map.get("bottom", 0))
        margin_left = max(0, margin_map.get("left", 0))

        avail_width = max(1, term_width - margin_left - margin_right)
        avail_height = max(1, term_height - margin_top - margin_bottom)

        width = _parse_size_value(options.width, term_width)
        if width is None:
            width = min(80, avail_width)
        if options.min_width is not None:
            width = max(width, options.min_width)
        width = max(1, min(width, avail_width))

        max_height = _parse_size_value(options.max_height, term_height)
        if max_height is not None:
            max_height = max(1, min(max_height, avail_height))

        effective_height = (
            min(overlay_height, max_height) if max_height is not None else overlay_height
        )

        anchor: OverlayAnchor = options.anchor or "center"

        if options.row is not None:
            if isinstance(options.row, str):
                match = _PERCENT_RE.match(options.row)
                if match:
                    # 0% = top, 100% = bottom, staying inside the margins.
                    max_row = max(0, avail_height - effective_height)
                    row = margin_top + int(max_row * float(match.group(1)) / 100)
                else:
                    row = self._resolve_anchor_row(
                        "center", effective_height, avail_height, margin_top
                    )
            else:
                row = options.row
        else:
            row = self._resolve_anchor_row(anchor, effective_height, avail_height, margin_top)

        if options.col is not None:
            if isinstance(options.col, str):
                match = _PERCENT_RE.match(options.col)
                if match:
                    max_col = max(0, avail_width - width)
                    col = margin_left + int(max_col * float(match.group(1)) / 100)
                else:
                    col = self._resolve_anchor_col("center", width, avail_width, margin_left)
            else:
                col = options.col
        else:
            col = self._resolve_anchor_col(anchor, width, avail_width, margin_left)

        if options.offset_y is not None:
            row += options.offset_y
        if options.offset_x is not None:
            col += options.offset_x

        row = max(margin_top, min(row, term_height - margin_bottom - effective_height))
        col = max(margin_left, min(col, term_width - margin_right - width))

        return _Layout(width=width, row=row, col=col, max_height=max_height)

    @staticmethod
    def _resolve_anchor_row(
        anchor: OverlayAnchor, height: int, avail_height: int, margin_top: int
    ) -> int:
        if anchor in ("top-left", "top-center", "top-right"):
            return margin_top
        if anchor in ("bottom-left", "bottom-center", "bottom-right"):
            return margin_top + avail_height - height
        return margin_top + (avail_height - height) // 2

    @staticmethod
    def _resolve_anchor_col(
        anchor: OverlayAnchor, width: int, avail_width: int, margin_left: int
    ) -> int:
        if anchor in ("top-left", "left-center", "bottom-left"):
            return margin_left
        if anchor in ("top-right", "right-center", "bottom-right"):
            return margin_left + avail_width - width
        return margin_left + (avail_width - width) // 2

    def _composite_overlays(self, lines: list[str], term_width: int, term_height: int) -> list[str]:
        """Composite every visible overlay, lowest focus_order first."""
        if not self._overlay_stack:
            return lines
        result = list(lines)

        rendered: list[tuple[list[str], int, int, int]] = []
        min_lines_needed = len(result)

        visible_entries = [e for e in self._overlay_stack if self._is_overlay_visible(e)]
        visible_entries.sort(key=lambda e: e.focus_order)
        for entry in visible_entries:
            # Width and maxHeight do not depend on the overlay's height, so
            # resolve them first with height 0.
            layout = self._resolve_overlay_layout(entry.options, 0, term_width, term_height)
            overlay_lines = entry.component.render(layout.width)
            if layout.max_height is not None and len(overlay_lines) > layout.max_height:
                overlay_lines = overlay_lines[: layout.max_height]
            placed = self._resolve_overlay_layout(
                entry.options, len(overlay_lines), term_width, term_height
            )
            rendered.append((overlay_lines, placed.row, placed.col, layout.width))
            min_lines_needed = max(min_lines_needed, placed.row + len(overlay_lines))

        # Pad to at least the terminal height so overlays get screen-relative
        # positions. maxLinesRendered is deliberately excluded: that high-water
        # mark was self-reinforcing and pushed content into scrollback on widen.
        working_height = max(len(result), term_height, min_lines_needed)
        while len(result) < working_height:
            result.append("")

        viewport_start = max(0, working_height - term_height)

        for overlay_lines, row, col, width in rendered:
            for i, overlay_line in enumerate(overlay_lines):
                idx = viewport_start + row + i
                if 0 <= idx < len(result):
                    # Defensive: components should already respect the width.
                    truncated = (
                        slice_by_column(overlay_line, 0, width, True)
                        if visible_width(overlay_line) > width
                        else overlay_line
                    )
                    result[idx] = self._composite_line_at(
                        result[idx], truncated, col, width, term_width
                    )

        return result

    def _composite_line_at(
        self,
        base_line: str,
        overlay_line: str,
        start_col: int,
        overlay_width: int,
        total_width: int,
    ) -> str:
        """Splice overlay content into a base line at a column."""
        if is_image_line(base_line):
            return base_line

        after_start = start_col + overlay_width
        before, before_width, after, after_width = extract_segments(
            base_line, start_col, after_start, total_width - after_start, True
        )
        # strict=True excludes a wide char straddling the boundary.
        overlay_text, overlay_visible_width = slice_with_width(overlay_line, 0, overlay_width, True)

        before_pad = max(0, start_col - before_width)
        overlay_pad = max(0, overlay_width - overlay_visible_width)
        actual_before_width = max(start_col, before_width)
        actual_overlay_width = max(overlay_width, overlay_visible_width)
        after_target = max(0, total_width - actual_before_width - actual_overlay_width)
        after_pad = max(0, after_target - after_width)

        result = (
            before
            + " " * before_pad
            + SEGMENT_RESET
            + overlay_text
            + " " * overlay_pad
            + SEGMENT_RESET
            + after
            + " " * after_pad
        )

        # Final safeguard: width tracking can drift on complex OSC/SGR runs and
        # wide chars at boundaries, and an over-wide line crashes the renderer.
        if visible_width(result) <= total_width:
            return result
        return slice_by_column(result, 0, total_width, True)

    # ---- line emission ----------------------------------------------------

    def _emit_line(self, line: str) -> str:
        """Append the per-line style/hyperlink reset at write time.

        Deliberately kept OFF the cached/diffed line lists: leaves cache their
        lines without the reset, so leaving them un-reset keeps unchanged lines
        identity-stable frame to frame and lets the diff short-circuit on
        identity instead of allocating a reset-appended copy of the whole
        transcript every frame. Image lines carry no trailing style.
        """
        if is_image_line(line):
            self._saw_image_line = True
            return line
        return normalize_terminal_output(line) + SEGMENT_RESET

    def _collect_kitty_image_ids(self, lines: list[str]) -> set[int]:
        # Nothing on screen carries a kitty id until an image has been drawn.
        if not self._saw_image_line:
            return set()
        ids: set[int] = set()
        for line in lines:
            ids.update(_extract_kitty_image_ids(line))
        return ids

    def _delete_kitty_images(self, ids: Iterable[int]) -> str:
        return "".join(_delete_kitty_image(image_id) for image_id in ids)

    def _expand_last_changed_for_kitty_images(self, first_changed: int, last_changed: int) -> int:
        # No image ever drawn: nothing to expand over. (Also, on patched frames
        # previousLines is not the previous content.)
        if not self._saw_image_line:
            return last_changed
        expanded = last_changed
        for i in range(first_changed, len(self._previous_lines)):
            if _extract_kitty_image_ids(self._previous_lines[i]):
                expanded = max(expanded, i)
        return expanded

    def _delete_changed_kitty_images(self, first_changed: int, last_changed: int) -> str:
        if first_changed < 0 or last_changed < first_changed:
            return ""
        ids: set[int] = set()
        max_line = min(last_changed, len(self._previous_lines) - 1)
        for i in range(first_changed, max_line + 1):
            ids.update(_extract_kitty_image_ids(self._previous_lines[i]))
        return self._delete_kitty_images(ids)

    # ---- cursor -----------------------------------------------------------

    def _extract_cursor_position(self, lines: list[str], height: int) -> tuple[int, int] | None:
        """Find ``CURSOR_MARKER``, return its (row, col), and strip it in place.

        Only the bottom ``height`` lines (the visible viewport) are scanned.
        """
        viewport_top = max(0, len(lines) - height)
        for row in range(len(lines) - 1, viewport_top - 1, -1):
            line = lines[row]
            marker_index = line.find(CURSOR_MARKER)
            if marker_index != -1:
                col = visible_width(line[:marker_index])
                lines[row] = line[:marker_index] + line[marker_index + len(CURSOR_MARKER) :]
                return (row, col)
        return None

    # ---- root render ------------------------------------------------------

    def render(self, width: int) -> list[str]:
        """Root flatten with patch tracking.

        Children stay memoized (see :class:`Container`), so a frame where only
        one region changed patches that region into the persistent flat buffer
        and reports the dirty row range via ``_last_patch`` — ``_do_render``
        then skips the whole-transcript diff. Falls back to a fresh flatten
        (``"full"``) when overlays are up, an image has been drawn (kitty
        bookkeeping needs the true previous content), the width changed, or the
        child list changed.
        """
        cache_allowed = not self._overlay_stack and not self._saw_image_line
        cache = self._flat_cache
        if (
            not cache_allowed
            or cache is None
            or cache[0] != width
            or len(cache[1]) != len(self.children)
        ):
            n = len(self.children)
            refs: list[list[str]] = [[] for _ in range(n)]
            offsets: list[int] = [0] * n
            flat: list[str] = []
            for i in range(n):
                refs[i] = self.children[i].render(width)
                offsets[i] = len(flat)
                flat.extend(refs[i])
            if cache_allowed:
                self._flat_cache = (width, refs, offsets)
                self._flat_lines = flat
            else:
                self._flat_cache = None
                self._flat_lines = None
            self._last_patch = "full"
            return flat

        _, cache_refs, cache_offsets = cache
        flat = self._flat_lines if self._flat_lines is not None else []
        prev_length = len(flat)
        low = -1
        high = -1
        delta = 0
        # Cursor bookkeeping: the marker was stripped out of the persistent flat
        # buffer when last extracted, so the cached position stays valid until
        # the row it lives on is overwritten by re-imported child content — and
        # it shifts when content above it grows or shrinks.
        cp = self._last_cursor_pos
        self._cursor_row_overwritten = False
        spliced = False
        for i in range(len(self.children)):
            r = self.children[i].render(width)
            old = cache_refs[i]
            if r is old:
                continue
            off = cache_offsets[i] + delta
            if len(r) == len(old):
                for k in range(len(r)):
                    if old[k] != r[k]:
                        row = off + k
                        flat[row] = r[k]
                        if low == -1 or row < low:
                            low = row
                        if row > high:
                            high = row
                        # Overwrote the marker's row, or imported a line
                        # carrying a (possibly relocated) marker: re-extract.
                        if cp is not None and cp[0] == row:
                            self._cursor_row_overwritten = True
                        if CURSOR_MARKER in r[k]:
                            self._cursor_row_overwritten = True
            else:
                # Length changed: find the first differing line, then splice the
                # child's new lines in. Everything below shifts rows, so the
                # dirty range extends to the end (positional diff semantics).
                p = 0
                min_len = min(len(old), len(r))
                while p < min_len and old[p] == r[p]:
                    p += 1
                flat = flat[: off + p] + r[p:] + flat[off + len(old) :]
                spliced = True
                if low == -1 or off + p < low:
                    low = off + p
                if cp is not None:
                    if cp[0] >= off + len(old):
                        # Below the replaced region: shifts with it.
                        cp = (cp[0] + (len(r) - len(old)), cp[1])
                    elif cp[0] >= off + p:
                        # Inside the replaced region: fresh content, re-extract.
                        self._cursor_row_overwritten = True
                if not self._cursor_row_overwritten:
                    for k in range(p, len(r)):
                        if CURSOR_MARKER in r[k]:
                            self._cursor_row_overwritten = True
                            break
                delta += len(r) - len(old)
            cache_refs[i] = r

        self._last_cursor_pos = cp
        if spliced:
            acc = 0
            for i in range(len(cache_refs)):
                cache_offsets[i] = acc
                acc += len(cache_refs[i])
            self._flat_lines = flat
            # Rows below the first splice all shifted; positional diff semantics
            # mean everything from there to the end is dirty.
            high = max(prev_length - 1, len(flat) - 1)

        self._last_patch = (
            None if low == -1 and high == -1 else _Patch(max(low, 0), high, prev_length)
        )
        return flat

    # ---- frame ------------------------------------------------------------

    def _do_render(self) -> None:  # noqa: C901 - 1:1 with tui.ts doRender
        if self._stopped:
            return
        width = self.terminal.columns
        height = self.terminal.rows
        width_changed = self._previous_width != 0 and self._previous_width != width
        height_changed = self._previous_height != 0 and self._previous_height != height
        previous_buffer_length = (
            self._previous_viewport_top + self._previous_height
            if self._previous_height > 0
            else height
        )
        prev_viewport_top = (
            max(0, previous_buffer_length - height)
            if height_changed
            else self._previous_viewport_top
        )
        viewport_top = prev_viewport_top
        hardware_cursor_row = self._hardware_cursor_row

        def compute_line_diff(target_row: int) -> int:
            current_screen_row = hardware_cursor_row - prev_viewport_top
            target_screen_row = target_row - viewport_top
            return target_screen_row - current_screen_row

        # The root render() reports what it changed via _last_patch; consume it
        # here (it is per-frame state).
        new_lines = self.render(width)
        patch = self._last_patch
        self._last_patch = "full"

        if self._overlay_stack:
            new_lines = self._composite_overlays(new_lines, width, height)

        # Extract the cursor position before the marker can be obscured. The
        # reset is applied per line at write time (see _emit_line), so new_lines
        # stays the un-reset, identity-stable output of the component tree.
        # On patched frames the persistent flat buffer already had the marker
        # stripped; the cached position (row-shifted by render()) stays valid
        # unless its row was overwritten, or a marker may have newly appeared.
        cursor_pos: tuple[int, int] | None
        if patch != "full" and self._has_cursor_pos:
            cp = self._last_cursor_pos
            if cp is not None and not self._cursor_row_overwritten:
                cursor_pos = cp
            elif patch is None:
                cursor_pos = cp
            else:
                cursor_pos = self._extract_cursor_position(new_lines, height)
        else:
            cursor_pos = self._extract_cursor_position(new_lines, height)
        self._last_cursor_pos = cursor_pos
        self._has_cursor_pos = True

        def full_render(clear: bool) -> None:
            self._full_redraw_count += 1
            buffer = "\x1b[?2026h"  # begin synchronized output
            if clear:
                buffer += self._delete_kitty_images(self._previous_kitty_image_ids)
                buffer += "\x1b[2J\x1b[H\x1b[3J"  # clear screen, home, clear scrollback
            for i, line in enumerate(new_lines):
                if i > 0:
                    buffer += "\r\n"
                buffer += self._emit_line(line)
            buffer += "\x1b[?2026l"  # end synchronized output
            self.terminal.write(buffer)
            self._cursor_row = max(0, len(new_lines) - 1)
            self._hardware_cursor_row = self._cursor_row
            if clear:
                self._max_lines_rendered = len(new_lines)
            else:
                self._max_lines_rendered = max(self._max_lines_rendered, len(new_lines))
            buffer_length = max(height, len(new_lines))
            self._previous_viewport_top = max(0, buffer_length - height)
            self._position_hardware_cursor(cursor_pos, len(new_lines))
            self._previous_lines = new_lines
            self._previous_kitty_image_ids = self._collect_kitty_image_ids(new_lines)
            self._previous_width = width
            self._previous_height = height

        debug_redraw = os.environ.get("HOOCODE_DEBUG_REDRAW") == "1"

        def log_redraw(reason: str) -> None:
            if not debug_redraw:
                return
            log_path = _agent_dir() / "hoocode-debug.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            message = (
                f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] fullRender: {reason} "
                f"(prev={len(self._previous_lines)}, new={len(new_lines)}, height={height})\n"
            )
            with log_path.open("a") as handle:
                handle.write(message)

        # First render — output everything without clearing (clean screen).
        if not self._previous_lines and not width_changed and not height_changed:
            log_redraw("first render")
            full_render(False)
            return

        # Width changes always need a full re-render because wrapping changes.
        if width_changed:
            log_redraw(f"terminal width changed ({self._previous_width} -> {width})")
            full_render(True)
            return

        # Height changes normally need a full re-render to keep the viewport
        # aligned, but Termux changes height whenever the software keyboard
        # toggles, which would replay the entire history each time.
        if height_changed and not _is_termux_session():
            log_redraw(f"terminal height changed ({self._previous_height} -> {height})")
            full_render(True)
            return

        # Content shrank below the working area and no overlays are up.
        if (
            self._clear_on_shrink
            and len(new_lines) < self._max_lines_rendered
            and not self._overlay_stack
        ):
            log_redraw(f"clearOnShrink (maxLinesRendered={self._max_lines_rendered})")
            full_render(True)
            return

        # Find the first and last changed lines. When render() produced a patch
        # the dirty range is already known and the whole-buffer scan is skipped.
        # On patched frames previousLines is the same list object as new_lines,
        # so the previous length must come from the report.
        if patch != "full":
            if patch is None:
                prev_line_count = len(new_lines)
                first_changed = -1
                last_changed = -1
            else:
                prev_line_count = patch.prev_length
                first_changed = patch.low
                last_changed = patch.high
        else:
            prev_line_count = len(self._previous_lines)
            first_changed = -1
            last_changed = -1
            max_lines = max(len(new_lines), prev_line_count)
            for i in range(max_lines):
                old_line = self._previous_lines[i] if i < prev_line_count else ""
                new_line = new_lines[i] if i < len(new_lines) else ""
                if old_line != new_line:
                    if first_changed == -1:
                        first_changed = i
                    last_changed = i

        appended_lines = len(new_lines) > prev_line_count
        if appended_lines:
            if first_changed == -1:
                first_changed = prev_line_count
            last_changed = len(new_lines) - 1
        if first_changed != -1:
            last_changed = self._expand_last_changed_for_kitty_images(first_changed, last_changed)
        append_start = appended_lines and first_changed == prev_line_count and first_changed > 0

        # Nothing changed — the hardware cursor may still have moved.
        if first_changed == -1:
            self._position_hardware_cursor(cursor_pos, len(new_lines))
            self._previous_viewport_top = prev_viewport_top
            self._previous_height = height
            return

        # All the changes are in deleted lines: nothing to draw, just clear.
        if first_changed >= len(new_lines):
            if prev_line_count > len(new_lines):
                buffer = "\x1b[?2026h"
                buffer += self._delete_changed_kitty_images(first_changed, last_changed)
                target_row = max(0, len(new_lines) - 1)
                if target_row < prev_viewport_top:
                    log_redraw(
                        f"deleted lines moved viewport up ({target_row} < {prev_viewport_top})"
                    )
                    full_render(True)
                    return
                line_diff = compute_line_diff(target_row)
                if line_diff > 0:
                    buffer += f"\x1b[{line_diff}B"
                elif line_diff < 0:
                    buffer += f"\x1b[{-line_diff}A"
                buffer += "\r"
                extra_lines = prev_line_count - len(new_lines)
                if extra_lines > height:
                    log_redraw(f"extraLines > height ({extra_lines} > {height})")
                    full_render(True)
                    return
                if extra_lines > 0:
                    buffer += "\x1b[1B"
                for i in range(extra_lines):
                    buffer += "\r\x1b[2K"
                    if i < extra_lines - 1:
                        buffer += "\x1b[1B"
                if extra_lines > 0:
                    buffer += f"\x1b[{extra_lines}A"
                buffer += "\x1b[?2026l"
                self.terminal.write(buffer)
                self._cursor_row = target_row
                self._hardware_cursor_row = target_row
            self._position_hardware_cursor(cursor_pos, len(new_lines))
            self._previous_lines = new_lines
            self._previous_kitty_image_ids = self._collect_kitty_image_ids(new_lines)
            self._previous_width = width
            self._previous_height = height
            self._previous_viewport_top = prev_viewport_top
            return

        # Differential rendering can only touch what was actually visible.
        if first_changed < prev_viewport_top:
            log_redraw(f"firstChanged < viewportTop ({first_changed} < {prev_viewport_top})")
            full_render(True)
            return

        buffer = "\x1b[?2026h"  # begin synchronized output
        buffer += self._delete_changed_kitty_images(first_changed, last_changed)
        prev_viewport_bottom = prev_viewport_top + height - 1
        move_target_row = first_changed - 1 if append_start else first_changed
        if move_target_row > prev_viewport_bottom:
            current_screen_row = max(0, min(height - 1, hardware_cursor_row - prev_viewport_top))
            move_to_bottom = height - 1 - current_screen_row
            if move_to_bottom > 0:
                buffer += f"\x1b[{move_to_bottom}B"
            scroll = move_target_row - prev_viewport_bottom
            buffer += "\r\n" * scroll
            prev_viewport_top += scroll
            viewport_top += scroll
            hardware_cursor_row = move_target_row

        line_diff = compute_line_diff(move_target_row)
        if line_diff > 0:
            buffer += f"\x1b[{line_diff}B"
        elif line_diff < 0:
            buffer += f"\x1b[{-line_diff}A"

        buffer += "\r\n" if append_start else "\r"

        # Only the changed lines, not everything to the end — one changed line
        # (a spinner frame) should not repaint the transcript.
        render_end = min(last_changed, len(new_lines) - 1)
        for i in range(first_changed, render_end + 1):
            if i > first_changed:
                buffer += "\r\n"
            buffer += "\x1b[2K"  # clear current line
            line = new_lines[i]
            is_image = is_image_line(line)
            if not is_image and visible_width(line) > width:
                self._crash_on_overlong_line(i, line, new_lines, width)
            if is_image:
                self._saw_image_line = True
            buffer += line if is_image else normalize_terminal_output(line) + SEGMENT_RESET

        final_cursor_row = render_end

        # More lines before than now: clear the leftovers and come back.
        if prev_line_count > len(new_lines):
            if render_end < len(new_lines) - 1:
                move_down = len(new_lines) - 1 - render_end
                buffer += f"\x1b[{move_down}B"
                final_cursor_row = len(new_lines) - 1
            extra_lines = prev_line_count - len(new_lines)
            for _ in range(len(new_lines), prev_line_count):
                buffer += "\r\n\x1b[2K"
            buffer += f"\x1b[{extra_lines}A"

        buffer += "\x1b[?2026l"  # end synchronized output

        if os.environ.get("HOOCODE_TUI_DEBUG") == "1":
            _write_frame_debug(
                first_changed=first_changed,
                viewport_top=viewport_top,
                cursor_row=self._cursor_row,
                height=height,
                line_diff=line_diff,
                hardware_cursor_row=hardware_cursor_row,
                render_end=render_end,
                final_cursor_row=final_cursor_row,
                cursor_pos=cursor_pos,
                new_lines=new_lines,
                previous_lines=self._previous_lines,
                buffer=buffer,
            )

        self.terminal.write(buffer)

        # cursorRow tracks the end of content (for viewport maths);
        # hardwareCursorRow tracks the real terminal cursor (for movement).
        self._cursor_row = max(0, len(new_lines) - 1)
        self._hardware_cursor_row = final_cursor_row
        self._max_lines_rendered = max(self._max_lines_rendered, len(new_lines))
        self._previous_viewport_top = max(prev_viewport_top, final_cursor_row - height + 1)

        self._position_hardware_cursor(cursor_pos, len(new_lines))

        self._previous_lines = new_lines
        self._previous_kitty_image_ids = self._collect_kitty_image_ids(new_lines)
        self._previous_width = width
        self._previous_height = height

    def _crash_on_overlong_line(
        self, index: int, line: str, new_lines: list[str], width: int
    ) -> None:
        """Dump every rendered line and abort: an over-wide line corrupts the screen."""
        crash_log_path = _agent_dir() / "hoocode-crash.log"
        crash_data = "\n".join(
            [
                f"Crash at {time.strftime('%Y-%m-%dT%H:%M:%S')}",
                f"Terminal width: {width}",
                f"Line {index} visible width: {visible_width(line)}",
                "",
                "=== All rendered lines ===",
                *[
                    f"[{i}] (w={visible_width(candidate)}) {candidate}"
                    for i, candidate in enumerate(new_lines)
                ],
                "",
            ]
        )
        crash_log_path.parent.mkdir(parents=True, exist_ok=True)
        crash_log_path.write_text(crash_data)

        # Restore the terminal before raising.
        self.stop()

        raise RuntimeError(
            "\n".join(
                [
                    f"Rendered line {index} exceeds terminal width "
                    f"({visible_width(line)} > {width}).",
                    "",
                    "This is likely caused by a custom TUI component not truncating its output.",
                    "Use visible_width() to measure and truncate_to_width() to truncate lines.",
                    "",
                    f"Debug log written to: {crash_log_path}",
                ]
            )
        )

    def _position_hardware_cursor(
        self, cursor_pos: tuple[int, int] | None, total_lines: int
    ) -> None:
        """Put the hardware cursor where the IME candidate window belongs."""
        if cursor_pos is None or total_lines <= 0:
            self.terminal.hide_cursor()
            return

        target_row = max(0, min(cursor_pos[0], total_lines - 1))
        target_col = max(0, cursor_pos[1])

        row_delta = target_row - self._hardware_cursor_row
        buffer = ""
        if row_delta > 0:
            buffer += f"\x1b[{row_delta}B"
        elif row_delta < 0:
            buffer += f"\x1b[{-row_delta}A"
        buffer += f"\x1b[{target_col + 1}G"  # absolute column, 1-indexed

        if buffer:
            self.terminal.write(buffer)

        self._hardware_cursor_row = target_row
        if self._show_hardware_cursor:
            self.terminal.show_cursor()
        else:
            self.terminal.hide_cursor()


def _agent_dir() -> Path:
    return Path(
        os.environ.get("HOOCODE_CODING_AGENT_DIR") or str(Path.home() / ".hoocode" / "agent")
    )


def _write_frame_debug(**fields: Any) -> None:
    debug_dir = Path("/tmp/tui")
    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_path = debug_dir / f"render-{int(time.time() * 1000)}-{os.getpid()}.log"
    new_lines = fields.pop("new_lines")
    previous_lines = fields.pop("previous_lines")
    buffer = fields.pop("buffer")
    debug_data = "\n".join(
        [
            *[f"{key}: {value}" for key, value in fields.items()],
            f"newLines.length: {len(new_lines)}",
            f"previousLines.length: {len(previous_lines)}",
            "",
            "=== newLines ===",
            json.dumps(new_lines, indent=2),
            "",
            "=== previousLines ===",
            json.dumps(previous_lines, indent=2),
            "",
            "=== buffer ===",
            json.dumps(buffer),
        ]
    )
    debug_path.write_text(debug_data)

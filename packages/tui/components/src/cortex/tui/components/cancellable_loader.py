"""Loader that can be cancelled with Escape.

Mechanical port of hoocode's ``packages/tui/src/components/cancellable-loader.ts``.

The TS uses the web ``AbortController``/``AbortSignal`` pair. Following the
convention set by the provider leaves, an abort signal here is simply an object
with a boolean ``aborted`` — that is all any consumer reads.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cortex.tui.components.loader import Loader, LoaderIndicatorOptions
from cortex.tui.keys import get_keybindings

__all__ = ["AbortController", "AbortSignal", "CancellableLoader"]


class AbortSignal:
    """Minimal stand-in for the web ``AbortSignal``: a readable ``aborted``.

    Read-only by construction — it reports its controller's state rather than
    holding its own, so handing the signal to a consumer does not hand over the
    ability to trip it.
    """

    def __init__(self, controller: AbortController) -> None:
        self._controller = controller

    @property
    def aborted(self) -> bool:
        return self._controller.aborted


class AbortController:
    """Owns an :class:`AbortSignal` and the right to trip it."""

    def __init__(self) -> None:
        self.aborted = False
        self.signal = AbortSignal(self)

    def abort(self) -> None:
        self.aborted = True


class CancellableLoader(Loader):
    """A :class:`Loader` that aborts on the ``tui.select.cancel`` keybinding.

    Example::

        loader = CancellableLoader(tui, cyan, dim, "Working...")
        loader.on_abort = lambda: done(None)
    """

    def __init__(
        self,
        ui: Any,
        spinner_color_fn: Callable[[str], str],
        message_color_fn: Callable[[str], str],
        message: str = "Loading...",
        indicator: LoaderIndicatorOptions | None = None,
    ) -> None:
        super().__init__(ui, spinner_color_fn, message_color_fn, message, indicator)
        self._abort_controller = AbortController()
        #: Called when the user cancels.
        self.on_abort: Callable[[], None] | None = None

    @property
    def signal(self) -> AbortSignal:
        """Signal that trips when the user cancels."""
        return self._abort_controller.signal

    @property
    def aborted(self) -> bool:
        return self._abort_controller.signal.aborted

    def handle_input(self, data: str) -> None:
        if get_keybindings().matches(data, "tui.select.cancel"):
            self._abort_controller.abort()
            if self.on_abort is not None:
                self.on_abort()

    def dispose(self) -> None:
        self.stop()

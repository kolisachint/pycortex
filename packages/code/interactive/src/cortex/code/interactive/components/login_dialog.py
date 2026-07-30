"""The dialog that stands in for the editor during a login.

Port of ``components/login-dialog.ts``. An OAuth flow is a conversation — here is
a URL, now paste the code, now wait — so the dialog is a *content area the flow
writes into* rather than a fixed screen: the provider's callbacks call
:meth:`show_auth`, :meth:`show_prompt`, :meth:`show_progress` in whatever order
that provider needs, and each appends.

**Only `show_auth` and `show_info` clear the content; the rest append.** That is
deliberate in the TS and easy to "fix" wrongly: the URL has to stay on screen
while the user is being asked for the code they got from visiting it.

Awaiting a keystroke from a callback is what
:class:`~asyncio.Future` is for here — :meth:`show_prompt` hands one back, the
``Input``'s submit handler resolves it, and Escape fails it with
``Login cancelled``, the sentinel string every caller checks for so that a
deliberate cancel is not reported as an error.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Callable

from cortex.ai.oauth import LOGIN_CANCELLED, get_oauth_providers
from cortex.code.interactive.components.dynamic_border import DynamicBorder
from cortex.code.interactive.components.keybinding_hints import key_hint
from cortex.code.interactive.theme import get_theme
from cortex.tui.components import AbortController, AbortSignal, Input, Spacer, Text
from cortex.tui.keys import get_keybindings
from cortex.tui.render import TUI, Container

__all__ = ["LOGIN_CANCELLED", "LoginDialogComponent"]

#: Re-exported from :mod:`cortex.ai.oauth` so this module's callers can keep
#: importing it from here. It is defined there because the providers raise it
#: too — see that constant's own note.


class LoginDialogComponent(Container):
    """Replaces the editor while a login is in progress."""

    def __init__(
        self,
        ui: TUI,
        provider_id: str,
        on_complete: Callable[[bool, str | None], None],
        provider_name_override: str | None = None,
        title_override: str | None = None,
    ) -> None:
        super().__init__()
        self._ui = ui
        self._on_complete = on_complete
        self._abort_controller = AbortController()
        self._input_future: asyncio.Future[str] | None = None
        self._focused = False

        provider_info = next((p for p in get_oauth_providers() if p.id == provider_id), None)
        provider_name = provider_name_override or (
            provider_info.name if provider_info is not None else provider_id
        )
        title = title_override if title_override is not None else f"Login to {provider_name}"

        theme = get_theme()

        self.add_child(DynamicBorder())
        self.add_child(Text(theme.fg("accent", theme.bold(title)), 1, 0))

        self._content_container = Container()
        self.add_child(self._content_container)

        # Present from the start and moved into the content area when a step
        # needs it, which is why it is not a child of this container.
        self._input = Input()
        self._input.on_submit = self._resolve_input
        self._input.on_escape = self.cancel

        self.add_child(DynamicBorder())

    # -- focus -------------------------------------------------------------

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        """Propagate focus to the input so the caret lands in it."""
        self._focused = value
        self._input.focused = value

    @property
    def signal(self) -> AbortSignal:
        """Tripped when the user cancels; the provider's flow watches it."""
        return self._abort_controller.signal

    # -- the pending keystroke ---------------------------------------------

    def _pending_future(self) -> asyncio.Future[str]:
        """A future for the next submitted line.

        Created against the running loop so that resolving it from a keystroke —
        which happens on that same loop — wakes the awaiting flow.
        """
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._input_future = future
        return future

    def _resolve_input(self, _value: str | None = None) -> None:
        future = self._input_future
        if future is None or future.done():
            return
        self._input_future = None
        future.set_result(self._input.get_value())

    def cancel(self) -> None:
        """Abort the flow and fail whatever it is waiting on."""
        self._abort_controller.abort()
        future = self._input_future
        if future is not None and not future.done():
            self._input_future = None
            future.set_exception(RuntimeError(LOGIN_CANCELLED))
        self._on_complete(False, LOGIN_CANCELLED)

    # -- the steps a flow can ask for --------------------------------------

    def show_auth(self, url: str, instructions: str | None = None) -> None:
        """The ``on_auth`` step: the URL to visit, and try to open it."""
        theme = get_theme()
        self._content_container.clear()
        self._content_container.add_child(Spacer(1))
        linked_url = f"\x1b]8;;{url}\x07{url}\x1b]8;;\x07"
        self._content_container.add_child(Text(theme.fg("accent", linked_url), 1, 0))

        click_hint = "Cmd+click to open" if sys.platform == "darwin" else "Ctrl+click to open"
        hyperlink = f"\x1b]8;;{url}\x07{click_hint}\x1b]8;;\x07"
        self._content_container.add_child(Text(theme.fg("dim", hyperlink), 1, 0))

        if instructions:
            self._content_container.add_child(Spacer(1))
            self._content_container.add_child(Text(theme.fg("warning", instructions), 1, 0))

        _open_in_browser(url)

        self._ui.request_render()

    def show_manual_input(self, prompt: str) -> asyncio.Future[str]:
        """The paste-the-redirect-URL box, for providers with a callback server."""
        theme = get_theme()
        self._content_container.add_child(Spacer(1))
        self._content_container.add_child(Text(theme.fg("dim", prompt), 1, 0))
        self._content_container.add_child(self._input)
        self._content_container.add_child(
            Text(f"({key_hint('tui.select.cancel', 'to cancel')})", 1, 0)
        )
        self._ui.request_render()
        return self._pending_future()

    def show_prompt(self, message: str, placeholder: str | None = None) -> asyncio.Future[str]:
        """The ``on_prompt`` step. Appends, so the URL above it stays visible."""
        theme = get_theme()
        self._content_container.add_child(Spacer(1))
        self._content_container.add_child(Text(theme.fg("text", message), 1, 0))
        if placeholder:
            self._content_container.add_child(Text(theme.fg("dim", f"e.g., {placeholder}"), 1, 0))
        self._content_container.add_child(self._input)
        self._content_container.add_child(
            Text(
                f"({key_hint('tui.select.cancel', 'to cancel,')} "
                f"{key_hint('tui.select.confirm', 'to submit')})",
                1,
                0,
            )
        )

        self._input.set_value("")
        self._ui.request_render()
        return self._pending_future()

    def show_info(self, lines: list[str]) -> None:
        """Text with nothing to answer — the Bedrock setup notice."""
        self._content_container.clear()
        self._content_container.add_child(Spacer(1))
        for line in lines:
            self._content_container.add_child(Text(line, 1, 0))
        self._content_container.add_child(Spacer(1))
        self._content_container.add_child(
            Text(f"({key_hint('tui.select.cancel', 'to close')})", 1, 0)
        )
        self._ui.request_render()

    def show_waiting(self, message: str) -> None:
        """For the polling flows (GitHub Copilot) where nothing is asked."""
        theme = get_theme()
        self._content_container.add_child(Spacer(1))
        self._content_container.add_child(Text(theme.fg("dim", message), 1, 0))
        self._content_container.add_child(
            Text(f"({key_hint('tui.select.cancel', 'to cancel')})", 1, 0)
        )
        self._ui.request_render()

    def show_progress(self, message: str) -> None:
        """The ``on_progress`` step: one more line, no spacer."""
        theme = get_theme()
        self._content_container.add_child(Text(theme.fg("dim", message), 1, 0))
        self._ui.request_render()

    # -- input -------------------------------------------------------------

    def handle_input(self, data: str) -> None:
        if get_keybindings().matches(data, "tui.select.cancel"):
            self.cancel()
            return
        self._input.handle_input(data)


def _open_in_browser(url: str) -> None:
    """Best-effort ``open``/``start``/``xdg-open``, like the TS's bare ``exec``.

    Failure is ignored on purpose: the URL is on screen and clickable, so a box
    with no browser (a container, a remote shell) is a working login, not a
    broken one.
    """
    if sys.platform == "darwin":
        command = ["open", url]
    elif sys.platform == "win32":
        command = ["cmd", "/c", "start", "", url]
    else:
        command = ["xdg-open", url]
    try:
        subprocess.Popen(  # noqa: S603 - a fixed opener over a provider's own URL
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        pass

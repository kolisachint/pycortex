"""A list of plain strings to pick from. Port of ``components/extension-selector.ts``.

The simplest overlay in the app, and the one the login flows lean on twice: once
to ask subscription-or-API-key, and once inside an OAuth flow when the provider
itself asks a multiple-choice question (``on_select``). Extensions use it for the
same reason — it needs nothing but labels.

``j``/``k`` work alongside the bound navigation keys, which is the TS's own
behaviour and worth not "cleaning up": this list is small enough to be driven
one-handed, and vim keys here never conflict with a search box because there is
none.

The countdown option is not ported. ``CountdownTimer`` is an extension-facing
component (a timeout is only ever passed by ``dialogs.showSelector``), and
nothing in this port constructs one; the parameter is absent rather than
accepted-and-ignored so a caller cannot believe it works.
"""

from __future__ import annotations

from collections.abc import Callable

from cortex.code.interactive.components.dynamic_border import DynamicBorder
from cortex.code.interactive.components.keybinding_hints import key_hint, raw_key_hint
from cortex.code.interactive.theme import get_theme
from cortex.tui.components import Spacer, Text
from cortex.tui.keys import get_keybindings
from cortex.tui.render import Container

__all__ = ["ExtensionSelectorComponent"]


class ExtensionSelectorComponent(Container):
    """A titled list of string options between two dynamic borders."""

    def __init__(
        self,
        title: str,
        options: list[str],
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__()

        self._options = list(options)
        self._selected_index = 0
        self._on_select = on_select
        self._on_cancel = on_cancel

        theme = get_theme()

        self.add_child(DynamicBorder())
        self.add_child(Spacer(1))

        self._title_text = Text(theme.fg("accent", theme.bold(title)), 1, 0)
        self.add_child(self._title_text)
        self.add_child(Spacer(1))

        self._list_container = Container()
        self.add_child(self._list_container)
        self.add_child(Spacer(1))
        self.add_child(
            Text(
                raw_key_hint("↑↓", "navigate")
                + "  "
                + key_hint("tui.select.confirm", "select")
                + "  "
                + key_hint("tui.select.cancel", "cancel"),
                1,
                0,
            )
        )
        self.add_child(Spacer(1))
        self.add_child(DynamicBorder())

        self._update_list()

    def _update_list(self) -> None:
        theme = get_theme()
        self._list_container.clear()
        for index, option in enumerate(self._options):
            if index == self._selected_index:
                text = theme.fg("accent", "→ ") + theme.fg("accent", option)
            else:
                text = f"  {theme.fg('text', option)}"
            self._list_container.add_child(Text(text, 1, 0))

    def handle_input(self, key_data: str) -> None:
        kb = get_keybindings()
        if kb.matches(key_data, "tui.select.up") or key_data == "k":
            self._selected_index = max(0, self._selected_index - 1)
            self._update_list()
        elif kb.matches(key_data, "tui.select.down") or key_data == "j":
            self._selected_index = min(len(self._options) - 1, self._selected_index + 1)
            self._update_list()
        elif kb.matches(key_data, "tui.select.confirm") or key_data == "\n":
            if 0 <= self._selected_index < len(self._options):
                self._on_select(self._options[self._selected_index])
        elif kb.matches(key_data, "tui.select.cancel"):
            self._on_cancel()

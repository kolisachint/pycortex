"""The theme picker overlay. Port of ``components/theme-selector.ts``.

A :class:`~cortex.tui.components.SelectList` between two dynamic borders, with
one extra wire: moving the selection *previews* the theme, so a palette is judged
on the screen it produces rather than on its name. Cancelling is what puts the
original back — the caller does that from ``on_cancel``, which is why this
component only reports.
"""

from __future__ import annotations

from collections.abc import Callable

from cortex.code.interactive.components.dynamic_border import DynamicBorder
from cortex.code.interactive.theme import get_available_themes, get_select_list_theme
from cortex.tui.components import SelectItem, SelectList, SelectListLayoutOptions
from cortex.tui.render import Container

__all__ = ["ThemeSelectorComponent"]

THEME_SELECT_LIST_LAYOUT = SelectListLayoutOptions(
    min_primary_column_width=12,
    max_primary_column_width=32,
)


class ThemeSelectorComponent(Container):
    """Renders a theme selector."""

    def __init__(
        self,
        current_theme: str,
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
        on_preview: Callable[[str], None],
    ) -> None:
        super().__init__()
        self._on_preview = on_preview

        themes = get_available_themes()
        theme_items = [
            SelectItem(
                value=name,
                label=name,
                description="(current)" if name == current_theme else None,
            )
            for name in themes
        ]

        self.add_child(DynamicBorder())

        self.select_list = SelectList(
            theme_items, 10, get_select_list_theme(), THEME_SELECT_LIST_LAYOUT
        )

        if current_theme in themes:
            self.select_list.set_selected_index(themes.index(current_theme))

        self.select_list.on_select = lambda item: on_select(item.value)
        self.select_list.on_cancel = on_cancel
        self.select_list.on_selection_change = lambda item: self._on_preview(item.value)

        self.add_child(self.select_list)

        self.add_child(DynamicBorder())

    def handle_input(self, data: str) -> None:
        self.select_list.handle_input(data)

    def get_select_list(self) -> SelectList:
        return self.select_list

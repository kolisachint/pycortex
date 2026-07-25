"""Settings list with value cycling, submenus and optional fuzzy search.

Mechanical port of hoocode's ``packages/tui/src/components/settings-list.ts``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cortex.tui.components.input import Input
from cortex.tui.fuzzy import fuzzy_filter
from cortex.tui.keys import get_keybindings
from cortex.tui.util import truncate_to_width, visible_width, wrap_text_with_ansi

__all__ = [
    "SettingItem",
    "SettingsList",
    "SettingsListOptions",
    "SettingsListTheme",
]


@dataclass
class SettingItem:
    #: Unique identifier for this setting.
    id: str
    #: Display label (left side).
    label: str
    #: Current value to display (right side).
    current_value: str
    #: Optional description shown when selected.
    description: str | None = None
    #: If provided, Enter/Space cycles through these values.
    values: list[str] | None = None
    #: If provided, Enter opens this submenu. Receives the current value and a
    #: `done` callback.
    submenu: Callable[[str, Callable[[str | None], None]], Any] | None = None


@dataclass
class SettingsListTheme:
    label: Callable[[str, bool], str]
    value: Callable[[str, bool], str]
    description: Callable[[str], str]
    cursor: str
    hint: Callable[[str], str]


@dataclass
class SettingsListOptions:
    enable_search: bool = False


class SettingsList:
    """Implements ``Component``."""

    def __init__(
        self,
        items: list[SettingItem],
        max_visible: int,
        theme: SettingsListTheme,
        on_change: Callable[[str, str], None],
        on_cancel: Callable[[], None],
        options: SettingsListOptions | None = None,
    ) -> None:
        self._items = items
        self._filtered_items = items
        self._max_visible = max_visible
        self._theme = theme
        self._on_change = on_change
        self._on_cancel = on_cancel
        self._selected_index = 0
        self._search_enabled = (options or SettingsListOptions()).enable_search
        self._search_input: Input | None = Input() if self._search_enabled else None

        # Submenu state
        self._submenu_component: Any | None = None
        self._submenu_item_index: int | None = None

    def update_value(self, item_id: str, new_value: str) -> None:
        """Update an item's ``current_value``."""
        for item in self._items:
            if item.id == item_id:
                item.current_value = new_value
                return

    def invalidate(self) -> None:
        if self._submenu_component is not None:
            invalidate = getattr(self._submenu_component, "invalidate", None)
            if invalidate is not None:
                invalidate()

    def render(self, width: int) -> list[str]:
        # An open submenu takes over the whole component.
        if self._submenu_component is not None:
            return self._submenu_component.render(width)
        return self._render_main_list(width)

    def _render_main_list(self, width: int) -> list[str]:
        lines: list[str] = []

        if self._search_enabled and self._search_input is not None:
            lines.extend(self._search_input.render(width))
            lines.append("")

        if not self._items:
            lines.append(self._theme.hint("  No settings available"))
            if self._search_enabled:
                self._add_hint_line(lines, width)
            return lines

        display_items = self._filtered_items if self._search_enabled else self._items
        if not display_items:
            lines.append(truncate_to_width(self._theme.hint("  No matching settings"), width))
            self._add_hint_line(lines, width)
            return lines

        start_index = max(
            0,
            min(
                self._selected_index - self._max_visible // 2,
                len(display_items) - self._max_visible,
            ),
        )
        end_index = min(start_index + self._max_visible, len(display_items))

        # Values align against the widest label, capped at 30 columns. Measured
        # over ALL items, not just the filtered ones, so the column does not
        # jump around as you type.
        max_label_width = min(30, max(visible_width(item.label) for item in self._items))

        for i in range(start_index, end_index):
            item = display_items[i]
            is_selected = i == self._selected_index
            prefix = self._theme.cursor if is_selected else "  "
            prefix_width = visible_width(prefix)

            label_padded = item.label + " " * max(0, max_label_width - visible_width(item.label))
            label_text = self._theme.label(label_padded, is_selected)

            separator = "  "
            used_width = prefix_width + max_label_width + visible_width(separator)
            value_max_width = width - used_width - 2

            value_text = self._theme.value(
                truncate_to_width(item.current_value, value_max_width, ""), is_selected
            )

            lines.append(truncate_to_width(prefix + label_text + separator + value_text, width))

        if start_index > 0 or end_index < len(display_items):
            scroll_text = f"  ({self._selected_index + 1}/{len(display_items)})"
            lines.append(self._theme.hint(truncate_to_width(scroll_text, width - 2, "")))

        selected_item = (
            display_items[self._selected_index]
            if 0 <= self._selected_index < len(display_items)
            else None
        )
        if selected_item is not None and selected_item.description:
            lines.append("")
            for line in wrap_text_with_ansi(selected_item.description, width - 4):
                lines.append(self._theme.description(f"  {line}"))

        self._add_hint_line(lines, width)

        return lines

    def handle_input(self, data: str) -> None:
        # An open submenu gets everything; its own cancel calls `done`, which
        # closes it.
        if self._submenu_component is not None:
            handle_input = getattr(self._submenu_component, "handle_input", None)
            if handle_input is not None:
                handle_input(data)
            return

        kb = get_keybindings()
        display_items = self._filtered_items if self._search_enabled else self._items
        if kb.matches(data, "tui.select.up"):
            if not display_items:
                return
            self._selected_index = (
                len(display_items) - 1 if self._selected_index == 0 else self._selected_index - 1
            )
        elif kb.matches(data, "tui.select.down"):
            if not display_items:
                return
            self._selected_index = (
                0 if self._selected_index == len(display_items) - 1 else self._selected_index + 1
            )
        elif kb.matches(data, "tui.select.confirm") or data == " ":
            self._activate_item()
        elif kb.matches(data, "tui.select.cancel"):
            self._on_cancel()
        elif self._search_enabled and self._search_input is not None:
            # Space is the activate key, so it never reaches the search box.
            sanitized = data.replace(" ", "")
            if not sanitized:
                return
            self._search_input.handle_input(sanitized)
            self._apply_filter(self._search_input.get_value())

    def _activate_item(self) -> None:
        source = self._filtered_items if self._search_enabled else self._items
        if not (0 <= self._selected_index < len(source)):
            return
        item = source[self._selected_index]

        if item.submenu is not None:
            # The submenu is handed the current value so it can pre-select.
            self._submenu_item_index = self._selected_index

            def done(selected_value: str | None = None) -> None:
                if selected_value is not None:
                    item.current_value = selected_value
                    self._on_change(item.id, selected_value)
                self._close_submenu()

            self._submenu_component = item.submenu(item.current_value, done)
        elif item.values:
            try:
                current_index = item.values.index(item.current_value)
            except ValueError:
                # Not in the list: `indexOf` returns -1 in the TS, so the next
                # index is 0 and cycling starts from the first value.
                current_index = -1
            next_index = (current_index + 1) % len(item.values)
            new_value = item.values[next_index]
            item.current_value = new_value
            self._on_change(item.id, new_value)

    def _close_submenu(self) -> None:
        self._submenu_component = None
        # Put the cursor back on the item that opened it.
        if self._submenu_item_index is not None:
            self._selected_index = self._submenu_item_index
            self._submenu_item_index = None

    def _apply_filter(self, query: str) -> None:
        self._filtered_items = fuzzy_filter(self._items, query, lambda item: item.label)
        self._selected_index = 0

    def _add_hint_line(self, lines: list[str], width: int) -> None:
        lines.append("")
        lines.append(
            truncate_to_width(
                self._theme.hint(
                    "  Type to search · Enter/Space to change · Esc to cancel"
                    if self._search_enabled
                    else "  Enter/Space to change · Esc to cancel"
                ),
                width,
            )
        )

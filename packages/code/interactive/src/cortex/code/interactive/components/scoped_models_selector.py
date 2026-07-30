"""The cycling-scope overlay. Port of ``components/scoped-models-selector.ts``.

What ``/scoped-models`` opens: the set of models Ctrl+P cycles through, and the
order it cycles them in. Changes take effect on the session immediately and reach
``settings.json`` only when explicitly saved, which is what the ``(unsaved)`` mark
in the footer line is for.

``enabled_ids`` carries the whole state and is deliberately three-valued:
``None`` means *no filter* — every model is in scope — and is not the same as
the empty list, which means one has been applied and nothing selected yet. The
first toggle is what turns the former into the latter.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from cortex.code.interactive.components.dynamic_border import DynamicBorder
from cortex.code.interactive.components.keybinding_hints import key_text
from cortex.code.interactive.theme import get_theme
from cortex.tui.components import Input, Spacer, Text
from cortex.tui.fuzzy import fuzzy_filter
from cortex.tui.keys import Key, get_keybindings, matches_key
from cortex.tui.render import Container

__all__ = ["ModelsCallbacks", "ModelsConfig", "ScopedModelsSelectorComponent"]

#: ``None`` = every model enabled (no filter); a list = an explicit, ordered set.
EnabledIds = list[str] | None


def _is_enabled(enabled_ids: EnabledIds, model_id: str) -> bool:
    return enabled_ids is None or model_id in enabled_ids


def _toggle(enabled_ids: EnabledIds, model_id: str) -> EnabledIds:
    if enabled_ids is None:
        return [model_id]  # First toggle: start with only this one.
    if model_id in enabled_ids:
        return [existing for existing in enabled_ids if existing != model_id]
    return [*enabled_ids, model_id]


def _enable_all(
    enabled_ids: EnabledIds, all_ids: list[str], target_ids: list[str] | None = None
) -> EnabledIds:
    if enabled_ids is None:
        return None  # Already all enabled.
    targets = target_ids if target_ids is not None else all_ids
    result = list(enabled_ids)
    for model_id in targets:
        if model_id not in result:
            result.append(model_id)
    return None if len(result) == len(all_ids) else result


def _clear_all(
    enabled_ids: EnabledIds, all_ids: list[str], target_ids: list[str] | None = None
) -> EnabledIds:
    if enabled_ids is None:
        return (
            [model_id for model_id in all_ids if model_id not in target_ids]
            if target_ids is not None
            else []
        )
    targets = set(target_ids if target_ids is not None else enabled_ids)
    return [model_id for model_id in enabled_ids if model_id not in targets]


def _move(enabled_ids: EnabledIds, model_id: str, delta: int) -> EnabledIds:
    if enabled_ids is None:
        return None
    result = list(enabled_ids)
    if model_id not in result:
        return result
    index = result.index(model_id)
    new_index = index + delta
    if new_index < 0 or new_index >= len(result):
        return result
    result[index], result[new_index] = result[new_index], result[index]
    return result


def _get_sorted_ids(enabled_ids: EnabledIds, all_ids: list[str]) -> list[str]:
    """Enabled models first, in their cycling order; the rest after."""
    if enabled_ids is None:
        return all_ids
    enabled_set = set(enabled_ids)
    return [*enabled_ids, *(model_id for model_id in all_ids if model_id not in enabled_set)]


@dataclass(frozen=True)
class _ModelItem:
    full_id: str
    model: Any
    enabled: bool


@dataclass
class ModelsConfig:
    all_models: list[Any]
    enabled_model_ids: list[str] | None


@dataclass
class ModelsCallbacks:
    #: The enabled set or its order changed (session-only, not persisted).
    on_change: Callable[[list[str] | None], Any]
    #: The user asked for the current selection to be written to settings.
    on_persist: Callable[[list[str] | None], Any]
    on_cancel: Callable[[], None]


class ScopedModelsSelectorComponent(Container):
    """Enable, disable and order the models Ctrl+P cycles. Implements ``Focusable``."""

    def __init__(self, config: ModelsConfig, callbacks: ModelsCallbacks) -> None:
        super().__init__()
        theme = get_theme()
        self._callbacks = callbacks
        self._models_by_id: dict[str, Any] = {}
        self._all_ids: list[str] = []
        self._selected_index = 0
        self._max_visible = 8
        self._is_dirty = False
        self._focused = False

        for model in config.all_models:
            full_id = f"{model.provider}/{model.id}"
            self._models_by_id[full_id] = model
            self._all_ids.append(full_id)

        self._enabled_ids: EnabledIds = (
            None if config.enabled_model_ids is None else list(config.enabled_model_ids)
        )
        self._filtered_items = self._build_items()

        self.add_child(DynamicBorder())
        self.add_child(Spacer(1))
        self.add_child(Text(theme.fg("accent", theme.bold("Model Configuration")), 0, 0))
        self.add_child(
            Text(
                theme.fg(
                    "muted", f"Session-only. {key_text('app.models.save')} to save to settings."
                ),
                0,
                0,
            )
        )
        self.add_child(Spacer(1))

        self.search_input = Input()
        self.add_child(self.search_input)
        self.add_child(Spacer(1))

        self.list_container = Container()
        self.add_child(self.list_container)

        self.add_child(Spacer(1))
        self._footer_text = Text(self._get_footer_text(), 0, 0)
        self.add_child(self._footer_text)

        self.add_child(DynamicBorder())
        self._update_list()

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        self._focused = value
        self.search_input.focused = value

    def _build_items(self) -> list[_ModelItem]:
        # Ids with no model behind them any more (a provider was logged out of)
        # are dropped rather than drawn as blank rows.
        return [
            _ModelItem(
                full_id=model_id,
                model=self._models_by_id[model_id],
                enabled=_is_enabled(self._enabled_ids, model_id),
            )
            for model_id in _get_sorted_ids(self._enabled_ids, self._all_ids)
            if model_id in self._models_by_id
        ]

    def _get_footer_text(self) -> str:
        theme = get_theme()
        enabled_count = (
            len(self._enabled_ids) if self._enabled_ids is not None else len(self._all_ids)
        )
        count_text = (
            "all enabled"
            if self._enabled_ids is None
            else f"{enabled_count}/{len(self._all_ids)} enabled"
        )
        parts = [
            f"{key_text('tui.select.confirm')} toggle",
            f"{key_text('app.models.enableAll')} all",
            f"{key_text('app.models.clearAll')} clear",
            f"{key_text('app.models.toggleProvider')} provider",
            f"{key_text('app.models.reorderUp')}/{key_text('app.models.reorderDown')} reorder",
            f"{key_text('app.models.save')} save",
            count_text,
        ]
        joined = " · ".join(parts)
        if self._is_dirty:
            return theme.fg("dim", f"  {joined} ") + theme.fg("warning", "(unsaved)")
        return theme.fg("dim", f"  {joined}")

    def _refresh(self) -> None:
        query = self.search_input.get_value()
        items = self._build_items()
        self._filtered_items = (
            fuzzy_filter(items, query, lambda item: f"{item.model.id} {item.model.provider}")
            if query
            else items
        )
        self._selected_index = min(self._selected_index, max(0, len(self._filtered_items) - 1))
        self._update_list()
        self._footer_text.set_text(self._get_footer_text())

    def _notify_change(self) -> None:
        self._callbacks.on_change(None if self._enabled_ids is None else list(self._enabled_ids))

    def _update_list(self) -> None:
        theme = get_theme()
        self.list_container.clear()

        if not self._filtered_items:
            self.list_container.add_child(Text(theme.fg("muted", "  No matching models"), 0, 0))
            return

        start_index = max(
            0,
            min(
                self._selected_index - self._max_visible // 2,
                len(self._filtered_items) - self._max_visible,
            ),
        )
        end_index = min(start_index + self._max_visible, len(self._filtered_items))
        all_enabled = self._enabled_ids is None

        for index in range(start_index, end_index):
            item = self._filtered_items[index]
            is_selected = index == self._selected_index
            prefix = theme.fg("accent", "→ ") if is_selected else "  "
            model_text = theme.fg("accent", item.model.id) if is_selected else item.model.id
            provider_badge = theme.fg("muted", f" [{item.model.provider}]")
            if all_enabled:
                status = ""
            else:
                status = theme.fg("success", " ✓") if item.enabled else theme.fg("dim", " ✗")
            self.list_container.add_child(
                Text(f"{prefix}{model_text}{provider_badge}{status}", 0, 0)
            )

        if start_index > 0 or end_index < len(self._filtered_items):
            self.list_container.add_child(
                Text(
                    theme.fg(
                        "muted", f"  ({self._selected_index + 1}/{len(self._filtered_items)})"
                    ),
                    0,
                    0,
                )
            )

        selected = self._filtered_items[self._selected_index]
        self.list_container.add_child(Spacer(1))
        self.list_container.add_child(
            Text(theme.fg("muted", f"  Model Name: {selected.model.name}"), 0, 0)
        )

    def _selected_item(self) -> _ModelItem | None:
        if self._selected_index < len(self._filtered_items):
            return self._filtered_items[self._selected_index]
        return None

    def _target_ids(self) -> list[str] | None:
        """The rows a bulk action applies to: the filtered ones, or everything."""
        if not self.search_input.get_value():
            return None
        return [item.full_id for item in self._filtered_items]

    def _apply(self, enabled_ids: EnabledIds) -> None:
        self._enabled_ids = enabled_ids
        self._is_dirty = True
        self._refresh()
        self._notify_change()

    def handle_input(self, data: str) -> None:  # noqa: C901 - 1:1 with the TS dispatch
        keybindings = get_keybindings()

        if keybindings.matches(data, "tui.select.up"):
            if not self._filtered_items:
                return
            self._selected_index = (
                len(self._filtered_items) - 1
                if self._selected_index == 0
                else self._selected_index - 1
            )
            self._update_list()
            return
        if keybindings.matches(data, "tui.select.down"):
            if not self._filtered_items:
                return
            self._selected_index = (
                0
                if self._selected_index == len(self._filtered_items) - 1
                else self._selected_index + 1
            )
            self._update_list()
            return

        reorder_up = keybindings.matches(data, "app.models.reorderUp")
        reorder_down = keybindings.matches(data, "app.models.reorderDown")
        if reorder_up or reorder_down:
            if self._enabled_ids is None:
                return
            item = self._selected_item()
            if item is not None and _is_enabled(self._enabled_ids, item.full_id):
                delta = -1 if reorder_up else 1
                new_index = self._enabled_ids.index(item.full_id) + delta
                # Only move within the enabled block: pushing a model past its
                # end would silently reorder something that is not on screen.
                if 0 <= new_index < len(self._enabled_ids):
                    self._selected_index += delta
                    self._apply(_move(self._enabled_ids, item.full_id, delta))
            return

        if keybindings.matches(data, "tui.select.confirm"):
            item = self._selected_item()
            if item is not None:
                self._apply(_toggle(self._enabled_ids, item.full_id))
            return

        if keybindings.matches(data, "app.models.enableAll"):
            self._apply(_enable_all(self._enabled_ids, self._all_ids, self._target_ids()))
            return

        if keybindings.matches(data, "app.models.clearAll"):
            self._apply(_clear_all(self._enabled_ids, self._all_ids, self._target_ids()))
            return

        if keybindings.matches(data, "app.models.toggleProvider"):
            item = self._selected_item()
            if item is not None:
                provider = item.model.provider
                provider_ids = [
                    model_id
                    for model_id in self._all_ids
                    if self._models_by_id[model_id].provider == provider
                ]
                all_on = all(_is_enabled(self._enabled_ids, i) for i in provider_ids)
                self._apply(
                    _clear_all(self._enabled_ids, self._all_ids, provider_ids)
                    if all_on
                    else _enable_all(self._enabled_ids, self._all_ids, provider_ids)
                )
            return

        if keybindings.matches(data, "app.models.save"):
            self._callbacks.on_persist(
                None if self._enabled_ids is None else list(self._enabled_ids)
            )
            self._is_dirty = False
            self._footer_text.set_text(self._get_footer_text())
            return

        # Ctrl+C clears the search box before it cancels the overlay.
        if matches_key(data, "ctrl+c"):
            if self.search_input.get_value():
                self.search_input.set_value("")
                self._refresh()
            else:
                self._callbacks.on_cancel()
            return

        if matches_key(data, Key.escape):
            self._callbacks.on_cancel()
            return

        self.search_input.handle_input(data)
        self._refresh()

    def get_search_input(self) -> Input:
        return self.search_input

    def get_enabled_ids(self) -> Sequence[str] | None:
        """The current selection, for a caller that needs it without a callback."""
        return None if self._enabled_ids is None else list(self._enabled_ids)

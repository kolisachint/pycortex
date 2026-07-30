"""The model picker overlay. Port of ``components/model-selector.ts``.

A search :class:`~cortex.tui.components.Input` over a hand-drawn list rather than
a :class:`~cortex.tui.components.SelectList`, because the rows are not plain text:
each carries a provider badge and a tick for the model in use, and the scope
header above them toggles between every available model and the ones
``/scoped-models`` narrowed to.

**Loading is asynchronous in the TS and synchronous here.** ``loadModels()`` is
awaited off the constructor there — the component paints empty and fills in when
the registry answers — which needs a running loop to schedule. This port takes
the models as a resolved list: the caller is
:class:`~cortex.code.interactive.model_controller.ModelController`, which is
already in async code when it opens the overlay, so awaiting one level up costs
nothing and removes a frame nobody wanted to see.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from cortex.ai.models import models_are_equal
from cortex.code.interactive.components.keybinding_hints import key_hint
from cortex.code.interactive.theme import get_theme
from cortex.tui.components import Input, Spacer, Text
from cortex.tui.fuzzy import fuzzy_filter
from cortex.tui.keys import get_keybindings
from cortex.tui.render import Container

__all__ = ["ModelItem", "ModelScope", "ModelSelectorComponent"]

#: Which set of models the list is showing.
ModelScope = Literal["all", "scoped"]

#: How many rows the list shows at once.
MAX_VISIBLE = 10


@dataclass(frozen=True)
class ModelItem:
    """One row: the model, with the two fields the row is keyed on."""

    provider: str
    id: str
    model: Any


class ModelSelectorComponent(Container):
    """A model selector with search. Implements ``Focusable``."""

    def __init__(
        self,
        current_model: Any,
        settings_manager: Any,
        available_models: list[Any],
        scoped_models: list[Any],
        on_select: Callable[[Any], None],
        on_cancel: Callable[[], None],
        initial_search_input: str | None = None,
        error_message: str | None = None,
    ) -> None:
        super().__init__()

        theme = get_theme()
        self._current_model = current_model
        self._settings_manager = settings_manager
        self._on_select = on_select
        self._on_cancel = on_cancel
        self._error_message = error_message
        self._selected_index = 0
        self._scope: ModelScope = "scoped" if scoped_models else "all"
        self._scope_text: Text | None = None
        self._scope_hint_text: Text | None = None
        self._focused = False

        # Minimal chrome — no filled border, just a clean overlay.
        self.add_child(Spacer(1))

        if scoped_models:
            self._scope_text = Text(self._get_scope_text(), 0, 0)
            self.add_child(self._scope_text)
            self._scope_hint_text = Text(self._get_scope_hint_text(), 0, 0)
            self.add_child(self._scope_hint_text)
        else:
            hint_text = (
                "Only showing models from configured providers. Use /login to add providers."
            )
            self.add_child(Text(theme.fg("warning", hint_text), 0, 0))
        self.add_child(Spacer(1))

        self.search_input = Input()
        if initial_search_input:
            self.search_input.set_value(initial_search_input)
        self.search_input.on_submit = self._submit_search
        self.add_child(self.search_input)

        self.add_child(Spacer(1))

        self.list_container = Container()
        self.add_child(self.list_container)

        self.add_child(Spacer(1))

        self._all_models = self._sort_models(
            [ModelItem(model.provider, model.id, model) for model in available_models]
        )
        self._scoped_model_items = [
            ModelItem(scoped.model.provider, scoped.model.id, scoped.model)
            for scoped in scoped_models
        ]
        self._active_models = (
            self._scoped_model_items if self._scope == "scoped" else self._all_models
        )
        self._filtered_models = self._active_models
        self._selected_index = self._index_of_current(self._filtered_models)

        if initial_search_input:
            self._filter_models(initial_search_input)
        else:
            self._update_list()

    # ------------------------------------------------------------------
    # Focus — propagated to the search input so the caret lands in it
    # ------------------------------------------------------------------

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        self._focused = value
        self.search_input.focused = value

    # ------------------------------------------------------------------
    # The list
    # ------------------------------------------------------------------

    def _index_of_current(self, items: list[ModelItem]) -> int:
        for index, item in enumerate(items):
            if models_are_equal(self._current_model, item.model):
                return index
        return min(self._selected_index, max(0, len(items) - 1))

    def _sort_models(self, models: list[ModelItem]) -> list[ModelItem]:
        """Current model first, then by provider."""
        return sorted(
            models,
            key=lambda item: (
                not models_are_equal(self._current_model, item.model),
                item.provider,
            ),
        )

    def _get_scope_text(self) -> str:
        theme = get_theme()
        all_text = theme.fg("accent" if self._scope == "all" else "muted", "all")
        scoped_text = theme.fg("accent" if self._scope == "scoped" else "muted", "scoped")
        return f"{theme.fg('muted', 'Scope: ')}{all_text}{theme.fg('muted', ' | ')}{scoped_text}"

    def _get_scope_hint_text(self) -> str:
        return key_hint("tui.input.tab", "scope") + get_theme().fg("muted", " (all/scoped)")

    def _set_scope(self, scope: ModelScope) -> None:
        if self._scope == scope:
            return
        self._scope = scope
        self._active_models = (
            self._scoped_model_items if self._scope == "scoped" else self._all_models
        )
        current_index = next(
            (
                index
                for index, item in enumerate(self._active_models)
                if models_are_equal(self._current_model, item.model)
            ),
            -1,
        )
        self._selected_index = current_index if current_index >= 0 else 0
        self._filter_models(self.search_input.get_value())
        if self._scope_text is not None:
            self._scope_text.set_text(self._get_scope_text())

    def _filter_models(self, query: str) -> None:
        self._filtered_models = (
            fuzzy_filter(
                self._active_models,
                query,
                lambda item: (
                    f"{item.id} {item.provider} {item.provider}/{item.id} {item.provider} {item.id}"
                ),
            )
            if query
            else self._active_models
        )
        self._selected_index = min(self._selected_index, max(0, len(self._filtered_models) - 1))
        self._update_list()

    def _update_list(self) -> None:
        theme = get_theme()
        self.list_container.clear()

        start_index = max(
            0,
            min(
                self._selected_index - MAX_VISIBLE // 2,
                len(self._filtered_models) - MAX_VISIBLE,
            ),
        )
        end_index = min(start_index + MAX_VISIBLE, len(self._filtered_models))

        for index in range(start_index, end_index):
            item = self._filtered_models[index]
            is_selected = index == self._selected_index
            is_current = models_are_equal(self._current_model, item.model)

            provider_badge = theme.fg("muted", f"[{item.provider}]")
            checkmark = theme.fg("success", " ✓") if is_current else ""
            if is_selected:
                prefix = theme.fg("accent", "→ ")
                line = f"{prefix + theme.fg('accent', item.id)} {provider_badge}{checkmark}"
            else:
                line = f"  {item.id} {provider_badge}{checkmark}"

            self.list_container.add_child(Text(line, 0, 0))

        if start_index > 0 or end_index < len(self._filtered_models):
            scroll_info = theme.fg(
                "muted", f"  ({self._selected_index + 1}/{len(self._filtered_models)})"
            )
            self.list_container.add_child(Text(scroll_info, 0, 0))

        if self._error_message:
            for line in self._error_message.split("\n"):
                self.list_container.add_child(Text(theme.fg("error", line), 0, 0))
        elif not self._filtered_models:
            self.list_container.add_child(Text(theme.fg("muted", "  No matching models"), 0, 0))
        else:
            selected = self._filtered_models[self._selected_index]
            self.list_container.add_child(Spacer(1))
            self.list_container.add_child(
                Text(theme.fg("muted", f"  Model Name: {selected.model.name}"), 0, 0)
            )

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def _submit_search(self, _value: str) -> None:
        """Enter in the search box takes the row the cursor is on."""
        if self._selected_index < len(self._filtered_models):
            self._handle_select(self._filtered_models[self._selected_index].model)

    def handle_input(self, key_data: str) -> None:
        keybindings = get_keybindings()

        if keybindings.matches(key_data, "tui.input.tab"):
            if self._scoped_model_items:
                self._set_scope("scoped" if self._scope == "all" else "all")
                if self._scope_hint_text is not None:
                    self._scope_hint_text.set_text(self._get_scope_hint_text())
            return

        if keybindings.matches(key_data, "tui.select.up"):
            if not self._filtered_models:
                return
            self._selected_index = (
                len(self._filtered_models) - 1
                if self._selected_index == 0
                else self._selected_index - 1
            )
            self._update_list()
        elif keybindings.matches(key_data, "tui.select.down"):
            if not self._filtered_models:
                return
            self._selected_index = (
                0
                if self._selected_index == len(self._filtered_models) - 1
                else self._selected_index + 1
            )
            self._update_list()
        elif keybindings.matches(key_data, "tui.select.confirm"):
            if self._selected_index < len(self._filtered_models):
                self._handle_select(self._filtered_models[self._selected_index].model)
        elif keybindings.matches(key_data, "tui.select.cancel"):
            self._on_cancel()
        else:
            self.search_input.handle_input(key_data)
            self._filter_models(self.search_input.get_value())

    def _handle_select(self, model: Any) -> None:
        # Saved as the new default before the callback runs, as in the TS: the
        # callback can fail (no key for the model) and the TS still remembers
        # what was asked for.
        self._settings_manager.set_default_model_and_provider(model.provider, model.id)
        self._on_select(model)

    def get_search_input(self) -> Input:
        return self.search_input

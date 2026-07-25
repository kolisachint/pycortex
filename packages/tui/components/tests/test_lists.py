# pyright: reportPrivateUsage=false
# Selection index and filtered-item state have no public surface; asserting on
# them is how the navigation semantics get pinned.
"""List component behaviour a rendered frame cannot show.

The frames themselves — column alignment, truncation, scroll indicators,
themed spans — are pinned by the 33 `component/select-*` and
`component/settings-*` scenarios in `packages/tui/testkit`, which diff whole
screens against the real TypeScript.

The first five tests here are ported from hoocode's `select-list.test.ts`; the
rest cover callbacks, navigation and submenu state that no frame reveals.
"""

from __future__ import annotations

from typing import Any

import pytest
from cortex.tui.components import (
    SelectItem,
    SelectList,
    SelectListLayoutOptions,
    SelectListTheme,
    SelectListTruncatePrimaryContext,
    SettingItem,
    SettingsList,
    SettingsListOptions,
    SettingsListTheme,
)
from cortex.tui.util import visible_width

UP = "\x1b[A"
DOWN = "\x1b[B"
ENTER = "\r"
ESCAPE = "\x1b"
SPACE = " "


def identity(text: str) -> str:
    return text


TEST_THEME = SelectListTheme(
    selected_prefix=identity,
    selected_text=identity,
    description=identity,
    scroll_info=identity,
    no_match=identity,
)


def visible_index_of(line: str, text: str) -> int:
    """Display column at which `text` starts — ANSI runs do not count."""
    index = line.index(text)
    return visible_width(line[:index])


def settings_theme() -> SettingsListTheme:
    return SettingsListTheme(
        label=lambda text, _selected: text,
        value=lambda text, _selected: text,
        description=identity,
        cursor="> ",
        hint=identity,
    )


class TestSelectListLayout:
    """Ported from `select-list.test.ts`."""

    def test_normalizes_multiline_descriptions_to_a_single_line(self) -> None:
        items = [SelectItem("a", "a", "line one\nline two\r\nline three")]
        rendered = SelectList(items, 5, TEST_THEME).render(80)
        assert "line one line two line three" in rendered[0]
        assert "\n" not in rendered[0]

    def test_descriptions_stay_aligned_when_the_primary_is_truncated(self) -> None:
        items = [
            SelectItem("short", "short", "short description"),
            SelectItem(
                "very-long-command-name-that-needs-truncation",
                "very-long-command-name-that-needs-truncation",
                "long description",
            ),
        ]
        rendered = SelectList(items, 5, TEST_THEME).render(80)
        assert visible_index_of(rendered[0], "short description") == visible_index_of(
            rendered[1], "long description"
        )

    def test_uses_the_configured_minimum_primary_column_width(self) -> None:
        items = [SelectItem("a", "a", "first"), SelectItem("bb", "bb", "second")]
        layout = SelectListLayoutOptions(min_primary_column_width=12, max_primary_column_width=20)
        rendered = SelectList(items, 5, TEST_THEME, layout).render(80)
        assert rendered[0].index("first") == 14
        assert rendered[1].index("second") == 14

    def test_uses_the_configured_maximum_primary_column_width(self) -> None:
        items = [
            SelectItem(
                "very-long-command-name-that-needs-truncation",
                "very-long-command-name-that-needs-truncation",
                "first",
            ),
            SelectItem("short", "short", "second"),
        ]
        layout = SelectListLayoutOptions(min_primary_column_width=12, max_primary_column_width=20)
        rendered = SelectList(items, 5, TEST_THEME, layout).render(80)
        assert visible_index_of(rendered[0], "first") == 22
        assert visible_index_of(rendered[1], "second") == 22

    def test_custom_truncation_still_keeps_descriptions_aligned(self) -> None:
        items = [
            SelectItem(
                "very-long-command-name-that-needs-truncation",
                "very-long-command-name-that-needs-truncation",
                "first",
            ),
            SelectItem("short", "short", "second"),
        ]

        def truncate(context: SelectListTruncatePrimaryContext) -> str:
            if len(context.text) <= context.max_width:
                return context.text
            return context.text[: max(0, context.max_width - 1)] + "…"

        layout = SelectListLayoutOptions(
            min_primary_column_width=12, max_primary_column_width=12, truncate_primary=truncate
        )
        rendered = SelectList(items, 5, TEST_THEME, layout).render(80)
        assert "…" in rendered[0]
        assert visible_index_of(rendered[0], "first") == visible_index_of(rendered[1], "second")

    def test_a_bound_given_alone_pins_the_column(self) -> None:
        # Either bound stands in for the missing one, so setting just `max`
        # fixes the column rather than leaving `min` at the 32 default.
        items = [SelectItem("a", "a", "d")]
        layout = SelectListLayoutOptions(max_primary_column_width=10)
        list_ = SelectList(items, 5, TEST_THEME, layout)
        assert list_._get_primary_column_bounds().min == 10

    def test_inverted_bounds_are_normalised(self) -> None:
        layout = SelectListLayoutOptions(min_primary_column_width=30, max_primary_column_width=10)
        bounds = SelectList([], 5, TEST_THEME, layout)._get_primary_column_bounds()
        assert (bounds.min, bounds.max) == (10, 30)

    def test_the_label_falls_back_to_the_value(self) -> None:
        rendered = SelectList([SelectItem("raw", "")], 5, TEST_THEME).render(60)
        assert "raw" in rendered[0]


class TestSelectListNavigation:
    def make(self, count: int = 3, max_visible: int = 5) -> SelectList:
        items = [SelectItem(f"v{i}", f"Label {i}") for i in range(count)]
        return SelectList(items, max_visible, TEST_THEME)

    def test_down_advances_and_up_reverses(self) -> None:
        list_ = self.make()
        list_.handle_input(DOWN)
        assert list_._selected_index == 1
        list_.handle_input(UP)
        assert list_._selected_index == 0

    def test_up_from_the_top_wraps_to_the_bottom(self) -> None:
        list_ = self.make()
        list_.handle_input(UP)
        assert list_._selected_index == 2

    def test_down_from_the_bottom_wraps_to_the_top(self) -> None:
        list_ = self.make()
        list_.set_selected_index(2)
        list_.handle_input(DOWN)
        assert list_._selected_index == 0

    def test_selection_change_fires_on_movement_only(self) -> None:
        list_ = self.make()
        seen: list[str] = []
        list_.on_selection_change = lambda item: seen.append(item.value)
        list_.handle_input(DOWN)
        list_.handle_input(ENTER)
        assert seen == ["v1"], "confirm must not report a selection change"

    def test_confirm_reports_the_selected_item(self) -> None:
        list_ = self.make()
        chosen: list[str] = []
        list_.on_select = lambda item: chosen.append(item.value)
        list_.handle_input(DOWN)
        list_.handle_input(ENTER)
        assert chosen == ["v1"]

    def test_cancel_fires_on_escape(self) -> None:
        list_ = self.make()
        calls: list[int] = []
        list_.on_cancel = lambda: calls.append(1)
        list_.handle_input(ESCAPE)
        assert calls == [1]

    def test_callbacks_are_optional(self) -> None:
        list_ = self.make()
        list_.handle_input(ENTER)
        list_.handle_input(ESCAPE)  # must not raise

    def test_set_selected_index_clamps(self) -> None:
        list_ = self.make()
        list_.set_selected_index(99)
        assert list_._selected_index == 2
        list_.set_selected_index(-5)
        assert list_._selected_index == 0

    def test_get_selected_item(self) -> None:
        list_ = self.make()
        list_.set_selected_index(1)
        selected = list_.get_selected_item()
        assert selected is not None and selected.value == "v1"

    def test_get_selected_item_is_none_when_empty(self) -> None:
        assert SelectList([], 5, TEST_THEME).get_selected_item() is None


class TestSelectListFilter:
    def test_filter_matches_a_case_insensitive_value_prefix(self) -> None:
        items = [
            SelectItem("Apple", "Apple"),
            SelectItem("apricot", "apricot"),
            SelectItem("b", "b"),
        ]
        list_ = SelectList(items, 5, TEST_THEME)
        list_.set_filter("AP")
        assert [i.value for i in list_._filtered_items] == ["Apple", "apricot"]

    def test_filter_is_a_prefix_match_not_a_substring_one(self) -> None:
        items = [SelectItem("banana", "banana")]
        list_ = SelectList(items, 5, TEST_THEME)
        list_.set_filter("nan")
        assert list_._filtered_items == []

    def test_filter_resets_the_selection(self) -> None:
        items = [SelectItem("aa", "aa"), SelectItem("ab", "ab")]
        list_ = SelectList(items, 5, TEST_THEME)
        list_.set_selected_index(1)
        list_.set_filter("a")
        assert list_._selected_index == 0


class TestSettingsList:
    def make(
        self, items: list[SettingItem], **kwargs: Any
    ) -> tuple[SettingsList, list[tuple[str, str]], list[int]]:
        changes: list[tuple[str, str]] = []
        cancels: list[int] = []
        list_ = SettingsList(
            items,
            kwargs.get("max_visible", 5),
            settings_theme(),
            lambda item_id, value: changes.append((item_id, value)),
            lambda: cancels.append(1),
            SettingsListOptions(enable_search=kwargs.get("enable_search", False)),
        )
        return list_, changes, cancels

    def test_enter_cycles_through_values(self) -> None:
        items = [SettingItem("t", "Theme", "dark", values=["dark", "light", "auto"])]
        list_, changes, _ = self.make(items)
        list_.handle_input(ENTER)
        assert items[0].current_value == "light"
        list_.handle_input(ENTER)
        assert items[0].current_value == "auto"
        list_.handle_input(ENTER)
        assert items[0].current_value == "dark", "cycles back round"
        assert changes == [("t", "light"), ("t", "auto"), ("t", "dark")]

    def test_space_also_cycles(self) -> None:
        items = [SettingItem("t", "Theme", "dark", values=["dark", "light"])]
        list_, changes, _ = self.make(items)
        list_.handle_input(SPACE)
        assert changes == [("t", "light")]

    def test_an_unknown_current_value_cycles_to_the_first(self) -> None:
        # `indexOf` returns -1 in the TS, so (-1 + 1) % n == 0.
        items = [SettingItem("t", "Theme", "sepia", values=["dark", "light"])]
        list_, changes, _ = self.make(items)
        list_.handle_input(ENTER)
        assert changes == [("t", "dark")]

    def test_an_item_without_values_does_nothing(self) -> None:
        items = [SettingItem("m", "Model", "opus")]
        list_, changes, _ = self.make(items)
        list_.handle_input(ENTER)
        assert changes == []

    def test_cancel_fires_on_escape(self) -> None:
        list_, _, cancels = self.make([SettingItem("a", "A", "1")])
        list_.handle_input(ESCAPE)
        assert cancels == [1]

    def test_navigation_wraps(self) -> None:
        items = [SettingItem(str(i), f"L{i}", "v") for i in range(3)]
        list_, _, _ = self.make(items)
        list_.handle_input(UP)
        assert list_._selected_index == 2
        list_.handle_input(DOWN)
        assert list_._selected_index == 0

    def test_navigation_on_an_empty_list_is_a_noop(self) -> None:
        list_, _, _ = self.make([])
        list_.handle_input(UP)
        list_.handle_input(DOWN)
        assert list_._selected_index == 0

    def test_update_value_finds_the_item_by_id(self) -> None:
        items = [SettingItem("a", "A", "1"), SettingItem("b", "B", "2")]
        list_, _, _ = self.make(items)
        list_.update_value("b", "9")
        assert items[1].current_value == "9"

    def test_update_value_with_an_unknown_id_is_a_noop(self) -> None:
        items = [SettingItem("a", "A", "1")]
        list_, _, _ = self.make(items)
        list_.update_value("nope", "9")
        assert items[0].current_value == "1"


class TestSettingsListSearch:
    def make_searchable(self) -> SettingsList:
        items = [
            SettingItem("theme", "Theme", "dark"),
            SettingItem("model", "Model", "opus"),
            SettingItem("verbose", "Verbose logging", "off"),
        ]
        return SettingsList(
            items,
            5,
            settings_theme(),
            lambda _id, _value: None,
            lambda: None,
            SettingsListOptions(enable_search=True),
        )

    def test_typing_filters_the_list(self) -> None:
        list_ = self.make_searchable()
        for char in "mod":
            list_.handle_input(char)
        assert [i.id for i in list_._filtered_items] == ["model"]

    def test_search_resets_the_selection(self) -> None:
        list_ = self.make_searchable()
        list_.handle_input(DOWN)
        list_.handle_input("v")
        assert list_._selected_index == 0

    def test_space_activates_rather_than_typing(self) -> None:
        # Space is the activate key, so it is stripped before reaching the box.
        list_ = self.make_searchable()
        list_.handle_input(SPACE)
        assert list_._search_input is not None
        assert list_._search_input.get_value() == ""

    def test_search_is_off_by_default(self) -> None:
        list_ = SettingsList(
            [SettingItem("a", "A", "1")],
            5,
            settings_theme(),
            lambda _id, _value: None,
            lambda: None,
        )
        assert list_._search_input is None
        list_.handle_input("x")  # must not raise


class TestSettingsListSubmenu:
    def make_with_submenu(self) -> tuple[SettingsList, dict[str, Any]]:
        state: dict[str, Any] = {"done": None, "opened_with": None}

        class Submenu:
            def render(self, width: int) -> list[str]:
                return ["SUBMENU"]

            def invalidate(self) -> None:
                state["invalidated"] = True

            def handle_input(self, data: str) -> None:
                state.setdefault("inputs", []).append(data)

        def open_submenu(current: str, done: Any) -> Submenu:
            state["opened_with"] = current
            state["done"] = done
            return Submenu()

        items = [
            SettingItem("a", "A", "1"),
            SettingItem("b", "B", "2", submenu=open_submenu),
        ]
        changes: list[tuple[str, str]] = []
        list_ = SettingsList(
            items,
            5,
            settings_theme(),
            lambda item_id, value: changes.append((item_id, value)),
            lambda: None,
        )
        state["items"] = items
        state["changes"] = changes
        return list_, state

    def test_enter_opens_the_submenu_with_the_current_value(self) -> None:
        list_, state = self.make_with_submenu()
        list_.handle_input(DOWN)
        list_.handle_input(ENTER)
        assert state["opened_with"] == "2"
        assert list_.render(40) == ["SUBMENU"], "the submenu takes over rendering"

    def test_input_is_delegated_to_an_open_submenu(self) -> None:
        list_, state = self.make_with_submenu()
        list_.handle_input(DOWN)
        list_.handle_input(ENTER)
        list_.handle_input("x")
        assert state["inputs"] == ["x"]

    def test_done_with_a_value_applies_it_and_closes(self) -> None:
        list_, state = self.make_with_submenu()
        list_.handle_input(DOWN)
        list_.handle_input(ENTER)
        state["done"]("chosen")
        assert state["items"][1].current_value == "chosen"
        assert state["changes"] == [("b", "chosen")]
        assert list_.render(40) != ["SUBMENU"]

    def test_done_without_a_value_just_closes(self) -> None:
        list_, state = self.make_with_submenu()
        list_.handle_input(DOWN)
        list_.handle_input(ENTER)
        state["done"](None)
        assert state["items"][1].current_value == "2"
        assert state["changes"] == []

    def test_closing_restores_the_selection_to_the_opening_item(self) -> None:
        list_, state = self.make_with_submenu()
        list_.handle_input(DOWN)
        list_.handle_input(ENTER)
        state["done"](None)
        assert list_._selected_index == 1

    def test_invalidate_reaches_the_submenu(self) -> None:
        list_, state = self.make_with_submenu()
        list_.handle_input(DOWN)
        list_.handle_input(ENTER)
        list_.invalidate()
        assert state["invalidated"] is True


class TestListRenderSafety:
    @pytest.mark.parametrize("width", [1, 5, 20, 41, 80])
    def test_select_list_never_returns_nothing(self, width: int) -> None:
        items = [SelectItem("a", "Alpha", "some description")]
        assert len(SelectList(items, 5, TEST_THEME).render(width)) >= 1

    @pytest.mark.parametrize("width", [1, 5, 20, 41, 80])
    def test_settings_list_never_returns_nothing(self, width: int) -> None:
        items = [SettingItem("a", "Alpha", "value")]
        list_ = SettingsList(items, 5, settings_theme(), lambda _i, _v: None, lambda: None)
        assert len(list_.render(width)) >= 1

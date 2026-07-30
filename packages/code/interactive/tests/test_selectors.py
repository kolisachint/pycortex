"""Tests for the overlay components (step 7.9).

These drive the components directly — construct, send keystrokes, read what came
back through the callbacks — because that is the layer where the interesting
behaviour lives. What the *app* does with them (open one over the editor, close
it on Escape, put the focus back) is the end-to-end corpus's job, in
`overlay/settings` and `overlay/escape-closes`.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from cortex.ai.types import Model
from cortex.code.config import SettingsManager, WarningSettings
from cortex.code.config.settings_storage import InMemorySettingsStorage
from cortex.code.interactive.components.model_selector import ModelSelectorComponent
from cortex.code.interactive.components.scoped_models_selector import (
    ModelsCallbacks,
    ModelsConfig,
    ScopedModelsSelectorComponent,
)
from cortex.code.interactive.components.settings_selector import (
    SettingsCallbacks,
    SettingsConfig,
    SettingsSelectorComponent,
    ToolGroupInfo,
    ToolToggleInfo,
)
from cortex.code.interactive.components.theme_selector import ThemeSelectorComponent
from cortex.code.interactive.keybindings import KeybindingsManager
from cortex.code.session import ScopedModel
from cortex.tui.keys import set_keybindings

DOWN = "\x1b[B"
UP = "\x1b[A"
ENTER = "\r"
ESCAPE = "\x1b"
TAB = "\t"


@pytest.fixture(autouse=True)
def _app_keybindings() -> None:  # pyright: ignore[reportUnusedFunction]
    """The selectors resolve keys through the process-wide manager, as the app does."""
    set_keybindings(KeybindingsManager())


def _model(provider: str, model_id: str, name: str | None = None) -> Model:
    return Model(
        id=model_id,
        name=name if name is not None else model_id,
        api="anthropic-messages",
        provider=provider,
        base_url="https://example.invalid",
        reasoning=False,
        input=["text"],
        cost={"input": 0.0, "output": 0.0},
        context_window=1000,
        max_tokens=100,
    )


ALPHA = _model("anthropic", "alpha")
BETA = _model("openai", "beta")
GAMMA = _model("openai", "gamma")


def _settings() -> SettingsManager:
    return SettingsManager.from_storage(InMemorySettingsStorage())


def _text(component: Any, width: int = 80) -> str:
    return "\n".join(component.render(width))


def _plain(component: Any, width: int = 80) -> list[str]:
    """The rendered lines with the colour stripped, for assertions about order."""
    return [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in component.render(width)]


# ---------------------------------------------------------------------------
# Model selector
# ---------------------------------------------------------------------------


class _ModelSelectorFixture:
    def __init__(self, **overrides: Any) -> None:
        self.selected: list[Any] = []
        self.cancels = 0
        self.settings = _settings()
        options: dict[str, Any] = {
            "current_model": ALPHA,
            "available_models": [ALPHA, BETA, GAMMA],
            "scoped_models": [],
        }
        options.update(overrides)
        self.component = ModelSelectorComponent(
            options["current_model"],
            self.settings,
            options["available_models"],
            options["scoped_models"],
            self.selected.append,
            self._cancel,
            options.get("initial_search_input"),
            options.get("error_message"),
        )

    def _cancel(self) -> None:
        self.cancels += 1


class TestModelSelector:
    def test_it_lists_every_model_with_its_provider(self) -> None:
        fixture = _ModelSelectorFixture()
        rendered = _text(fixture.component)
        for model in (ALPHA, BETA, GAMMA):
            assert model.id in rendered
            assert f"[{model.provider}]" in rendered

    def test_the_model_in_use_is_the_first_row(self) -> None:
        """Sorted to the top, not just pre-selected: the list is long, and the
        model you are on is the one you most often want to see."""
        fixture = _ModelSelectorFixture(current_model=GAMMA)
        rows = [line for line in _plain(fixture.component) if "[" in line]
        assert rows, "the list drew no model rows at all"
        assert "gamma" in rows[0], f"the model in use is not the first row: {rows!r}"

    def test_the_model_in_use_opens_selected(self) -> None:
        fixture = _ModelSelectorFixture(current_model=BETA)
        fixture.component.handle_input(ENTER)
        assert fixture.selected == [BETA]

    def test_down_then_enter_takes_the_next_model(self) -> None:
        fixture = _ModelSelectorFixture()
        fixture.component.handle_input(DOWN)
        fixture.component.handle_input(ENTER)
        assert [model.id for model in fixture.selected] == ["beta"]

    def test_up_from_the_top_wraps_to_the_bottom(self) -> None:
        fixture = _ModelSelectorFixture()
        fixture.component.handle_input(UP)
        fixture.component.handle_input(ENTER)
        assert [model.id for model in fixture.selected] == ["gamma"]

    def test_choosing_saves_the_model_as_the_default(self) -> None:
        fixture = _ModelSelectorFixture()
        fixture.component.handle_input(DOWN)
        fixture.component.handle_input(ENTER)
        assert fixture.settings.get_default_model() == "beta"
        assert fixture.settings.get_default_provider() == "openai"

    def test_typing_filters_the_list(self) -> None:
        fixture = _ModelSelectorFixture()
        for char in "gam":
            fixture.component.handle_input(char)
        rendered = _text(fixture.component)
        assert "gamma" in rendered
        assert "alpha" not in rendered

    def test_escape_cancels_without_choosing(self) -> None:
        fixture = _ModelSelectorFixture()
        fixture.component.handle_input(ESCAPE)
        assert (fixture.cancels, fixture.selected) == (1, [])

    def test_an_empty_registry_says_so_rather_than_drawing_nothing(self) -> None:
        fixture = _ModelSelectorFixture(available_models=[])
        assert "No matching models" in _text(fixture.component)

    def test_an_error_is_shown_in_place_of_the_list(self) -> None:
        fixture = _ModelSelectorFixture(available_models=[], error_message="models.json is broken")
        assert "models.json is broken" in _text(fixture.component)

    def test_with_no_scope_it_advises_how_to_add_providers(self) -> None:
        assert "/login" in _text(_ModelSelectorFixture().component)

    def test_a_scope_opens_on_the_scoped_models(self) -> None:
        fixture = _ModelSelectorFixture(scoped_models=[ScopedModel(GAMMA)])
        rendered = _text(fixture.component)
        assert "Scope: " in rendered
        assert "alpha" not in rendered, "the scoped view is showing unscoped models"

    def test_tab_switches_between_scoped_and_all(self) -> None:
        fixture = _ModelSelectorFixture(scoped_models=[ScopedModel(GAMMA)])
        fixture.component.handle_input(TAB)
        assert "alpha" in _text(fixture.component)

    def test_an_initial_search_opens_filtered(self) -> None:
        fixture = _ModelSelectorFixture(initial_search_input="beta")
        rendered = _text(fixture.component)
        assert "beta" in rendered
        assert "gamma" not in rendered


# ---------------------------------------------------------------------------
# Scoped models selector
# ---------------------------------------------------------------------------


class _ScopedFixture:
    def __init__(self, enabled: list[str] | None = None) -> None:
        self.changes: list[list[str] | None] = []
        self.persisted: list[list[str] | None] = []
        self.cancels = 0
        self.component = ScopedModelsSelectorComponent(
            ModelsConfig(all_models=[ALPHA, BETA, GAMMA], enabled_model_ids=enabled),
            ModelsCallbacks(
                on_change=self.changes.append,
                on_persist=self.persisted.append,
                on_cancel=self._cancel,
            ),
        )

    def _cancel(self) -> None:
        self.cancels += 1


class TestScopedModelsSelector:
    def test_it_opens_with_everything_enabled(self) -> None:
        assert "all enabled" in _text(_ScopedFixture().component)

    def test_the_first_toggle_narrows_to_just_that_model(self) -> None:
        """`None` means no filter at all, so the first toggle cannot be a
        removal — it starts an explicit set containing only what was toggled."""
        fixture = _ScopedFixture()
        fixture.component.handle_input(ENTER)
        assert fixture.changes == [["anthropic/alpha"]]

    def test_toggling_an_enabled_model_removes_it(self) -> None:
        fixture = _ScopedFixture(enabled=["anthropic/alpha", "openai/beta"])
        fixture.component.handle_input(ENTER)
        assert fixture.changes == [["openai/beta"]]

    def test_enable_all_clears_the_filter(self) -> None:
        fixture = _ScopedFixture(enabled=["anthropic/alpha"])
        fixture.component.handle_input("\x01")  # Ctrl+A — app.models.enableAll
        assert fixture.changes == [None], "enabling every model must mean 'no filter'"

    def test_clear_all_empties_the_set(self) -> None:
        fixture = _ScopedFixture(enabled=["anthropic/alpha", "openai/beta"])
        fixture.component.handle_input("\x18")  # Ctrl+X — app.models.clearAll
        assert fixture.changes == [[]]

    def test_toggling_a_provider_takes_all_of_its_models(self) -> None:
        """And enabling the last of them collapses back to "no filter": that is
        what `None` means, and an explicit list of everything would behave the
        same while reading as a filter in `settings.json`."""
        fixture = _ScopedFixture(enabled=["anthropic/alpha"])
        fixture.component.handle_input(DOWN)  # onto an openai model
        fixture.component.handle_input("\x10")  # Ctrl+P — app.models.toggleProvider
        assert fixture.changes == [None]

    def test_toggling_a_provider_off_removes_all_of_its_models(self) -> None:
        fixture = _ScopedFixture(enabled=["openai/beta", "openai/gamma", "anthropic/alpha"])
        fixture.component.handle_input("\x10")  # the cursor opens on openai/beta
        assert fixture.changes == [["anthropic/alpha"]]

    def test_changes_are_marked_unsaved_until_they_are_saved(self) -> None:
        fixture = _ScopedFixture()
        fixture.component.handle_input(ENTER)
        assert "(unsaved)" in _text(fixture.component)
        fixture.component.handle_input("\x13")  # Ctrl+S — app.models.save
        assert "(unsaved)" not in _text(fixture.component)
        assert fixture.persisted == [["anthropic/alpha"]]

    def test_reordering_moves_a_model_within_the_enabled_set(self) -> None:
        fixture = _ScopedFixture(enabled=["anthropic/alpha", "openai/beta"])
        fixture.component.handle_input(DOWN)  # onto openai/beta
        fixture.component.handle_input("\x1b[1;3A")  # Alt+Up — app.models.reorderUp
        assert fixture.changes == [["openai/beta", "anthropic/alpha"]]

    def test_escape_cancels(self) -> None:
        fixture = _ScopedFixture()
        fixture.component.handle_input(ESCAPE)
        assert fixture.cancels == 1

    def test_ctrl_c_clears_the_search_before_it_cancels(self) -> None:
        fixture = _ScopedFixture()
        fixture.component.handle_input("g")
        fixture.component.handle_input("\x03")
        assert fixture.cancels == 0, "the first Ctrl+C should have cleared the search"
        fixture.component.handle_input("\x03")
        assert fixture.cancels == 1


# ---------------------------------------------------------------------------
# Theme selector
# ---------------------------------------------------------------------------


class TestThemeSelector:
    def test_it_lists_the_built_in_palettes(self) -> None:
        rendered = _text(ThemeSelectorComponent("dark", lambda _n: None, lambda: None, _noop_str))
        assert "dark" in rendered
        assert "light" in rendered

    def test_the_active_palette_is_marked(self) -> None:
        rendered = _text(ThemeSelectorComponent("dark", lambda _n: None, lambda: None, _noop_str))
        assert "(current)" in rendered

    def test_moving_the_selection_previews(self) -> None:
        previews: list[str] = []
        component = ThemeSelectorComponent("dark", lambda _n: None, lambda: None, previews.append)
        component.handle_input(DOWN)
        assert previews, "moving the cursor did not preview a theme"

    def test_enter_selects(self) -> None:
        chosen: list[str] = []
        component = ThemeSelectorComponent("dark", chosen.append, lambda: None, _noop_str)
        component.handle_input(ENTER)
        assert chosen == ["dark"]

    def test_escape_cancels(self) -> None:
        cancels: list[bool] = []
        component = ThemeSelectorComponent(
            "dark", lambda _n: None, lambda: cancels.append(True), _noop_str
        )
        component.handle_input(ESCAPE)
        assert cancels == [True]


def _noop_str(_value: str) -> None:
    return None


# ---------------------------------------------------------------------------
# Settings selector
# ---------------------------------------------------------------------------


def _settings_config(**overrides: Any) -> SettingsConfig:
    base: dict[str, Any] = {
        "auto_compact": True,
        "tools": [ToolToggleInfo("read", True), ToolToggleInfo("bash", False)],
        "tool_groups": [ToolGroupInfo("web", "Web tools", "webfetch + websearch.", True)],
        "flags": [],
        "tool_output_display": "standard",
        "tool_output_max_bytes": 32 * 1024,
        "tool_output_max_lines": 800,
        "context_gc": False,
        "show_images": True,
        "image_width_cells": 80,
        "auto_resize_images": True,
        "block_images": False,
        "enable_skill_commands": True,
        "steering_mode": "all",
        "follow_up_mode": "all",
        "transport": "auto",
        "thinking_level": "off",
        "available_thinking_levels": ["off", "low", "high"],
        "current_theme": "dark",
        "available_themes": ["dark", "light"],
        "hide_thinking_block": False,
        "collapse_changelog": False,
        "enable_install_telemetry": False,
        "double_escape_action": "tree",
        "tree_filter_mode": "default",
        "show_hardware_cursor": False,
        "editor_padding_x": 1,
        "autocomplete_max_visible": 10,
        "quiet_startup": False,
        "clear_on_shrink": False,
        "show_terminal_progress": True,
        "warnings": WarningSettings(),
        "voice_silence_ms": 800,
        "webtools_timeout_secs": 30,
    }
    base.update(overrides)
    return SettingsConfig(**base)


class TestSettingsSelector:
    def test_the_top_level_is_categories_rather_than_thirty_rows(self) -> None:
        component = SettingsSelectorComponent(_settings_config(), SettingsCallbacks())
        rendered = _text(component)
        for label in ("Auto-compact", "Tools", "Behavior", "Interface", "Images", "Advanced"):
            assert label in rendered
        # A leaf that lives inside a category is not on the top level.
        assert "Steering mode" not in rendered

    def test_a_leaf_row_cycles_its_values(self) -> None:
        changed: list[str] = []
        component = SettingsSelectorComponent(
            _settings_config(),
            SettingsCallbacks(on_tool_output_display_change=changed.append),
        )
        component.handle_input(DOWN)
        component.handle_input(DOWN)  # onto "Tool output display"
        component.handle_input(ENTER)
        assert changed == ["collapsed"]

    def test_the_tools_row_counts_what_is_on_and_off(self) -> None:
        component = SettingsSelectorComponent(_settings_config(), SettingsCallbacks())
        assert "1 on · 1 off" in _text(component)

    def test_the_flags_row_is_absent_when_there_are_none(self) -> None:
        component = SettingsSelectorComponent(_settings_config(), SettingsCallbacks())
        assert "Flags" not in _text(component)

    def test_the_flags_row_appears_when_an_extension_registered_one(self) -> None:
        from cortex.code.interactive.components.settings_selector import FlagInfo

        component = SettingsSelectorComponent(
            _settings_config(flags=[FlagInfo("verbose", "boolean", True)]),
            SettingsCallbacks(),
        )
        assert "Flags" in _text(component)

    def test_a_category_opens_onto_its_leaves(self) -> None:
        component = SettingsSelectorComponent(_settings_config(), SettingsCallbacks())
        for _ in range(4):
            component.handle_input(DOWN)  # onto "Behavior"
        component.handle_input(ENTER)
        assert "Steering mode" in _text(component)

    def test_a_leaf_inside_a_category_reports_to_the_same_handler(self) -> None:
        changed: list[str] = []
        component = SettingsSelectorComponent(
            _settings_config(), SettingsCallbacks(on_steering_mode_change=changed.append)
        )
        for _ in range(4):
            component.handle_input(DOWN)
        component.handle_input(ENTER)  # open Behavior
        component.handle_input(ENTER)  # cycle "Steering mode"
        assert changed == ["one-at-a-time"]

    def test_escape_at_the_top_cancels_the_overlay(self) -> None:
        cancels: list[bool] = []
        component = SettingsSelectorComponent(
            _settings_config(), SettingsCallbacks(on_cancel=lambda: cancels.append(True))
        )
        component.handle_input(ESCAPE)
        assert cancels == [True]

    def test_escape_inside_a_category_goes_back_rather_than_out(self) -> None:
        cancels: list[bool] = []
        component = SettingsSelectorComponent(
            _settings_config(), SettingsCallbacks(on_cancel=lambda: cancels.append(True))
        )
        for _ in range(4):
            component.handle_input(DOWN)
        component.handle_input(ENTER)
        component.handle_input(ESCAPE)
        assert cancels == [], "escaping a submenu closed the whole overlay"
        assert "Behavior" in _text(component)

    def test_the_theme_submenu_previews_as_the_cursor_moves(self) -> None:
        previews: list[str] = []
        component = SettingsSelectorComponent(
            _settings_config(), SettingsCallbacks(on_theme_preview=previews.append)
        )
        for _ in range(5):
            component.handle_input(DOWN)  # onto "Interface"
        component.handle_input(ENTER)
        component.handle_input(ENTER)  # open "Theme"
        component.handle_input(DOWN)
        assert previews, "moving inside the theme list did not preview"

    def test_cancelling_the_theme_submenu_restores_the_theme_it_opened_on(self) -> None:
        previews: list[str] = []
        component = SettingsSelectorComponent(
            _settings_config(), SettingsCallbacks(on_theme_preview=previews.append)
        )
        for _ in range(5):
            component.handle_input(DOWN)
        component.handle_input(ENTER)
        component.handle_input(ENTER)
        component.handle_input(DOWN)
        component.handle_input(ESCAPE)
        assert previews[-1] == "dark"

    def test_the_last_core_tool_cannot_be_switched_off(self) -> None:
        changed: list[tuple[str, bool]] = []
        component = SettingsSelectorComponent(
            _settings_config(tools=[ToolToggleInfo("read", True)]),
            SettingsCallbacks(on_tool_enabled_change=lambda name, on: changed.append((name, on))),
        )
        component.handle_input(DOWN)  # onto "Tools"
        component.handle_input(ENTER)  # open it
        component.handle_input(DOWN)  # past the group switch, onto "read"
        component.handle_input(ENTER)
        assert changed == [], "the only remaining core tool was allowed to be disabled"

    def test_a_group_switch_reports_separately_from_the_tools(self) -> None:
        groups: list[tuple[str, bool]] = []
        component = SettingsSelectorComponent(
            _settings_config(),
            SettingsCallbacks(on_tool_group_change=lambda gid, on: groups.append((gid, on))),
        )
        component.handle_input(DOWN)
        component.handle_input(ENTER)
        component.handle_input(ENTER)  # the first row is the group switch
        assert groups == [("web", False)]

    def test_the_image_rows_are_absent_on_a_terminal_that_cannot_show_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import cortex.code.interactive.components.settings_selector as module

        class NoImages:
            images = None

        monkeypatch.setattr(module, "get_capabilities", lambda: NoImages())
        component = SettingsSelectorComponent(_settings_config(), SettingsCallbacks())
        for _ in range(6):
            component.handle_input(DOWN)  # onto "Images"
        component.handle_input(ENTER)
        rendered = _text(component)
        assert "Show images" not in rendered
        assert "Block images" in rendered, "the always-available rows went missing too"

"""Tests for the app-level keybindings module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cortex.code.interactive.keybindings import (
    KEYBINDINGS,
    KeybindingsManager,
    is_legacy_keybinding_name,
    load_raw_config,
    migrate_keybindings_config,
    order_keybindings_config,
    to_keybindings_config,
)


class TestKeybindingsDefinitions:
    """Tests for the KEYBINDINGS table."""

    def test_includes_tui_keybindings(self):
        """App keybindings extend the TUI keybindings."""
        assert "tui.editor.cursorUp" in KEYBINDINGS
        assert "tui.input.submit" in KEYBINDINGS

    def test_includes_app_keybindings(self):
        """App keybindings are present."""
        assert "app.interrupt" in KEYBINDINGS
        assert "app.clear" in KEYBINDINGS
        assert "app.exit" in KEYBINDINGS

    def test_all_have_description(self):
        """Every keybinding has a description."""
        for keybinding, definition in KEYBINDINGS.items():
            assert "description" in definition, f"{keybinding} missing description"

    def test_all_have_default_keys(self):
        """Every keybinding has a defaultKeys field."""
        for keybinding, definition in KEYBINDINGS.items():
            assert "defaultKeys" in definition, f"{keybinding} missing defaultKeys"


class TestLegacyNameMigration:
    """Tests for legacy keybinding name migration."""

    def test_is_legacy_name(self):
        assert is_legacy_keybinding_name("cursorUp") is True
        assert is_legacy_keybinding_name("submit") is True
        assert is_legacy_keybinding_name("tui.editor.cursorUp") is False

    @pytest.mark.parametrize(
        ("legacy", "expected"),
        [
            ("cursorUp", "tui.editor.cursorUp"),
            ("submit", "tui.input.submit"),
            ("interrupt", "app.interrupt"),
            ("clear", "app.clear"),
            ("exit", "app.exit"),
        ],
    )
    def test_migrate_single_key(self, legacy: str, expected: str):
        config, migrated = migrate_keybindings_config({legacy: "ctrl+x"})
        assert migrated is True
        assert expected in config
        assert config[expected] == "ctrl+x"

    def test_migrate_multiple_keys(self):
        raw = {
            "cursorUp": "up",
            "submit": "enter",
            "interrupt": "escape",
        }
        config, migrated = migrate_keybindings_config(raw)
        assert migrated is True
        assert "tui.editor.cursorUp" in config
        assert "tui.input.submit" in config
        assert "app.interrupt" in config

    def test_no_migration_needed(self):
        config, migrated = migrate_keybindings_config(
            {
                "tui.editor.cursorUp": "up",
                "tui.input.submit": "enter",
            }
        )
        assert migrated is False
        assert config["tui.editor.cursorUp"] == "up"
        assert config["tui.input.submit"] == "enter"

    def test_duplicate_preferred_key_wins(self):
        """When both legacy and new key exist, new key wins."""
        raw = {
            "cursorUp": "alt+up",
            "tui.editor.cursorUp": "up",
        }
        config, migrated = migrate_keybindings_config(raw)
        assert migrated is True
        # The new key takes precedence
        assert config["tui.editor.cursorUp"] == "up"


class TestOrderKeybindingsConfig:
    """Tests for ordering keybindings config."""

    def test_preserves_order(self):
        config = {
            "tui.input.submit": "enter",
            "app.interrupt": "escape",
            "tui.editor.cursorUp": "up",
        }
        ordered = order_keybindings_config(config)
        keys = list(ordered.keys())
        # Should be in KEYBINDINGS order
        assert keys.index("tui.editor.cursorUp") < keys.index("tui.input.submit")
        assert keys.index("tui.input.submit") < keys.index("app.interrupt")

    def test_extras_at_end(self):
        """Custom keys not in KEYBINDINGS go at the end, sorted."""
        config = {
            "custom.foo": "ctrl+f",
            "tui.input.submit": "enter",
            "custom.bar": "ctrl+b",
        }
        ordered = order_keybindings_config(config)
        keys = list(ordered.keys())
        assert keys[-2] == "custom.bar"
        assert keys[-1] == "custom.foo"


class TestKeybindingsConfigCoercion:
    """Tests for _to_keybindings_config."""

    def test_string_value(self):
        config = to_keybindings_config({"submit": "enter"})
        assert config["submit"] == "enter"

    def test_list_value(self):
        config = to_keybindings_config({"cursorUp": ["up", "ctrl+p"]})
        assert config["cursorUp"] == ["up", "ctrl+p"]

    def test_non_string_list_filtered(self):
        config = to_keybindings_config({"bad": [1, 2, 3]})
        assert "bad" not in config

    def test_non_dict_returns_empty(self):
        config = to_keybindings_config("not a dict")
        assert config == {}


class TestRawConfigLoading:
    """Tests for _load_raw_config."""

    def test_load_valid_json(self, tmp_path: Path):
        config_file = tmp_path / "keybindings.json"
        config_file.write_text('{"submit": "enter"}')
        result = load_raw_config(config_file)
        assert result == {"submit": "enter"}

    def test_missing_file(self, tmp_path: Path):
        result = load_raw_config(tmp_path / "missing.json")
        assert result is None

    def test_invalid_json(self, tmp_path: Path):
        config_file = tmp_path / "keybindings.json"
        config_file.write_text("not json")
        result = load_raw_config(config_file)
        assert result is None

    def test_non_dict_json(self, tmp_path: Path):
        config_file = tmp_path / "keybindings.json"
        config_file.write_text('["not", "a", "dict"]')
        result = load_raw_config(config_file)
        assert result is None


class TestKeybindingsManager:
    """Tests for the KeybindingsManager class."""

    def test_default_bindings(self):
        manager = KeybindingsManager()
        # Should have all keybindings defined
        assert len(manager.definitions) > 0
        # submit should be bound to enter by default
        keys = manager.get_keys("tui.input.submit")
        assert "enter" in keys

    def test_custom_bindings(self):
        manager = KeybindingsManager(user_bindings={"tui.input.submit": "ctrl+enter"})
        keys = manager.get_keys("tui.input.submit")
        assert "ctrl+enter" in keys

    def test_matches_key(self):
        manager = KeybindingsManager()
        assert manager.matches("\r", "tui.input.submit") is True
        assert manager.matches("\x1b", "app.interrupt") is True

    def test_does_not_match_wrong_key(self):
        manager = KeybindingsManager()
        assert manager.matches("a", "tui.input.submit") is False

    def test_set_user_bindings(self):
        manager = KeybindingsManager()
        manager.set_user_bindings({"tui.input.submit": "ctrl+enter"})
        keys = manager.get_keys("tui.input.submit")
        assert "ctrl+enter" in keys

    def test_get_resolved_bindings(self):
        manager = KeybindingsManager()
        resolved = manager.get_resolved_bindings()
        assert "tui.input.submit" in resolved
        assert resolved["tui.input.submit"] == "enter"

    def test_create_with_config_file(self, tmp_path: Path):
        config_file = tmp_path / "keybindings.json"
        config_file.write_text(json.dumps({"tui.input.submit": "ctrl+enter"}))
        manager = KeybindingsManager.create(tmp_path)
        keys = manager.get_keys("tui.input.submit")
        assert "ctrl+enter" in keys

    def test_reload(self, tmp_path: Path):
        config_file = tmp_path / "keybindings.json"
        config_file.write_text(json.dumps({"tui.input.submit": "ctrl+enter"}))
        manager = KeybindingsManager(config_path=config_file)

        # Initial state
        keys = manager.get_keys("tui.input.submit")
        assert "ctrl+enter" in keys

        # Update file
        config_file.write_text(json.dumps({"tui.input.submit": "ctrl+shift+enter"}))
        manager.reload()

        # Reloaded state
        keys = manager.get_keys("tui.input.submit")
        assert "ctrl+shift+enter" in keys

    def test_reload_without_config_path(self):
        manager = KeybindingsManager()
        # Should not raise
        manager.reload()

    def test_effective_config(self):
        manager = KeybindingsManager()
        config = manager.get_effective_config()
        assert "tui.input.submit" in config

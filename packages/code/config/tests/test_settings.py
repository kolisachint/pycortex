"""Tests for the settings module."""

from cortex.code.config.settings_defaults import DEFAULT_SETTINGS
from cortex.code.config.settings_manager import SettingsManager
from cortex.code.config.settings_storage import InMemorySettingsStorage
from cortex.code.config.settings_types import (
    CompactionSettings,
    ImageSettings,
    RetrySettings,
    Settings,
    TerminalSettings,
    ToolOutputSettings,
    WarningSettings,
)


class TestSettingsTypes:
    def test_settings_defaults(self):
        settings = Settings()
        assert settings.transport == "auto"
        assert settings.steering_mode == "one-at-a-time"
        assert settings.follow_up_mode == "one-at-a-time"
        assert settings.theme is None
        assert settings.compaction is not None
        assert settings.tool_output is not None

    def test_compaction_settings(self):
        settings = CompactionSettings()
        assert settings.enabled is True
        assert settings.reserve_tokens == 16384
        assert settings.keep_recent_tokens == 20000
        assert settings.max_context_ratio == 0.75

    def test_tool_output_settings(self):
        settings = ToolOutputSettings()
        assert settings.max_bytes == 32768
        assert settings.max_lines == 800

    def test_terminal_settings(self):
        settings = TerminalSettings()
        assert settings.show_images is True
        assert settings.image_width_cells == 60
        assert settings.clear_on_shrink is False
        assert settings.show_terminal_progress is False
        assert settings.chime_on_turn_complete is False

    def test_image_settings(self):
        settings = ImageSettings()
        assert settings.auto_resize is True
        assert settings.block_images is False

    def test_retry_settings(self):
        settings = RetrySettings()
        assert settings.enabled is True
        assert settings.max_retries == 3
        assert settings.base_delay_ms == 2000


class TestDefaultSettings:
    def test_default_settings_exists(self):
        assert DEFAULT_SETTINGS is not None

    def test_default_settings_has_all_fields(self):
        assert DEFAULT_SETTINGS.transport == "auto"
        assert DEFAULT_SETTINGS.compaction is not None
        assert DEFAULT_SETTINGS.tool_output is not None
        assert DEFAULT_SETTINGS.context_gc is not None
        assert DEFAULT_SETTINGS.voice is not None
        assert DEFAULT_SETTINGS.webtools is not None
        assert DEFAULT_SETTINGS.branch_summary is not None
        assert DEFAULT_SETTINGS.retry is not None
        assert DEFAULT_SETTINGS.terminal is not None
        assert DEFAULT_SETTINGS.images is not None
        assert DEFAULT_SETTINGS.markdown is not None
        assert DEFAULT_SETTINGS.warnings is not None


class TestSettingsManager:
    def test_in_memory_creation(self):
        manager = SettingsManager.in_memory()
        assert manager is not None

    def test_get_theme_default(self):
        manager = SettingsManager.in_memory()
        assert manager.get_theme() is None

    def test_set_theme(self):
        manager = SettingsManager.in_memory()
        manager.set_theme("dark")
        assert manager.get_theme() == "dark"

    def test_get_default_provider(self):
        manager = SettingsManager.in_memory()
        assert manager.get_default_provider() is None

    def test_set_default_provider(self):
        manager = SettingsManager.in_memory()
        manager.set_default_provider("anthropic")
        assert manager.get_default_provider() == "anthropic"

    def test_get_default_model(self):
        manager = SettingsManager.in_memory()
        assert manager.get_default_model() is None

    def test_set_default_model(self):
        manager = SettingsManager.in_memory()
        manager.set_default_model("claude-3-opus")
        assert manager.get_default_model() == "claude-3-opus"

    def test_get_disabled_tools_default(self):
        manager = SettingsManager.in_memory()
        assert manager.get_disabled_tools() == []

    def test_set_disabled_tools(self):
        manager = SettingsManager.in_memory()
        manager.set_disabled_tools(["bash", "write"])
        assert manager.get_disabled_tools() == ["bash", "write"]

    def test_get_tool_output_display_default(self):
        manager = SettingsManager.in_memory()
        assert manager.get_tool_output_display() == "standard"

    def test_set_tool_output_display(self):
        manager = SettingsManager.in_memory()
        manager.set_tool_output_display("peek")
        assert manager.get_tool_output_display() == "peek"

    def test_get_tool_output_max_bytes(self):
        manager = SettingsManager.in_memory()
        assert manager.get_tool_output_max_bytes() == 32768

    def test_set_tool_output_max_bytes(self):
        manager = SettingsManager.in_memory()
        manager.set_tool_output_max_bytes(65536)
        assert manager.get_tool_output_max_bytes() == 65536

    def test_set_tool_output_max_bytes_clamps(self):
        manager = SettingsManager.in_memory()
        manager.set_tool_output_max_bytes(100)
        assert manager.get_tool_output_max_bytes() == 1024

    def test_get_tool_output_max_lines(self):
        manager = SettingsManager.in_memory()
        assert manager.get_tool_output_max_lines() == 800

    def test_set_tool_output_max_lines(self):
        manager = SettingsManager.in_memory()
        manager.set_tool_output_max_lines(1600)
        assert manager.get_tool_output_max_lines() == 1600

    def test_set_tool_output_max_lines_clamps(self):
        manager = SettingsManager.in_memory()
        manager.set_tool_output_max_lines(0)
        assert manager.get_tool_output_max_lines() == 1

    def test_get_voice_silence_ms(self):
        manager = SettingsManager.in_memory()
        assert manager.get_voice_silence_ms() == 800

    def test_set_voice_silence_ms(self):
        manager = SettingsManager.in_memory()
        manager.set_voice_silence_ms(1500)
        assert manager.get_voice_silence_ms() == 1500

    def test_set_voice_silence_ms_clamps(self):
        manager = SettingsManager.in_memory()
        manager.set_voice_silence_ms(50)
        assert manager.get_voice_silence_ms() == 300

    def test_get_webtools_timeout_secs(self):
        manager = SettingsManager.in_memory()
        assert manager.get_webtools_timeout_secs() == 15

    def test_set_webtools_timeout_secs(self):
        manager = SettingsManager.in_memory()
        manager.set_webtools_timeout_secs(45)
        assert manager.get_webtools_timeout_secs() == 45

    def test_set_webtools_timeout_secs_clamps(self):
        manager = SettingsManager.in_memory()
        manager.set_webtools_timeout_secs(0)
        assert manager.get_webtools_timeout_secs() == 1

    def test_get_flag_overrides(self):
        manager = SettingsManager.in_memory()
        assert manager.get_flag_overrides() == {}

    def test_set_flag_override(self):
        manager = SettingsManager.in_memory()
        manager.set_flag_override("plan", True)
        assert manager.get_flag_overrides() == {"plan": True}

    def test_clear_flag_override(self):
        manager = SettingsManager.in_memory()
        manager.set_flag_override("plan", True)
        manager.set_flag_override("endpoint", "https://example.test")
        manager.clear_flag_override("plan")
        assert manager.get_flag_overrides() == {"endpoint": "https://example.test"}

    def test_get_shell_command_prefix(self):
        manager = SettingsManager.in_memory()
        assert manager.get_shell_command_prefix() is None

    def test_set_shell_command_prefix(self):
        manager = SettingsManager.in_memory()
        manager.set_shell_command_prefix("shopt -s expand_aliases")
        assert manager.get_shell_command_prefix() == "shopt -s expand_aliases"

    def test_get_session_dir(self):
        manager = SettingsManager.in_memory()
        assert manager.get_session_dir() is None

    def test_get_warnings(self):
        manager = SettingsManager.in_memory()
        warnings = manager.get_warnings()
        assert warnings.anthropic_extra_usage is True

    def test_set_warnings(self):
        manager = SettingsManager.in_memory()
        warnings = WarningSettings(anthropic_extra_usage=False)
        manager.set_warnings(warnings)
        assert manager.get_warnings().anthropic_extra_usage is False


class TestSettingsManagerWithStorage:
    def test_in_memory_storage(self):
        storage = InMemorySettingsStorage()
        manager = SettingsManager.from_storage(storage)
        assert manager is not None

    def test_round_trip(self):
        storage = InMemorySettingsStorage()
        manager = SettingsManager.from_storage(storage)

        # Set some values
        manager.set_theme("dark")
        manager.set_disabled_tools(["bash"])
        manager.set_tool_output_display("peek")

        # Flush
        import asyncio

        asyncio.run(manager.flush())

        # Create new manager from same storage
        manager2 = SettingsManager.from_storage(storage)

        # Values should be preserved
        assert manager2.get_theme() == "dark"
        assert manager2.get_disabled_tools() == ["bash"]
        assert manager2.get_tool_output_display() == "peek"

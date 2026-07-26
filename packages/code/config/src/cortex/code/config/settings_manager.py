# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportReturnType=false, reportUnnecessaryIsInstance=false, reportUnnecessaryComparison=false
"""Settings manager for loading, saving, and accessing settings."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import fields

from cortex.code.config import get_agent_dir
from cortex.code.config.settings_defaults import DEFAULT_SETTINGS
from cortex.code.config.settings_storage import (
    FileSettingsStorage,
    InMemorySettingsStorage,
    SettingsError,
    SettingsScope,
    SettingsStorage,
)
from cortex.code.config.settings_types import (
    ModelCategories,
    PackageSource,
    ProviderRetrySettings,
    Settings,
    ThinkingBudgetsSettings,
    WarningSettings,
)


def _deep_merge_settings(base: Settings, overrides: Settings) -> Settings:
    """Deep merge two Settings instances, with overrides taking precedence."""
    result = copy.deepcopy(base)

    for f in fields(overrides):
        override_value = getattr(overrides, f.name)
        base_value = getattr(base, f.name)

        # For nested dataclasses, merge them
        if hasattr(override_value, "__dataclass_fields__") and hasattr(
            base_value, "__dataclass_fields__"
        ):
            setattr(result, f.name, _deep_merge_settings(base_value, override_value))
        elif override_value is not None:
            setattr(result, f.name, copy.deepcopy(override_value))

    return result


def _migrate_settings(settings: Settings) -> Settings:
    """Migrate old settings formats to current format."""
    result = copy.deepcopy(settings)

    # Migration: queueMode -> steeringMode
    # (This would be handled during JSON parsing)

    # Migration: websockets -> transport
    # (This would be handled during JSON parsing)

    # Migration: skills object -> array
    # (This would be handled during JSON parsing)

    # Migration: retry.maxDelayMs -> provider.maxRetryDelayMs
    if result.retry.provider is None:
        result.retry.provider = ProviderRetrySettings()

    return result


def _settings_to_dict(settings: Settings) -> dict:
    """Convert a Settings instance to a dictionary for JSON serialization."""
    result = {}
    for f in fields(settings):
        value = getattr(settings, f.name)
        if value is None:
            continue
        if hasattr(value, "__dataclass_fields__"):
            result[f.name] = _settings_to_dict(value)
        else:
            result[f.name] = value
    return result


def _dict_to_dataclass(cls: type, data: dict) -> object:
    """Convert a dictionary to a dataclass instance."""
    from dataclasses import fields as dataclass_fields

    if not hasattr(cls, "__dataclass_fields__"):
        return data

    # Map field names to their types
    field_types = {f.name: f.type for f in dataclass_fields(cls)}

    kwargs = {}
    for field_name, value in data.items():
        if field_name not in field_types:
            continue

        field_type = field_types[field_name]

        # Check if it's a nested dataclass
        if isinstance(value, dict):
            # Try to find the actual type class
            type_name = (
                field_type
                if isinstance(field_type, str)
                else field_type.__name__
                if hasattr(field_type, "__name__")
                else None
            )
            if type_name:
                # Import the type from the module
                import cortex.code.config.settings_types as st

                if hasattr(st, type_name):
                    nested_cls = getattr(st, type_name)
                    if hasattr(nested_cls, "__dataclass_fields__"):
                        kwargs[field_name] = _dict_to_dataclass(nested_cls, value)
                        continue

        kwargs[field_name] = value

    return cls(**kwargs)


def _dict_to_settings(data: dict) -> Settings:
    """Convert a dictionary to a Settings instance."""
    return _dict_to_dataclass(Settings, data)


class SettingsManager:
    """Main settings manager that loads/saves settings and provides getters/setters."""

    def __init__(
        self,
        storage: SettingsStorage,
        initial_global: Settings,
        initial_project: Settings,
        initial_settings: Settings | None = None,
        global_load_error: Exception | None = None,
        project_load_error: Exception | None = None,
        initial_errors: list[SettingsError] | None = None,
    ) -> None:
        self._storage = storage
        self._global_settings = copy.deepcopy(initial_global)
        self._project_settings = copy.deepcopy(initial_project)
        self._settings = (
            copy.deepcopy(initial_settings)
            if initial_settings is not None
            else _deep_merge_settings(initial_global, initial_project)
        )
        self._global_settings_load_error = global_load_error
        self._project_settings_load_error = project_load_error
        self._errors = list(initial_errors or [])
        self._modified_fields: set[str] = set()
        self._modified_nested_fields: dict[str, set[str]] = {}
        self._modified_project_fields: set[str] = set()
        self._modified_project_nested_fields: dict[str, set[str]] = {}

    @classmethod
    def create(cls, cwd: str, agent_dir: str | None = None) -> SettingsManager:
        """Create a SettingsManager with file-based storage."""
        if agent_dir is None:
            agent_dir = get_agent_dir()
        storage = FileSettingsStorage(cwd, agent_dir)
        return cls.from_storage(storage)

    @classmethod
    def from_storage(cls, storage: SettingsStorage) -> SettingsManager:
        """Create a SettingsManager from an existing storage backend."""
        global_settings = copy.deepcopy(DEFAULT_SETTINGS)
        project_settings = copy.deepcopy(DEFAULT_SETTINGS)
        global_file_loaded = False
        project_file_loaded = False
        global_error: Exception | None = None
        project_error: Exception | None = None
        errors: list[SettingsError] = []

        def load_global(current: str | None) -> None:
            nonlocal global_settings, global_file_loaded, global_error
            if current is None:
                return
            try:
                data = json.loads(current)
                loaded = _dict_to_settings(data)
                global_settings = _deep_merge_settings(DEFAULT_SETTINGS, loaded)
                global_file_loaded = True
            except Exception as e:
                global_error = e
                errors.append(SettingsError("global", e))
            return None

        def load_project(current: str | None) -> None:
            nonlocal project_settings, project_file_loaded, project_error
            if current is None:
                return
            try:
                data = json.loads(current)
                loaded = _dict_to_settings(data)
                project_settings = _deep_merge_settings(DEFAULT_SETTINGS, loaded)
                project_file_loaded = True
            except Exception as e:
                project_error = e
                errors.append(SettingsError("project", e))
            return None

        storage.with_lock("global", load_global)
        storage.with_lock("project", load_project)

        # When no project file exists, don't let project defaults overwrite global
        # values. Only merge when both layers have data from disk.
        if project_file_loaded:
            settings = _deep_merge_settings(global_settings, project_settings)
        else:
            settings = copy.deepcopy(global_settings)
        settings = _migrate_settings(settings)

        return cls(
            storage=storage,
            initial_global=global_settings,
            initial_project=project_settings,
            initial_settings=settings,
            global_load_error=global_error,
            project_load_error=project_error,
            initial_errors=errors,
        )

    @classmethod
    def in_memory(cls, settings: Settings | None = None) -> SettingsManager:
        """Create a SettingsManager with in-memory storage for tests."""
        storage = InMemorySettingsStorage()
        initial = settings or copy.deepcopy(DEFAULT_SETTINGS)
        return cls(
            storage=storage,
            initial_global=initial,
            initial_project=copy.deepcopy(DEFAULT_SETTINGS),
        )

    @property
    def settings(self) -> Settings:
        """Get the current merged settings."""
        return self._settings

    def get_global_settings(self) -> Settings:
        """Get the global settings."""
        return copy.deepcopy(self._global_settings)

    def get_project_settings(self) -> Settings:
        """Get the project settings."""
        return copy.deepcopy(self._project_settings)

    def get_default_settings(self) -> Settings:
        """Get the default settings."""
        return copy.deepcopy(DEFAULT_SETTINGS)

    async def reload(self) -> None:
        """Reload settings from disk."""
        new_global = copy.deepcopy(DEFAULT_SETTINGS)
        new_project = copy.deepcopy(DEFAULT_SETTINGS)
        new_errors: list[SettingsError] = []

        def load_global(current: str | None) -> None:
            nonlocal new_global
            if current is None:
                return
            try:
                data = json.loads(current)
                loaded = _dict_to_settings(data)
                new_global = _deep_merge_settings(DEFAULT_SETTINGS, loaded)
            except Exception as e:
                new_errors.append(SettingsError("global", e))
            return None

        def load_project(current: str | None) -> None:
            nonlocal new_project
            if current is None:
                return
            try:
                data = json.loads(current)
                loaded = _dict_to_settings(data)
                new_project = _deep_merge_settings(DEFAULT_SETTINGS, loaded)
            except Exception as e:
                new_errors.append(SettingsError("project", e))
            return None

        self._storage.with_lock("global", load_global)
        self._storage.with_lock("project", load_project)

        self._global_settings = new_global
        self._project_settings = new_project
        self._settings = _deep_merge_settings(new_global, new_project)
        self._settings = _migrate_settings(self._settings)
        self._errors.extend(new_errors)
        self._modified_fields.clear()
        self._modified_nested_fields.clear()
        self._modified_project_fields.clear()
        self._modified_project_nested_fields.clear()

    def apply_overrides(self, overrides: Settings) -> None:
        """Apply overrides to the current settings."""
        self._settings = _deep_merge_settings(self._settings, overrides)

    async def flush(self) -> None:
        """Save modified settings to storage."""
        if self._modified_fields or self._modified_nested_fields:
            self._save_settings(
                "global", self._global_settings, self._modified_fields, self._modified_nested_fields
            )
        if self._modified_project_fields or self._modified_project_nested_fields:
            self._save_settings(
                "project",
                self._project_settings,
                self._modified_project_fields,
                self._modified_project_nested_fields,
            )
        self._modified_fields.clear()
        self._modified_nested_fields.clear()
        self._modified_project_fields.clear()
        self._modified_project_nested_fields.clear()

    def _save_settings(
        self,
        scope: SettingsScope,
        settings: Settings,
        modified_fields: set[str],
        modified_nested_fields: dict[str, set[str]],
    ) -> None:
        """Save settings to storage."""

        def write(current: str | None) -> str | None:
            current_data = json.loads(current) if current else {}
            new_data = _settings_to_dict(settings)

            # Only write modified fields
            for field_name in modified_fields:
                if field_name in new_data:
                    current_data[field_name] = new_data[field_name]
                elif field_name in current_data:
                    del current_data[field_name]

            # Handle nested fields
            for field_name, nested_fields in modified_nested_fields.items():
                if field_name not in current_data:
                    current_data[field_name] = {}
                if field_name in new_data:
                    nested_new = new_data[field_name]
                    if isinstance(nested_new, dict):
                        for nested_field in nested_fields:
                            if nested_field in nested_new:
                                current_data[field_name][nested_field] = nested_new[nested_field]

            return json.dumps(current_data, indent=2)

        self._storage.with_lock(scope, write)

    def drain_errors(self) -> list[SettingsError]:
        """Drain and return accumulated errors."""
        errors = list(self._errors)
        self._errors.clear()
        return errors

    def _mark_modified(self, field_name: str, nested_field: str | None = None) -> None:
        """Mark a field as modified."""
        self._modified_fields.add(field_name)
        if nested_field:
            if field_name not in self._modified_nested_fields:
                self._modified_nested_fields[field_name] = set()
            self._modified_nested_fields[field_name].add(nested_field)

    def _mark_project_modified(self, field_name: str, nested_field: str | None = None) -> None:
        """Mark a project field as modified."""
        self._modified_project_fields.add(field_name)
        if nested_field:
            if field_name not in self._modified_project_nested_fields:
                self._modified_project_nested_fields[field_name] = set()
            self._modified_project_nested_fields[field_name].add(nested_field)

    # ============================================================================
    # Getter/Setter pairs
    # ============================================================================

    def get_theme(self) -> str | None:
        return self._settings.theme

    def set_theme(self, theme: str) -> None:
        self._global_settings.theme = theme
        self._settings.theme = theme
        self._mark_modified("theme")

    def get_default_provider(self) -> str | None:
        return self._settings.default_provider

    def set_default_provider(self, provider: str | None) -> None:
        self._global_settings.default_provider = provider
        self._settings.default_provider = provider
        self._mark_modified("default_provider")

    def get_default_model(self) -> str | None:
        return self._settings.default_model

    def set_default_model(self, model: str | None) -> None:
        self._global_settings.default_model = model
        self._settings.default_model = model
        self._mark_modified("default_model")

    def get_default_thinking_level(self) -> str | None:
        return self._settings.default_thinking_level

    def set_default_thinking_level(self, level: str) -> None:
        self._global_settings.default_thinking_level = level  # type: ignore
        self._settings.default_thinking_level = level  # type: ignore
        self._mark_modified("default_thinking_level")

    def get_model_categories(self) -> ModelCategories | None:
        return self._settings.model_categories

    def get_transport(self) -> str:
        return self._settings.transport

    def set_transport(self, transport: str) -> None:
        self._global_settings.transport = transport  # type: ignore
        self._settings.transport = transport  # type: ignore
        self._mark_modified("transport")

    def get_steering_mode(self) -> str:
        return self._settings.steering_mode

    def get_follow_up_mode(self) -> str:
        return self._settings.follow_up_mode

    def get_compaction_enabled(self) -> bool:
        return self._settings.compaction.enabled

    def get_compaction_reserve_tokens(self) -> int:
        return self._settings.compaction.reserve_tokens

    def get_compaction_keep_recent_tokens(self) -> int:
        return self._settings.compaction.keep_recent_tokens

    def get_compaction_max_context_ratio(self) -> float:
        return self._settings.compaction.max_context_ratio

    def get_tool_output_max_bytes(self) -> int:
        return self._settings.tool_output.max_bytes

    def set_tool_output_max_bytes(self, max_bytes: int) -> None:
        self._global_settings.tool_output.max_bytes = max(1024, max_bytes)
        self._settings.tool_output.max_bytes = max(1024, max_bytes)
        self._mark_modified("tool_output", "max_bytes")

    def get_tool_output_max_lines(self) -> int:
        return self._settings.tool_output.max_lines

    def set_tool_output_max_lines(self, max_lines: int) -> None:
        self._global_settings.tool_output.max_lines = max(1, max_lines)
        self._settings.tool_output.max_lines = max(1, max_lines)
        self._mark_modified("tool_output", "max_lines")

    def get_context_gc_enabled(self) -> bool:
        return self._settings.context_gc.enabled

    def set_context_gc_enabled(self, enabled: bool) -> None:
        self._global_settings.context_gc.enabled = enabled
        self._settings.context_gc.enabled = enabled
        self._mark_modified("context_gc", "enabled")

    def get_voice_silence_ms(self) -> int:
        return self._settings.voice.silence_ms

    def set_voice_silence_ms(self, ms: int) -> None:
        clamped = max(300, min(10000, ms))
        self._global_settings.voice.silence_ms = clamped
        self._settings.voice.silence_ms = clamped
        self._mark_modified("voice", "silence_ms")

    def get_webtools_timeout_secs(self) -> int:
        # Check env var if setting is at default
        if self._settings.webtools.timeout_secs == DEFAULT_SETTINGS.webtools.timeout_secs:
            env_val = os.environ.get("HOOCODE_WEBTOOLS_TIMEOUT")
            if env_val:
                try:
                    return max(1, min(120, int(env_val)))
                except ValueError:
                    pass
        return self._settings.webtools.timeout_secs

    def set_webtools_timeout_secs(self, secs: int) -> None:
        clamped = max(1, min(120, secs))
        self._global_settings.webtools.timeout_secs = clamped
        self._settings.webtools.timeout_secs = clamped
        self._mark_modified("webtools", "timeout_secs")

    def get_disabled_tools(self) -> list[str]:
        return list(self._settings.disabled_tools)

    def set_disabled_tools(self, tools: list[str]) -> None:
        self._global_settings.disabled_tools = list(tools)
        self._settings.disabled_tools = list(tools)
        self._mark_modified("disabled_tools")

    def get_tool_output_display(self) -> str:
        return self._settings.tool_output_display

    def set_tool_output_display(self, display: str) -> None:
        valid = ["collapsed", "peek", "standard"]
        if display in valid:
            self._global_settings.tool_output_display = display  # type: ignore
            self._settings.tool_output_display = display  # type: ignore
            self._mark_modified("tool_output_display")

    def get_flag_overrides(self) -> dict[str, bool | str]:
        return dict(self._settings.flags)

    def set_flag_override(self, key: str, value: bool | str) -> None:
        self._global_settings.flags[key] = value
        self._settings.flags[key] = value
        self._mark_modified("flags")

    def clear_flag_override(self, key: str) -> None:
        self._global_settings.flags.pop(key, None)
        self._settings.flags.pop(key, None)
        self._mark_modified("flags")

    def get_branch_summary_reserve_tokens(self) -> int:
        return self._settings.branch_summary.reserve_tokens

    def get_branch_summary_skip_prompt(self) -> bool:
        return self._settings.branch_summary.skip_prompt

    def get_retry_enabled(self) -> bool:
        return self._settings.retry.enabled

    def get_retry_max_retries(self) -> int:
        return self._settings.retry.max_retries

    def get_retry_base_delay_ms(self) -> int:
        return self._settings.retry.base_delay_ms

    def get_hide_thinking_block(self) -> bool:
        return self._settings.hide_thinking_block

    def set_hide_thinking_block(self, hide: bool) -> None:
        self._global_settings.hide_thinking_block = hide
        self._settings.hide_thinking_block = hide
        self._mark_modified("hide_thinking_block")

    def get_shell_path(self) -> str | None:
        return self._settings.shell_path

    def set_shell_path(self, path: str | None) -> None:
        self._global_settings.shell_path = path
        self._settings.shell_path = path
        self._mark_modified("shell_path")

    def get_quiet_startup(self) -> bool:
        return self._settings.quiet_startup

    def set_quiet_startup(self, quiet: bool) -> None:
        self._global_settings.quiet_startup = quiet
        self._settings.quiet_startup = quiet
        self._mark_modified("quiet_startup")

    def get_shell_command_prefix(self) -> str | None:
        return self._settings.shell_command_prefix

    def set_shell_command_prefix(self, prefix: str | None) -> None:
        self._global_settings.shell_command_prefix = prefix
        self._settings.shell_command_prefix = prefix
        self._mark_modified("shell_command_prefix")

    def get_npm_command(self) -> list[str] | None:
        return self._settings.npm_command

    def get_collapse_changelog(self) -> bool:
        return self._settings.collapse_changelog

    def get_enable_install_telemetry(self) -> bool:
        return self._settings.enable_install_telemetry

    def get_packages(self) -> list[PackageSource]:
        return list(self._settings.packages)

    def set_project_packages(self, packages: list[PackageSource]) -> None:
        self._project_settings.packages = list(packages)
        self._settings.packages = list(packages)
        self._mark_project_modified("packages")

    def get_extension_paths(self) -> list[str]:
        return list(self._settings.extensions)

    def set_extension_paths(self, paths: list[str]) -> None:
        self._global_settings.extensions = list(paths)
        self._settings.extensions = list(paths)
        self._mark_modified("extensions")

    def set_project_extension_paths(self, paths: list[str]) -> None:
        self._project_settings.extensions = list(paths)
        self._settings.extensions = list(paths)
        self._mark_project_modified("extensions")

    def get_skill_paths(self) -> list[str]:
        return list(self._settings.skills)

    def set_skill_paths(self, paths: list[str]) -> None:
        self._global_settings.skills = list(paths)
        self._settings.skills = list(paths)
        self._mark_modified("skills")

    def set_project_skill_paths(self, paths: list[str]) -> None:
        self._project_settings.skills = list(paths)
        self._settings.skills = list(paths)
        self._mark_project_modified("skills")

    def get_prompt_template_paths(self) -> list[str]:
        return list(self._settings.prompts)

    def set_prompt_template_paths(self, paths: list[str]) -> None:
        self._global_settings.prompts = list(paths)
        self._settings.prompts = list(paths)
        self._mark_modified("prompts")

    def set_project_prompt_template_paths(self, paths: list[str]) -> None:
        self._project_settings.prompts = list(paths)
        self._settings.prompts = list(paths)
        self._mark_project_modified("prompts")

    def get_slash_command_paths(self) -> list[str]:
        return list(self._settings.slash_commands)

    def set_slash_command_paths(self, paths: list[str]) -> None:
        self._global_settings.slash_commands = list(paths)
        self._settings.slash_commands = list(paths)
        self._mark_modified("slash_commands")

    def set_project_slash_command_paths(self, paths: list[str]) -> None:
        self._project_settings.slash_commands = list(paths)
        self._settings.slash_commands = list(paths)
        self._mark_project_modified("slash_commands")

    def get_theme_paths(self) -> list[str]:
        return list(self._settings.themes)

    def set_theme_paths(self, paths: list[str]) -> None:
        self._global_settings.themes = list(paths)
        self._settings.themes = list(paths)
        self._mark_modified("themes")

    def set_project_theme_paths(self, paths: list[str]) -> None:
        self._project_settings.themes = list(paths)
        self._settings.themes = list(paths)
        self._mark_project_modified("themes")

    def get_enable_skill_commands(self) -> bool:
        return self._settings.enable_skill_commands

    def set_enable_skill_commands(self, enabled: bool) -> None:
        self._global_settings.enable_skill_commands = enabled
        self._settings.enable_skill_commands = enabled
        self._mark_modified("enable_skill_commands")

    def get_enable_subagent(self) -> bool:
        return self._settings.enable_subagent

    def set_enable_subagent(self, enabled: bool) -> None:
        self._global_settings.enable_subagent = enabled
        self._settings.enable_subagent = enabled
        self._mark_modified("enable_subagent")

    def get_warm_subagents(self) -> bool:
        return self._settings.warm_subagents

    def set_warm_subagents(self, enabled: bool) -> None:
        self._global_settings.warm_subagents = enabled
        self._settings.warm_subagents = enabled
        self._mark_modified("warm_subagents")

    def get_max_subagent_depth(self) -> int:
        v = self._settings.max_subagent_depth
        return max(1, v) if isinstance(v, int) and v >= 1 else 1

    def get_nested_subagent_concurrency(self) -> int:
        v = self._settings.nested_subagent_concurrency
        return max(1, v) if isinstance(v, int) and v >= 1 else 2

    def get_enable_todo_write(self) -> bool:
        return self._settings.enable_todo_write

    def set_enable_todo_write(self, enabled: bool) -> None:
        self._global_settings.enable_todo_write = enabled
        self._settings.enable_todo_write = enabled
        self._mark_modified("enable_todo_write")

    def get_support_platform(self) -> list[str] | None:
        value = self._settings.support_platform
        if value is None:
            return None
        return value if isinstance(value, list) else [value]

    def get_enable_plugin_tools(self) -> bool:
        return self._settings.enable_plugin_tools

    def set_enable_plugin_tools(self, enabled: bool) -> None:
        self._global_settings.enable_plugin_tools = enabled
        self._settings.enable_plugin_tools = enabled
        self._mark_modified("enable_plugin_tools")

    def get_defer_mcp_schemas(self) -> bool:
        return self._settings.defer_mcp_schemas

    def set_defer_mcp_schemas(self, enabled: bool) -> None:
        self._global_settings.defer_mcp_schemas = enabled
        self._settings.defer_mcp_schemas = enabled
        self._mark_modified("defer_mcp_schemas")

    def get_enable_web_tools(self) -> bool:
        return self._settings.enable_web_tools

    def set_enable_web_tools(self, enabled: bool) -> None:
        self._global_settings.enable_web_tools = enabled
        self._settings.enable_web_tools = enabled
        self._mark_modified("enable_web_tools")

    def get_enable_embsearch_tools(self) -> bool:
        return self._settings.enable_embsearch_tools

    def set_enable_embsearch_tools(self, enabled: bool) -> None:
        self._global_settings.enable_embsearch_tools = enabled
        self._settings.enable_embsearch_tools = enabled
        self._mark_modified("enable_embsearch_tools")

    def get_embsearch_binary_path(self) -> str | None:
        return self._settings.embsearch_binary_path

    def get_embsearch_threshold_bytes(self) -> int:
        return self._settings.embsearch_threshold_bytes

    def get_enable_browser_tools(self) -> bool:
        return self._settings.enable_browser_tools

    def set_enable_browser_tools(self, enabled: bool) -> None:
        self._global_settings.enable_browser_tools = enabled
        self._settings.enable_browser_tools = enabled
        self._mark_modified("enable_browser_tools")

    def get_enable_browser_live_preview(self) -> bool:
        return self._settings.enable_browser_live_preview

    def set_enable_browser_live_preview(self, enabled: bool) -> None:
        self._global_settings.enable_browser_live_preview = enabled
        self._settings.enable_browser_live_preview = enabled
        self._mark_modified("enable_browser_live_preview")

    def get_enable_file_tools(self) -> bool:
        return self._settings.enable_file_tools

    def set_enable_file_tools(self, enabled: bool) -> None:
        self._global_settings.enable_file_tools = enabled
        self._settings.enable_file_tools = enabled
        self._mark_modified("enable_file_tools")

    def get_light(self) -> bool:
        return self._settings.light

    def set_light(self, enabled: bool) -> None:
        self._global_settings.light = enabled
        self._settings.light = enabled
        self._mark_modified("light")

    def get_thinking_budgets(self) -> ThinkingBudgetsSettings | None:
        return self._settings.thinking_budgets

    def get_thinking_display(self) -> str | None:
        return self._settings.thinking_display

    def get_show_images(self) -> bool:
        return self._settings.terminal.show_images

    def set_show_images(self, show: bool) -> None:
        self._global_settings.terminal.show_images = show
        self._settings.terminal.show_images = show
        self._mark_modified("terminal", "show_images")

    def get_image_width_cells(self) -> int:
        width = self._settings.terminal.image_width_cells
        if not isinstance(width, (int, float)):
            return DEFAULT_SETTINGS.terminal.image_width_cells
        return max(1, int(width))

    def set_image_width_cells(self, width: int) -> None:
        self._global_settings.terminal.image_width_cells = max(1, int(width))
        self._settings.terminal.image_width_cells = max(1, int(width))
        self._mark_modified("terminal", "image_width_cells")

    def get_clear_on_shrink(self) -> bool:
        if self._settings.terminal.clear_on_shrink is not None:
            return self._settings.terminal.clear_on_shrink
        return os.environ.get("HOOCODE_CLEAR_ON_SHRINK") == "1"

    def set_clear_on_shrink(self, enabled: bool) -> None:
        self._global_settings.terminal.clear_on_shrink = enabled
        self._settings.terminal.clear_on_shrink = enabled
        self._mark_modified("terminal", "clear_on_shrink")

    def get_show_terminal_progress(self) -> bool:
        return self._settings.terminal.show_terminal_progress

    def set_show_terminal_progress(self, enabled: bool) -> None:
        self._global_settings.terminal.show_terminal_progress = enabled
        self._settings.terminal.show_terminal_progress = enabled
        self._mark_modified("terminal", "show_terminal_progress")

    def get_chime_on_turn_complete(self) -> bool:
        return self._settings.terminal.chime_on_turn_complete

    def set_chime_on_turn_complete(self, enabled: bool) -> None:
        self._global_settings.terminal.chime_on_turn_complete = enabled
        self._settings.terminal.chime_on_turn_complete = enabled
        self._mark_modified("terminal", "chime_on_turn_complete")

    def get_image_auto_resize(self) -> bool:
        return self._settings.images.auto_resize

    def set_image_auto_resize(self, enabled: bool) -> None:
        self._global_settings.images.auto_resize = enabled
        self._settings.images.auto_resize = enabled
        self._mark_modified("images", "auto_resize")

    def get_block_images(self) -> bool:
        return self._settings.images.block_images

    def set_block_images(self, blocked: bool) -> None:
        self._global_settings.images.block_images = blocked
        self._settings.images.block_images = blocked
        self._mark_modified("images", "block_images")

    def get_enabled_models(self) -> list[str] | None:
        return self._settings.enabled_models if self._settings.enabled_models else None

    def set_enabled_models(self, patterns: list[str] | None) -> None:
        self._global_settings.enabled_models = patterns or []
        self._settings.enabled_models = patterns or []
        self._mark_modified("enabled_models")

    def get_double_escape_action(self) -> str:
        return self._settings.double_escape_action

    def set_double_escape_action(self, action: str) -> None:
        self._global_settings.double_escape_action = action  # type: ignore
        self._settings.double_escape_action = action  # type: ignore
        self._mark_modified("double_escape_action")

    def get_tree_filter_mode(self) -> str:
        valid = ["default", "no-tools", "user-only", "labeled-only", "all"]
        mode = self._settings.tree_filter_mode
        return mode if mode in valid else DEFAULT_SETTINGS.tree_filter_mode

    def set_tree_filter_mode(self, mode: str) -> None:
        self._global_settings.tree_filter_mode = mode  # type: ignore
        self._settings.tree_filter_mode = mode  # type: ignore
        self._mark_modified("tree_filter_mode")

    def get_show_hardware_cursor(self) -> bool:
        return (
            self._settings.show_hardware_cursor or os.environ.get("HOOCODE_HARDWARE_CURSOR") == "1"
        )

    def set_show_hardware_cursor(self, enabled: bool) -> None:
        self._global_settings.show_hardware_cursor = enabled
        self._settings.show_hardware_cursor = enabled
        self._mark_modified("show_hardware_cursor")

    def get_editor_padding_x(self) -> int:
        return self._settings.editor_padding_x

    def set_editor_padding_x(self, padding: int) -> None:
        self._global_settings.editor_padding_x = max(0, min(3, int(padding)))
        self._settings.editor_padding_x = max(0, min(3, int(padding)))
        self._mark_modified("editor_padding_x")

    def get_autocomplete_max_visible(self) -> int:
        return self._settings.autocomplete_max_visible

    def set_autocomplete_max_visible(self, max_visible: int) -> None:
        self._global_settings.autocomplete_max_visible = max(3, min(20, int(max_visible)))
        self._settings.autocomplete_max_visible = max(3, min(20, int(max_visible)))
        self._mark_modified("autocomplete_max_visible")

    def get_code_block_indent(self) -> str:
        return self._settings.markdown.code_block_indent

    def get_warnings(self) -> WarningSettings:
        return copy.deepcopy(self._settings.warnings)

    def set_warnings(self, warnings: WarningSettings) -> None:
        self._global_settings.warnings = copy.deepcopy(warnings)
        self._settings.warnings = copy.deepcopy(warnings)
        self._mark_modified("warnings")

    def get_session_dir(self) -> str | None:
        session_dir = self._settings.session_dir
        if session_dir and session_dir.startswith("~"):
            from pathlib import Path

            return str(Path.home() / session_dir[1:])
        return session_dir

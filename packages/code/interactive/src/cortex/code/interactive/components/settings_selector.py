"""The settings overlay. Port of ``components/settings-selector.ts``.

Everything ``/settings`` shows, and it is worth saying what the shape is: a
:class:`~cortex.tui.components.SettingsList` of leaf rows that cycle through
fixed values, plus rows that open a submenu — a nested list (tools, tool
settings, warnings, flags), a :class:`~cortex.tui.components.SelectList` (theme,
thinking level), or a text field (a string flag). The top level shows four
category rows rather than the thirty leaves, and each category submenu shares the
*same* change handler as the top list, so a row behaves identically wherever it
is reached from.

The component is ported whole: every row, every description, every value set. It
is driven entirely by the :class:`SettingsConfig` it is handed and the
:class:`SettingsCallbacks` it reports through, so what the app can actually
supply is the app's business — see
:meth:`~cortex.code.interactive.interactive_mode.InteractiveMode.show_settings_selector`
for the two lists that are empty in this port and why.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from cortex.code.config import WarningSettings
from cortex.code.interactive.components.dynamic_border import DynamicBorder
from cortex.code.interactive.components.keybinding_hints import key_display_text
from cortex.code.interactive.theme import (
    get_select_list_theme,
    get_settings_list_theme,
    get_theme,
)
from cortex.tui.components import (
    Input,
    SelectItem,
    SelectList,
    SelectListLayoutOptions,
    SettingItem,
    SettingsList,
    SettingsListOptions,
    Spacer,
    Text,
)
from cortex.tui.images import get_capabilities
from cortex.tui.render import Container

__all__ = [
    "FlagInfo",
    "SettingsCallbacks",
    "SettingsConfig",
    "SettingsSelectorComponent",
    "ToolGroupInfo",
    "ToolToggleInfo",
]

SETTINGS_SUBMENU_SELECT_LIST_LAYOUT = SelectListLayoutOptions(
    min_primary_column_width=12,
    max_primary_column_width=32,
)

THINKING_DESCRIPTIONS: dict[str, str] = {
    "off": "No reasoning",
    "minimal": "Very brief reasoning (~1k tokens)",
    "low": "Light reasoning (~2k tokens)",
    "medium": "Moderate reasoning (~8k tokens)",
    "high": "Deep reasoning (~16k tokens)",
    "xhigh": "Maximum reasoning (~32k tokens)",
}


@dataclass(frozen=True)
class ToolToggleInfo:
    #: Tool name (e.g. "read", "bash").
    name: str
    #: Whether the tool is enabled (i.e. not in the persisted disabled set).
    enabled: bool


@dataclass(frozen=True)
class FlagInfo:
    #: Flag name, without the leading ``--``.
    name: str
    type: str
    #: Current effective value.
    value: bool | str
    description: str | None = None


@dataclass(frozen=True)
class ToolGroupInfo:
    #: Group identifier ("web", "browser", "file", "embsearch").
    id: str
    label: str
    description: str
    #: Whether the group's tools are available at all.
    enabled: bool


@dataclass
class SettingsConfig:
    """The values every row opens on."""

    auto_compact: bool
    tools: list[ToolToggleInfo]
    tool_groups: list[ToolGroupInfo]
    flags: list[FlagInfo]
    tool_output_display: str
    tool_output_max_bytes: int
    tool_output_max_lines: int
    context_gc: bool
    show_images: bool
    image_width_cells: int
    auto_resize_images: bool
    block_images: bool
    enable_skill_commands: bool
    steering_mode: str
    follow_up_mode: str
    transport: str
    thinking_level: str
    available_thinking_levels: list[str]
    current_theme: str
    available_themes: list[str]
    hide_thinking_block: bool
    collapse_changelog: bool
    enable_install_telemetry: bool
    double_escape_action: str
    tree_filter_mode: str
    show_hardware_cursor: bool
    editor_padding_x: int
    autocomplete_max_visible: int
    quiet_startup: bool
    clear_on_shrink: bool
    show_terminal_progress: bool
    warnings: WarningSettings
    voice_silence_ms: int
    webtools_timeout_secs: int


def _noop(*_args: Any) -> None:
    """The default for every callback: report nowhere.

    The TS's interface makes all of them required and the app passes all of
    them; defaulting here is what lets a test drive one row without writing
    thirty stubs, and an unset callback is never silently *wrong* — the row
    still shows its new value, it just changes nothing behind it.
    """


@dataclass
class SettingsCallbacks:
    """Where each row reports to. Port of ``SettingsCallbacks``."""

    on_auto_compact_change: Callable[[bool], None] = _noop
    on_tool_enabled_change: Callable[[str, bool], None] = _noop
    on_tool_group_change: Callable[[str, bool], None] = _noop
    on_tool_output_display_change: Callable[[str], None] = _noop
    on_tool_output_max_bytes_change: Callable[[int], None] = _noop
    on_tool_output_max_lines_change: Callable[[int], None] = _noop
    on_context_gc_change: Callable[[bool], None] = _noop
    on_flag_change: Callable[[str, bool | str], None] = _noop
    on_show_images_change: Callable[[bool], None] = _noop
    on_image_width_cells_change: Callable[[int], None] = _noop
    on_auto_resize_images_change: Callable[[bool], None] = _noop
    on_block_images_change: Callable[[bool], None] = _noop
    on_enable_skill_commands_change: Callable[[bool], None] = _noop
    on_steering_mode_change: Callable[[str], None] = _noop
    on_follow_up_mode_change: Callable[[str], None] = _noop
    on_transport_change: Callable[[str], None] = _noop
    on_thinking_level_change: Callable[[str], None] = _noop
    on_theme_change: Callable[[str], None] = _noop
    on_theme_preview: Callable[[str], None] = _noop
    on_hide_thinking_block_change: Callable[[bool], None] = _noop
    on_collapse_changelog_change: Callable[[bool], None] = _noop
    on_enable_install_telemetry_change: Callable[[bool], None] = _noop
    on_double_escape_action_change: Callable[[str], None] = _noop
    on_tree_filter_mode_change: Callable[[str], None] = _noop
    on_show_hardware_cursor_change: Callable[[bool], None] = _noop
    on_editor_padding_x_change: Callable[[int], None] = _noop
    on_autocomplete_max_visible_change: Callable[[int], None] = _noop
    on_quiet_startup_change: Callable[[bool], None] = _noop
    on_clear_on_shrink_change: Callable[[bool], None] = _noop
    on_show_terminal_progress_change: Callable[[bool], None] = _noop
    on_warnings_change: Callable[[WarningSettings], None] = _noop
    on_voice_silence_ms_change: Callable[[int], None] = _noop
    on_webtools_timeout_secs_change: Callable[[int], None] = _noop
    on_cancel: Callable[[], None] = _noop


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


class _WarningSettingsSubmenu(Container):
    """The per-warning on/off list."""

    def __init__(
        self,
        warnings: WarningSettings,
        on_change: Callable[[WarningSettings], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__()
        self._state = replace(warnings)

        items = [
            SettingItem(
                id="anthropic-extra-usage",
                label="Anthropic extra usage",
                description=("Warn when Anthropic subscription auth may use paid extra usage"),
                current_value=_bool_text(self._state.anthropic_extra_usage),
                values=["true", "false"],
            )
        ]

        def changed(item_id: str, new_value: str) -> None:
            if item_id == "anthropic-extra-usage":
                self._state = replace(self._state, anthropic_extra_usage=new_value == "true")
                on_change(replace(self._state))

        self.settings_list = SettingsList(
            items, min(len(items), 10), get_settings_list_theme(), changed, on_cancel
        )
        self.add_child(self.settings_list)

    def handle_input(self, data: str) -> None:
        self.settings_list.handle_input(data)


class _ToolsSubmenu(Container):
    """Tool availability: group master switches first, then per-tool toggles.

    Groups decide whether a group's tools are created at all and so apply on the
    next session; per-tool toggles apply live and persist. The core four are
    guarded — the last one of them cannot be switched off, or the agent would be
    left with no way to act at all.
    """

    CORE = frozenset({"read", "bash", "edit", "write"})
    GROUP_PREFIX = "group:"

    def __init__(
        self,
        tools: list[ToolToggleInfo],
        groups: list[ToolGroupInfo],
        on_change: Callable[[str, bool], None],
        on_group_change: Callable[[str, bool], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__()
        self._enabled = {tool.name: tool.enabled for tool in tools}

        group_items = [
            SettingItem(
                id=f"{self.GROUP_PREFIX}{group.id}",
                label=f"[group] {group.label}",
                description=(
                    f"{group.description} Governs whether these tools exist; "
                    "applies on the next session."
                ),
                current_value="on" if group.enabled else "off",
                values=["on", "off"],
            )
            for group in groups
        ]

        tool_items = [
            SettingItem(
                id=tool.name,
                label=tool.name,
                description=(
                    "Core tool. Disabling leaves the agent unable to perform this "
                    "action in every session."
                    if tool.name in self.CORE
                    else "Disable to remove this tool from the agent this session and "
                    "every future session."
                ),
                current_value="on" if tool.enabled else "off",
                values=["on", "off"],
            )
            for tool in tools
        ]

        items = [*group_items, *tool_items]

        def changed(item_id: str, new_value: str) -> None:
            want_enabled = new_value == "on"
            if item_id.startswith(self.GROUP_PREFIX):
                on_group_change(item_id[len(self.GROUP_PREFIX) :], want_enabled)
                return
            if not want_enabled and item_id in self.CORE:
                remaining_core = [
                    name for name in self.CORE if name != item_id and self._enabled.get(name)
                ]
                if not remaining_core:
                    self.settings_list.update_value(item_id, "on")
                    return
            self._enabled[item_id] = want_enabled
            on_change(item_id, want_enabled)

        self.settings_list = SettingsList(
            items,
            min(len(items), 12),
            get_settings_list_theme(),
            changed,
            on_cancel,
            SettingsListOptions(enable_search=True),
        )
        self.add_child(self.settings_list)

    def handle_input(self, data: str) -> None:
        self.settings_list.handle_input(data)


#: Byte-cap presets shown as human labels; mapped back to raw byte counts.
TOOL_OUTPUT_BYTE_PRESETS: tuple[tuple[str, int], ...] = (
    ("8 KB", 8 * 1024),
    ("16 KB", 16 * 1024),
    ("32 KB", 32 * 1024),
    ("64 KB", 64 * 1024),
    ("128 KB", 128 * 1024),
)


def _bytes_to_label(byte_count: int) -> str:
    for label, preset in TOOL_OUTPUT_BYTE_PRESETS:
        if preset == byte_count:
            return label
    return f"{round(byte_count / 1024)} KB"


class _ToolSettingsSubmenu(Container):
    """Truncation caps and context GC. These feed the tool runtime next time it is built."""

    def __init__(
        self,
        max_bytes: int,
        max_lines: int,
        context_gc: bool,
        callbacks: SettingsCallbacks,
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__()

        items = [
            SettingItem(
                id="output-max-bytes",
                label="Output max bytes",
                description=(
                    "Byte cap on a single read/bash result before truncation. "
                    "Applies to future tool calls."
                ),
                current_value=_bytes_to_label(max_bytes),
                values=[label for label, _ in TOOL_OUTPUT_BYTE_PRESETS],
            ),
            SettingItem(
                id="output-max-lines",
                label="Output max lines",
                description=(
                    "Line cap on a single read/bash result before truncation. "
                    "Applies to future tool calls."
                ),
                current_value=str(max_lines),
                values=["200", "400", "800", "1600", "3200"],
            ),
            SettingItem(
                id="context-gc",
                label="Context GC",
                description=(
                    "Stub superseded read results (files later edited/re-read) out of "
                    "the outgoing context."
                ),
                current_value=_bool_text(context_gc),
                values=["true", "false"],
            ),
        ]

        def changed(item_id: str, new_value: str) -> None:
            if item_id == "output-max-bytes":
                preset = next(
                    (value for label, value in TOOL_OUTPUT_BYTE_PRESETS if label == new_value),
                    None,
                )
                if preset is not None:
                    callbacks.on_tool_output_max_bytes_change(preset)
            elif item_id == "output-max-lines":
                callbacks.on_tool_output_max_lines_change(int(new_value))
            elif item_id == "context-gc":
                callbacks.on_context_gc_change(new_value == "true")

        self.settings_list = SettingsList(
            items, min(len(items), 10), get_settings_list_theme(), changed, on_cancel
        )
        self.add_child(self.settings_list)

    def handle_input(self, data: str) -> None:
        self.settings_list.handle_input(data)


class _FlagStringEditSubmenu(Container):
    """Single-line text editor for a string flag value."""

    def __init__(
        self, flag_name: str, current_value: str, done: Callable[[str | None], None]
    ) -> None:
        super().__init__()
        theme = get_theme()

        self.add_child(Text(theme.bold(theme.fg("accent", f"Flag: --{flag_name}")), 0, 0))
        self.add_child(Spacer(1))
        self.add_child(
            Text(theme.fg("muted", "Enter a value · Enter to save · Esc to cancel"), 0, 0)
        )
        self.add_child(Spacer(1))

        self.input = Input()
        self.input.set_value(current_value)
        self.input.on_submit = done
        self.input.on_escape = lambda: done(None)
        self.add_child(self.input)

    def handle_input(self, data: str) -> None:
        self.input.handle_input(data)


class _FlagsSubmenu(Container):
    """Extension-registered flags: booleans toggle, strings open an editor."""

    def __init__(
        self,
        flags: list[FlagInfo],
        on_change: Callable[[str, bool | str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__()

        items: list[SettingItem] = []
        for flag in flags:
            base_description = flag.description or "Extension-registered flag."
            description = (
                f"{base_description} Persists across sessions; some flags need a "
                "restart to fully apply."
            )
            if flag.type == "boolean":
                items.append(
                    SettingItem(
                        id=flag.name,
                        label=flag.name,
                        description=description,
                        current_value="on" if flag.value else "off",
                        values=["on", "off"],
                    )
                )
                continue

            def make_submenu(
                flag_name: str,
            ) -> Callable[[str, Callable[[str | None], None]], Any]:
                return lambda current_value, done: _FlagStringEditSubmenu(
                    flag_name, current_value, done
                )

            items.append(
                SettingItem(
                    id=flag.name,
                    label=flag.name,
                    description=description,
                    # The TS's `?? ""` guards an optional value; `value` is
                    # required here, and a string flag's is already a string.
                    current_value=str(flag.value),
                    submenu=make_submenu(flag.name),
                )
            )

        type_by_name = {flag.name: flag.type for flag in flags}

        def changed(item_id: str, new_value: str) -> None:
            if type_by_name.get(item_id) == "boolean":
                on_change(item_id, new_value == "on")
            else:
                on_change(item_id, new_value)

        self.settings_list = SettingsList(
            items,
            min(len(items), 12) or 1,
            get_settings_list_theme(),
            changed,
            on_cancel,
            SettingsListOptions(enable_search=True),
        )
        self.add_child(self.settings_list)

    def handle_input(self, data: str) -> None:
        self.settings_list.handle_input(data)


class _CategorySubmenu(Container):
    """A named group of leaf rows, sharing the parent's change handler."""

    def __init__(
        self,
        items: list[SettingItem],
        on_change: Callable[[str, str], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__()
        self.settings_list = SettingsList(
            items,
            min(len(items), 10),
            get_settings_list_theme(),
            on_change,
            on_cancel,
            SettingsListOptions(enable_search=True),
        )
        self.add_child(self.settings_list)

    def handle_input(self, data: str) -> None:
        self.settings_list.handle_input(data)


class _SelectSubmenu(Container):
    """A titled :class:`~cortex.tui.components.SelectList` for one setting."""

    def __init__(
        self,
        title: str,
        description: str,
        options: list[SelectItem],
        current_value: str,
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
        on_selection_change: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        theme = get_theme()

        self.add_child(Text(theme.bold(theme.fg("accent", title)), 0, 0))

        if description:
            self.add_child(Spacer(1))
            self.add_child(Text(theme.fg("muted", description), 0, 0))

        self.add_child(Spacer(1))

        self.select_list = SelectList(
            options,
            min(len(options), 10),
            get_select_list_theme(),
            SETTINGS_SUBMENU_SELECT_LIST_LAYOUT,
        )

        current_index = next(
            (index for index, option in enumerate(options) if option.value == current_value),
            -1,
        )
        if current_index != -1:
            self.select_list.set_selected_index(current_index)

        self.select_list.on_select = lambda item: on_select(item.value)
        self.select_list.on_cancel = on_cancel
        if on_selection_change is not None:
            self.select_list.on_selection_change = lambda item: on_selection_change(item.value)

        self.add_child(self.select_list)

        self.add_child(Spacer(1))
        self.add_child(Text(theme.fg("dim", "  Enter to select · Esc to go back"), 0, 0))

    def handle_input(self, data: str) -> None:
        self.select_list.handle_input(data)


class SettingsSelectorComponent(Container):
    """The `/settings` overlay."""

    def __init__(self, config: SettingsConfig, callbacks: SettingsCallbacks) -> None:  # noqa: C901 - 1:1 with the TS's item table
        super().__init__()

        supports_images = bool(get_capabilities().images)
        follow_up_key = key_display_text("app.message.followUp")
        current_warnings = replace(config.warnings)

        tools_on = sum(1 for tool in config.tools if tool.enabled)
        tools_off = len(config.tools) - tools_on

        items: list[SettingItem] = [
            SettingItem(
                id="autocompact",
                label="Auto-compact",
                description="Automatically compact context when it gets too large",
                current_value=_bool_text(config.auto_compact),
                values=["true", "false"],
            ),
            SettingItem(
                id="steering-mode",
                label="Steering mode",
                description=(
                    "Enter while streaming queues steering messages. 'one-at-a-time': "
                    "deliver one, wait for response. 'all': deliver all at once."
                ),
                current_value=config.steering_mode,
                values=["one-at-a-time", "all"],
            ),
            SettingItem(
                id="follow-up-mode",
                label="Follow-up mode",
                description=(
                    f"{follow_up_key} queues follow-up messages until agent stops. "
                    "'one-at-a-time': deliver one, wait for response. 'all': deliver "
                    "all at once."
                ),
                current_value=config.follow_up_mode,
                values=["one-at-a-time", "all"],
            ),
            SettingItem(
                id="transport",
                label="Transport",
                description=("Preferred transport for providers that support multiple transports"),
                current_value=config.transport,
                values=["sse", "websocket", "websocket-cached", "auto"],
            ),
            SettingItem(
                id="hide-thinking",
                label="Hide thinking",
                description="Hide thinking blocks in assistant responses",
                current_value=_bool_text(config.hide_thinking_block),
                values=["true", "false"],
            ),
            SettingItem(
                id="collapse-changelog",
                label="Collapse changelog",
                description="Show condensed changelog after updates",
                current_value=_bool_text(config.collapse_changelog),
                values=["true", "false"],
            ),
            SettingItem(
                id="quiet-startup",
                label="Quiet startup",
                description="Disable verbose printing at startup",
                current_value=_bool_text(config.quiet_startup),
                values=["true", "false"],
            ),
            SettingItem(
                id="install-telemetry",
                label="Install telemetry",
                description=(
                    "Send an anonymous version/update ping after changelog-detected updates"
                ),
                current_value=_bool_text(config.enable_install_telemetry),
                values=["true", "false"],
            ),
            SettingItem(
                id="double-escape-action",
                label="Double-escape action",
                description="Action when pressing Escape twice with empty editor",
                current_value=config.double_escape_action,
                values=["tree", "fork", "none"],
            ),
            SettingItem(
                id="tree-filter-mode",
                label="Tree filter mode",
                description="Default filter when opening /tree",
                current_value=config.tree_filter_mode,
                values=["default", "no-tools", "user-only", "labeled-only", "all"],
            ),
        ]

        def warnings_submenu(_current_value: str, done: Callable[[str | None], None]) -> Container:
            def changed(warnings: WarningSettings) -> None:
                nonlocal current_warnings
                current_warnings = warnings
                callbacks.on_warnings_change(warnings)

            return _WarningSettingsSubmenu(current_warnings, changed, lambda: done(None))

        items.append(
            SettingItem(
                id="warnings",
                label="Warnings",
                description="Enable or disable individual warnings",
                current_value="configure",
                submenu=warnings_submenu,
            )
        )

        def thinking_submenu(current_value: str, done: Callable[[str | None], None]) -> Container:
            def selected(value: str) -> None:
                callbacks.on_thinking_level_change(value)
                done(value)

            return _SelectSubmenu(
                "Thinking Level",
                "Select reasoning depth for thinking-capable models",
                [
                    SelectItem(
                        value=level, label=level, description=THINKING_DESCRIPTIONS.get(level)
                    )
                    for level in config.available_thinking_levels
                ],
                current_value,
                selected,
                lambda: done(None),
            )

        items.append(
            SettingItem(
                id="thinking",
                label="Thinking level",
                description="Reasoning depth for thinking-capable models",
                current_value=config.thinking_level,
                submenu=thinking_submenu,
            )
        )

        def theme_submenu(current_value: str, done: Callable[[str | None], None]) -> Container:
            def selected(value: str) -> None:
                callbacks.on_theme_change(value)
                done(value)

            def cancelled() -> None:
                # Restore the theme the overlay was opened on: the list previews
                # as the cursor moves, so leaving without choosing has to undo
                # the last preview.
                callbacks.on_theme_preview(current_value)
                done(None)

            return _SelectSubmenu(
                "Theme",
                "Select color theme",
                [SelectItem(value=name, label=name) for name in config.available_themes],
                current_value,
                selected,
                cancelled,
                callbacks.on_theme_preview,
            )

        items.append(
            SettingItem(
                id="theme",
                label="Theme",
                description="Color theme for the interface",
                current_value=config.current_theme,
                submenu=theme_submenu,
            )
        )

        # The TS splices these in one at a time, each anchored on the last, which
        # is a list order rather than a sequence of edits — so this builds the
        # order directly and keeps the anchors as the comment they encode.
        if supports_images:
            items.insert(
                1,
                SettingItem(
                    id="show-images",
                    label="Show images",
                    description="Render images inline in terminal",
                    current_value=_bool_text(config.show_images),
                    values=["true", "false"],
                ),
            )
            items.insert(
                2,
                SettingItem(
                    id="image-width-cells",
                    label="Image width",
                    description="Preferred inline image width in terminal cells",
                    current_value=str(config.image_width_cells),
                    values=["60", "80", "120"],
                ),
            )

        trailing: list[SettingItem] = [
            SettingItem(
                id="auto-resize-images",
                label="Auto-resize images",
                description=("Resize large images to 2000x2000 max for better model compatibility"),
                current_value=_bool_text(config.auto_resize_images),
                values=["true", "false"],
            ),
            SettingItem(
                id="block-images",
                label="Block images",
                description="Prevent images from being sent to LLM providers",
                current_value=_bool_text(config.block_images),
                values=["true", "false"],
            ),
            SettingItem(
                id="skill-commands",
                label="Skill commands",
                description="Register skills as /skill:name commands",
                current_value=_bool_text(config.enable_skill_commands),
                values=["true", "false"],
            ),
            SettingItem(
                id="show-hardware-cursor",
                label="Show hardware cursor",
                description=("Show the terminal cursor while still positioning it for IME support"),
                current_value=_bool_text(config.show_hardware_cursor),
                values=["true", "false"],
            ),
            SettingItem(
                id="editor-padding",
                label="Editor padding",
                description="Horizontal padding for input editor (0-3)",
                current_value=str(config.editor_padding_x),
                values=["0", "1", "2", "3"],
            ),
            SettingItem(
                id="autocomplete-max-visible",
                label="Autocomplete max items",
                description="Max visible items in autocomplete dropdown (3-20)",
                current_value=str(config.autocomplete_max_visible),
                values=["3", "5", "7", "10", "15", "20"],
            ),
            SettingItem(
                id="clear-on-shrink",
                label="Clear on shrink",
                description="Clear empty rows when content shrinks (may cause flicker)",
                current_value=_bool_text(config.clear_on_shrink),
                values=["true", "false"],
            ),
            SettingItem(
                id="terminal-progress",
                label="Terminal progress",
                description="Show OSC 9;4 progress indicators in the terminal tab bar",
                current_value=_bool_text(config.show_terminal_progress),
                values=["true", "false"],
            ),
            SettingItem(
                id="voice-silence-ms",
                label="Voice silence window",
                description=(
                    "Trailing-silence (ms) before voice capture auto-stops (300-10000). "
                    "Env: VOICETOOLS_SILENCE_MS."
                ),
                current_value=str(config.voice_silence_ms),
                values=["300", "500", "800", "1200", "2000", "3000", "5000", "8000", "10000"],
            ),
            SettingItem(
                id="webtools-timeout-secs",
                label="Web tools timeout",
                description=(
                    "Per-request timeout (secs) for webfetch/websearch (1-120). "
                    "Env: HOOCODE_WEBTOOLS_TIMEOUT."
                ),
                current_value=str(config.webtools_timeout_secs),
                values=["5", "10", "15", "30", "60", "120"],
            ),
        ]
        insert_at = 3 if supports_images else 1
        items[insert_at:insert_at] = trailing

        def tools_submenu(_current_value: str, done: Callable[[str | None], None]) -> Container:
            return _ToolsSubmenu(
                config.tools,
                config.tool_groups,
                callbacks.on_tool_enabled_change,
                callbacks.on_tool_group_change,
                lambda: done(None),
            )

        def tool_settings_submenu(
            _current_value: str, done: Callable[[str | None], None]
        ) -> Container:
            return _ToolSettingsSubmenu(
                config.tool_output_max_bytes,
                config.tool_output_max_lines,
                config.context_gc,
                callbacks,
                lambda: done(None),
            )

        # Kept together as one block near the top, after the splices above so
        # they are not leapfrogged.
        tool_flag_group: list[SettingItem] = [
            SettingItem(
                id="tools",
                label="Tools",
                description=(
                    "Enable/disable tools and tool groups (web, browser, document, "
                    "semantic search). Changes persist across sessions."
                ),
                current_value=(
                    f"{tools_on} on · {tools_off} off" if tools_off > 0 else f"{tools_on} on"
                ),
                submenu=tools_submenu,
            ),
            SettingItem(
                id="tool-output-display",
                label="Tool output display",
                description=(
                    "How tool results render. 'standard': shown (expandable). "
                    "'collapsed': hidden. 'peek': hidden with a ▸ reveal caret "
                    "(press the expand key to reveal)."
                ),
                current_value=config.tool_output_display,
                values=["standard", "collapsed", "peek"],
            ),
            SettingItem(
                id="tool-settings",
                label="Tool settings",
                description=(
                    "Per-tool runtime settings: output truncation caps and context "
                    "garbage collection."
                ),
                current_value="configure",
                submenu=tool_settings_submenu,
            ),
        ]

        if config.flags:

            def flags_submenu(_current_value: str, done: Callable[[str | None], None]) -> Container:
                return _FlagsSubmenu(config.flags, callbacks.on_flag_change, lambda: done(None))

            tool_flag_group.append(
                SettingItem(
                    id="flags",
                    label="Flags",
                    description=(
                        "Set flags registered by extensions. Changes persist across sessions."
                    ),
                    current_value=(
                        f"{len(config.flags)} flag{'' if len(config.flags) == 1 else 's'}"
                    ),
                    submenu=flags_submenu,
                )
            )

        self.add_child(DynamicBorder())

        def apply_change(item_id: str, new_value: str) -> None:  # noqa: C901 - the TS switch
            is_true = new_value == "true"
            if item_id == "autocompact":
                callbacks.on_auto_compact_change(is_true)
            elif item_id == "tool-output-display":
                callbacks.on_tool_output_display_change(new_value)
            elif item_id == "show-images":
                callbacks.on_show_images_change(is_true)
            elif item_id == "image-width-cells":
                callbacks.on_image_width_cells_change(int(new_value))
            elif item_id == "auto-resize-images":
                callbacks.on_auto_resize_images_change(is_true)
            elif item_id == "block-images":
                callbacks.on_block_images_change(is_true)
            elif item_id == "skill-commands":
                callbacks.on_enable_skill_commands_change(is_true)
            elif item_id == "steering-mode":
                callbacks.on_steering_mode_change(new_value)
            elif item_id == "follow-up-mode":
                callbacks.on_follow_up_mode_change(new_value)
            elif item_id == "transport":
                callbacks.on_transport_change(new_value)
            elif item_id == "hide-thinking":
                callbacks.on_hide_thinking_block_change(is_true)
            elif item_id == "collapse-changelog":
                callbacks.on_collapse_changelog_change(is_true)
            elif item_id == "quiet-startup":
                callbacks.on_quiet_startup_change(is_true)
            elif item_id == "install-telemetry":
                callbacks.on_enable_install_telemetry_change(is_true)
            elif item_id == "double-escape-action":
                callbacks.on_double_escape_action_change(new_value)
            elif item_id == "tree-filter-mode":
                callbacks.on_tree_filter_mode_change(new_value)
            elif item_id == "show-hardware-cursor":
                callbacks.on_show_hardware_cursor_change(is_true)
            elif item_id == "editor-padding":
                callbacks.on_editor_padding_x_change(int(new_value))
            elif item_id == "autocomplete-max-visible":
                callbacks.on_autocomplete_max_visible_change(int(new_value))
            elif item_id == "clear-on-shrink":
                callbacks.on_clear_on_shrink_change(is_true)
            elif item_id == "terminal-progress":
                callbacks.on_show_terminal_progress_change(is_true)
            elif item_id == "voice-silence-ms":
                callbacks.on_voice_silence_ms_change(int(new_value))
            elif item_id == "webtools-timeout-secs":
                callbacks.on_webtools_timeout_secs_change(int(new_value))

        # Partition the flat leaves into named categories so the top level stays
        # short. `items` holds autocompact plus every leaf setting.
        by_id = {item.id: item for item in items}

        def pick(ids: list[str]) -> list[SettingItem]:
            return [by_id[item_id] for item_id in ids if item_id in by_id]

        def category_row(row_id: str, label: str, description: str, ids: list[str]) -> SettingItem:
            members = pick(ids)
            return SettingItem(
                id=row_id,
                label=label,
                description=description,
                current_value=f"{len(members)} setting{'' if len(members) == 1 else 's'}",
                submenu=lambda _current_value, done: _CategorySubmenu(
                    members, apply_change, lambda: done(None)
                ),
            )

        top_items: list[SettingItem] = [
            *([by_id["autocompact"]] if "autocompact" in by_id else []),
            *tool_flag_group,
            category_row(
                "cat-behavior",
                "Behavior",
                (
                    "Agent and session behavior: steering, follow-up, thinking, escape, "
                    "tree filter, transport."
                ),
                [
                    "steering-mode",
                    "follow-up-mode",
                    "thinking",
                    "double-escape-action",
                    "tree-filter-mode",
                    "transport",
                ],
            ),
            category_row(
                "cat-interface",
                "Interface",
                (
                    "Appearance and editor: theme, thinking visibility, cursor, padding, "
                    "autocomplete, terminal."
                ),
                [
                    "theme",
                    "hide-thinking",
                    "show-hardware-cursor",
                    "editor-padding",
                    "autocomplete-max-visible",
                    "clear-on-shrink",
                    "terminal-progress",
                ],
            ),
            category_row(
                "cat-images",
                "Images",
                "Inline image rendering and resizing.",
                [
                    "show-images",
                    "image-width-cells",
                    "auto-resize-images",
                    "block-images",
                ],
            ),
            category_row(
                "cat-advanced",
                "Advanced",
                "Startup, telemetry, skills, warnings, voice, and web tools.",
                [
                    "quiet-startup",
                    "collapse-changelog",
                    "install-telemetry",
                    "skill-commands",
                    "warnings",
                    "voice-silence-ms",
                    "webtools-timeout-secs",
                ],
            ),
        ]

        self.settings_list = SettingsList(
            top_items,
            min(len(top_items), 10),
            get_settings_list_theme(),
            apply_change,
            callbacks.on_cancel,
            SettingsListOptions(enable_search=True),
        )

        self.add_child(self.settings_list)
        self.add_child(DynamicBorder())

    def handle_input(self, data: str) -> None:
        self.settings_list.handle_input(data)

    def get_settings_list(self) -> SettingsList:
        return self.settings_list

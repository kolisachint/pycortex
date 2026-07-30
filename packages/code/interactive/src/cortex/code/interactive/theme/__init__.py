"""Theme palette for the interactive surfaces."""

from cortex.code.interactive.theme.theme import (
    ColorMode,
    SetThemeResult,
    Theme,
    detect_color_mode,
    get_available_themes,
    get_editor_theme,
    get_markdown_theme,
    get_select_list_theme,
    get_settings_list_theme,
    get_theme,
    get_theme_name,
    hex_to_256,
    hex_to_rgb,
    load_builtin_theme,
    set_theme,
    set_theme_instance,
)

__all__ = [
    "ColorMode",
    "SetThemeResult",
    "Theme",
    "detect_color_mode",
    "get_available_themes",
    "get_editor_theme",
    "get_markdown_theme",
    "get_select_list_theme",
    "get_settings_list_theme",
    "get_theme",
    "get_theme_name",
    "hex_to_256",
    "hex_to_rgb",
    "load_builtin_theme",
    "set_theme",
    "set_theme_instance",
]

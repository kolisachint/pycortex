"""Tests for the theme palette."""

from __future__ import annotations

import pytest
from cortex.code.interactive.theme import (
    Theme,
    detect_color_mode,
    get_editor_theme,
    get_select_list_theme,
    get_theme,
    hex_to_256,
    hex_to_rgb,
    load_builtin_theme,
)


class TestColorMode:
    def test_colorterm_wins(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("COLORTERM", "truecolor")
        monkeypatch.setenv("TERM", "dumb")
        assert detect_color_mode() == "truecolor"

    @pytest.mark.parametrize("term", ["dumb", "", "linux", "screen", "screen-256color"])
    def test_limited_terminals_fall_back(self, term: str, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("COLORTERM", raising=False)
        monkeypatch.delenv("WT_SESSION", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.setenv("TERM", term)
        assert detect_color_mode() == "256color"

    def test_apple_terminal_falls_back(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("COLORTERM", raising=False)
        monkeypatch.delenv("WT_SESSION", raising=False)
        monkeypatch.setenv("TERM", "xterm-256color")
        monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
        assert detect_color_mode() == "256color"

    def test_modern_terminals_get_truecolor(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("COLORTERM", raising=False)
        monkeypatch.delenv("WT_SESSION", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.setenv("TERM", "xterm-256color")
        assert detect_color_mode() == "truecolor"


class TestHexConversion:
    def test_hex_to_rgb(self):
        assert hex_to_rgb("#5cc8bb") == (0x5C, 0xC8, 0xBB)
        assert hex_to_rgb("5cc8bb") == (0x5C, 0xC8, 0xBB)

    @pytest.mark.parametrize("bad", ["#abc", "", "#12345g"])
    def test_bad_hex_raises(self, bad: str):
        with pytest.raises(ValueError):
            hex_to_rgb(bad)

    def test_neutral_greys_land_on_the_grayscale_ramp(self):
        # #666666 is neutral (spread 0), so the ramp beats the colour cube.
        assert 232 <= hex_to_256("#666666") <= 255

    def test_saturated_colours_keep_their_tint(self):
        # A tinted colour must stay in the 6x6x6 cube even when a grey is nearer
        # in raw distance — that is what the `spread < 10` guard is for, and
        # #3a3a4a (the palette's own selectedBg) is exactly that case: the ramp
        # would win on distance alone, and picking it would drain the blue out
        # of every selected row.
        assert hex_to_256("#3a3a4a") == 59


class TestTheme:
    def test_fg_wraps_with_a_foreground_reset(self):
        theme = Theme({"accent": "#5cc8bb"}, {}, "truecolor")
        assert theme.fg("accent", "hi") == "\x1b[38;2;92;200;187mhi\x1b[39m"

    def test_bg_wraps_with_a_background_reset(self):
        theme = Theme({}, {"selectedBg": "#3a3a4a"}, "truecolor")
        assert theme.bg("selectedBg", "hi") == "\x1b[48;2;58;58;74mhi\x1b[49m"

    def test_256_mode_emits_indexed_colour(self):
        theme = Theme({"accent": "#5cc8bb"}, {}, "256color")
        assert theme.fg("accent", "hi").startswith("\x1b[38;5;")

    def test_empty_colour_is_the_default_foreground(self):
        theme = Theme({"text": ""}, {}, "truecolor")
        assert theme.fg("text", "hi") == "\x1b[39mhi\x1b[39m"

    def test_unknown_tokens_raise(self):
        theme = Theme({"accent": "#5cc8bb"}, {"selectedBg": "#3a3a4a"}, "truecolor")
        with pytest.raises(ValueError, match="Unknown theme color"):
            theme.fg("nope", "hi")
        with pytest.raises(ValueError, match="Unknown theme background color"):
            theme.bg("nope", "hi")

    def test_attribute_helpers_close_only_their_own_attribute(self):
        theme = Theme({}, {}, "truecolor")
        assert theme.bold("x") == "\x1b[1mx\x1b[22m"
        assert theme.blink("x") == "\x1b[5mx\x1b[25m"
        assert theme.italic("x") == "\x1b[3mx\x1b[23m"
        assert theme.underline("x") == "\x1b[4mx\x1b[24m"


class TestBuiltinTheme:
    def test_dark_resolves_variable_references(self):
        theme = load_builtin_theme("dark", "truecolor")
        assert theme.name == "dark"
        # `muted` is the var `gray` = #808080, not the literal string "gray".
        assert theme.fg("muted", "x") == "\x1b[38;2;128;128;128mx\x1b[39m"

    def test_background_tokens_land_on_the_background_map(self):
        theme = load_builtin_theme("dark", "truecolor")
        assert theme.bg("selectedBg", "x").startswith("\x1b[48;")
        # …and are not also offered as foregrounds, as in the TS split.
        with pytest.raises(ValueError):
            theme.fg("selectedBg", "x")

    def test_the_tokens_the_shell_paints_with_all_exist(self):
        theme = load_builtin_theme("dark", "truecolor")
        for token in ("accent", "dim", "muted", "text", "error", "warning", "borderMuted"):
            assert theme.fg(token, "x")

    def test_get_theme_is_cached(self):
        assert get_theme() is get_theme()

    def test_derived_component_themes_colourise(self):
        assert get_select_list_theme().selected_text("x") != "x"
        assert get_editor_theme().border_color("x") != "x"

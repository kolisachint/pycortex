"""Tests for the interactive-mode shell.

These drive the app through a fake terminal rather than the end-to-end harness:
`cortex.code.e2e` depends on this leaf, so depending on it back — even only for
tests — would put a cycle in the workspace graph. The screen-level assertions
live over there, in the corpus; what is checked here is the wiring the corpus
cannot see from the outside.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from cortex.code.config import APP_TITLE, SettingsManager
from cortex.code.config.settings_storage import InMemorySettingsStorage
from cortex.code.interactive import (
    InteractiveMode,
    InteractiveModeOptions,
    build_app_root,
    format_display_path,
)
from cortex.tui.render import TUI
from cortex.tui.terminal import Terminal


class FakeTerminal(Terminal):
    """A terminal that records instead of writing anywhere."""

    def __init__(self, columns: int = 80, rows: int = 24) -> None:
        self._columns = columns
        self._rows = rows
        self.out: list[str] = []
        self.titles: list[str] = []
        self.progress: list[bool] = []
        self.drained = 0
        self.started = False
        self.stopped = False
        self.cursor_hidden: bool | None = None
        self._on_input: Callable[[str], None] | None = None

    @property
    def columns(self) -> int:
        return self._columns

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def kitty_protocol_active(self) -> bool:
        return False

    def start(self, on_input: Callable[[str], None], on_resize: Callable[[], None]) -> None:
        self.started = True
        self._on_input = on_input

    def stop(self) -> None:
        self.stopped = True

    def drain_input(self, max_ms: float = 1000, idle_ms: float = 50) -> None:
        self.drained += 1

    def write(self, data: str) -> None:
        self.out.append(data)

    def move_by(self, lines: int) -> None:
        pass

    def hide_cursor(self) -> None:
        self.cursor_hidden = True

    def show_cursor(self) -> None:
        self.cursor_hidden = False

    def clear_line(self) -> None:
        pass

    def clear_from_cursor(self) -> None:
        pass

    def clear_screen(self) -> None:
        pass

    def set_title(self, title: str) -> None:
        self.titles.append(title)

    def set_progress(self, active: bool) -> None:
        self.progress.append(active)

    def send_input(self, data: str) -> None:
        assert self._on_input is not None, "terminal was never started"
        self._on_input(data)


def _settings() -> SettingsManager:
    return SettingsManager.from_storage(InMemorySettingsStorage())


def _app(terminal: FakeTerminal | None = None, **overrides: object) -> InteractiveMode:
    options: dict[str, object] = {"settings": _settings(), "cwd": "/w/project"}
    options.update(overrides)
    return build_app_root(TUI(terminal or FakeTerminal()), **options)


class TestFormatDisplayPath:
    def test_home_becomes_a_tilde(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HOME", "/home/dev")
        assert format_display_path("/home/dev/code") == "~/code"

    def test_other_paths_are_untouched(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HOME", "/home/dev")
        assert format_display_path("/srv/code") == "/srv/code"


class TestBuildAppRoot:
    def test_returns_an_initialised_app(self):
        app = _app()
        assert isinstance(app, InteractiveMode)
        assert app.editor.prompt_prefix == ">"

    def test_focuses_the_editor(self):
        app = _app()
        assert app.editor.focused is True

    def test_lays_the_bands_out_in_the_ts_order(self):
        app = _app()
        assert app.ui.children == [
            app.header_container,
            app.chat_container,
            app.pending_messages_container,
            app.status_container,
            app.editor_container,
            app.footer,
        ]

    def test_does_not_start_the_tui(self):
        terminal = FakeTerminal()
        _app(terminal)
        assert terminal.started is False

    def test_is_idempotent(self):
        app = _app()
        app.init()
        assert len(app.ui.children) == 6

    def test_sets_the_terminal_title_to_the_cwd_basename(self):
        terminal = FakeTerminal()
        _app(terminal)
        assert terminal.titles == [f"{APP_TITLE} - project"]

    def test_footer_reports_the_cwd(self):
        app = _app()
        assert app.footer.state.cwd == "/w/project"


class TestBanner:
    def test_wide_terminals_get_the_compact_wordmark(self):
        app = _app(FakeTerminal(columns=80))
        assert "▟▀▀▀▀▀▙" in app.build_banner()

    def test_narrow_terminals_fall_back_to_a_one_liner(self):
        app = _app(FakeTerminal(columns=39))
        banner = app.build_banner()
        assert "▟▀▀▀▀▀▙" not in banner
        assert "hoocode" in banner
        assert "v" in banner

    def test_quiet_startup_renders_an_empty_header(self):
        settings = _settings()
        settings.set_quiet_startup(True)
        app = _app(settings=settings)
        assert app.built_in_header is not None
        assert app.built_in_header.render(80) == []

    def test_verbose_overrides_quiet_startup(self):
        settings = _settings()
        settings.set_quiet_startup(True)
        app = _app(settings=settings, verbose=True)
        assert app.built_in_header is not None
        assert any("▟▀▀▀▀▀▙" in line for line in app.built_in_header.render(80))


class TestCtrlC:
    def test_first_press_clears_the_editor_and_does_not_exit(self):
        exits: list[int] = []
        app = _app(on_exit=exits.append)
        app.editor.set_text("half a thought")
        app.handle_ctrl_c()
        assert app.editor.get_text() == ""
        assert exits == []

    def test_second_press_inside_the_window_exits(self):
        exits: list[int] = []
        app = _app(on_exit=exits.append)
        app.handle_ctrl_c()
        app.handle_ctrl_c()
        assert exits == [0]

    def test_a_late_second_press_only_clears_again(self):
        exits: list[int] = []
        app = _app(on_exit=exits.append)
        app.handle_ctrl_c()
        # Push the first press outside the 500 ms window.
        app._last_sigint_time -= 600  # pyright: ignore[reportPrivateUsage]
        app.handle_ctrl_c()
        assert exits == []

    def test_arrives_through_the_terminal(self):
        exits: list[int] = []
        terminal = FakeTerminal()
        app = _app(terminal, on_exit=exits.append)
        app.ui.start()
        terminal.send_input("\x03")
        terminal.send_input("\x03")
        assert exits == [0]

    def test_ctrl_c_never_reaches_the_editor(self):
        terminal = FakeTerminal()
        app = _app(terminal)
        app.ui.start()
        app.editor.set_text("keep")
        terminal.send_input("\x03")
        # Cleared by the app, not inserted as a control character by the editor.
        assert app.editor.get_text() == ""

    def test_ordinary_keys_still_reach_the_editor(self):
        terminal = FakeTerminal()
        app = _app(terminal)
        app.ui.start()
        terminal.send_input("h")
        terminal.send_input("i")
        assert app.editor.get_text() == "hi"

    def test_the_key_is_consumed_rather_than_passed_on(self):
        """Nothing downstream of the app's listener sees Ctrl+C.

        Today the base editor happens to ignore it, so dropping the `consume`
        would look harmless from the screen. It is not: listeners run in order,
        and 7.3's editor binds `app.clear` for real.
        """
        seen: list[str] = []
        terminal = FakeTerminal()
        app = _app(terminal)
        app.ui.add_input_listener(lambda data: seen.append(data) or None)
        app.ui.start()
        terminal.send_input("\x03")
        terminal.send_input("x")
        assert seen == ["x"], f"Ctrl+C reached a later listener: {seen!r}"


class TestShutdown:
    def test_restores_the_terminal(self):
        terminal = FakeTerminal()
        app = _app(terminal)
        app.ui.start()
        assert terminal.cursor_hidden is True
        app.shutdown()
        assert terminal.stopped is True
        assert terminal.cursor_hidden is False

    def test_drains_input_before_stopping(self):
        terminal = FakeTerminal()
        app = _app(terminal)
        app.ui.start()
        app.shutdown()
        assert terminal.drained == 1

    def test_is_idempotent(self):
        exits: list[int] = []
        app = _app(on_exit=exits.append)
        app.ui.start()
        app.shutdown()
        app.shutdown()
        assert exits == [0]

    def test_carries_the_exit_code(self):
        exits: list[int] = []
        app = _app(on_exit=exits.append)
        app.shutdown(3)
        assert exits == [3]

    def test_stops_listening_for_input(self):
        """A stopped app does not act on keys that arrive after it let go.

        Watching `on_exit` cannot see this — the shutdown guard would swallow a
        second call anyway — so the assertion is on Ctrl+C's *other* effect:
        with the listener still attached, this clears the editor.
        """
        terminal = FakeTerminal()
        app = _app(terminal)
        app.ui.start()
        app.editor.set_text("survives")
        app.shutdown()
        terminal.send_input("\x03")
        assert app.editor.get_text() == "survives", "the app kept handling keys after shutting down"


class TestRun:
    async def test_returns_the_code_shutdown_was_given(self):
        import asyncio

        app = _app()
        task = asyncio.ensure_future(app.run())
        await asyncio.sleep(0)
        app.shutdown(0)
        assert await task == 0

    async def test_returns_immediately_if_shutdown_beat_the_loop(self):
        app = _app()
        app.shutdown(2)
        assert await app.run() == 2

    async def test_starts_the_tui(self):
        import asyncio

        terminal = FakeTerminal()
        app = _app(terminal)
        task = asyncio.ensure_future(app.run())
        await asyncio.sleep(0)
        assert terminal.started is True
        app.shutdown()
        await task


class TestOptions:
    def test_defaults_are_empty(self):
        options = InteractiveModeOptions()
        assert options.migrated_providers == []
        assert options.initial_messages == []
        assert options.verbose is False
        assert options.on_exit is None

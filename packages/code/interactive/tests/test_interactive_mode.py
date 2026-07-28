"""Tests for the interactive-mode shell.

These drive the app through a fake terminal rather than the end-to-end harness:
`cortex.code.e2e` depends on this leaf, so depending on it back — even only for
tests — would put a cycle in the workspace graph. The screen-level assertions
live over there, in the corpus; what is checked here is the wiring the corpus
cannot see from the outside.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from cortex.code.config import APP_TITLE, SettingsManager
from cortex.code.config.settings_storage import InMemorySettingsStorage
from cortex.code.interactive import (
    InteractiveMode,
    InteractiveModeOptions,
    KeybindingsManager,
    build_app_root,
    format_display_path,
)
from cortex.code.interactive.theme import get_markdown_theme
from cortex.tui.keys import set_keybindings
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
        # `ProcessTerminal.stop` destroys its stdin reader; without the same here
        # a shut-down app would keep being handed keys no real terminal sends.
        self._on_input = None

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
        assert self._on_input is not None, "terminal is not delivering input"
        self._on_input(data)


def _settings() -> SettingsManager:
    return SettingsManager.from_storage(InMemorySettingsStorage())


def _app(terminal: FakeTerminal | None = None, **overrides: object) -> InteractiveMode:
    # Keybindings come from an empty config for the same reason settings do: the
    # defaults are the contract, and the machine running the tests may not use them.
    options: dict[str, object] = {
        "settings": _settings(),
        "keybindings": KeybindingsManager(),
        "cwd": "/w/project",
    }
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

    def test_it_arrives_as_the_editors_app_clear_action(self):
        """Ctrl+C is the editor's `app.clear` binding, not a listener on the TUI.

        The distinction is not cosmetic: an app-level listener sees every key
        before the focused component does, so it would fire on Ctrl+C even while
        an overlay owned the keyboard (7.9). Rebinding `app.clear` is enough to
        prove where the handler hangs — the app must follow the binding.
        """
        terminal = FakeTerminal()
        app = _app(terminal, keybindings=KeybindingsManager({"app.clear": "ctrl+g"}))
        app.ui.start()
        app.editor.set_text("keep")

        terminal.send_input("\x03")
        assert app.editor.get_text() == "keep", "Ctrl+C still cleared after being rebound"
        terminal.send_input("\x07")
        assert app.editor.get_text() == "", "the rebound key did not reach `app.clear`"


class TestCtrlD:
    def test_an_empty_editor_exits(self):
        exits: list[int] = []
        terminal = FakeTerminal()
        app = _app(terminal, on_exit=exits.append)
        app.ui.start()
        terminal.send_input("\x04")
        assert exits == [0]

    def test_a_non_empty_editor_does_not(self):
        """Ctrl+D on text is delete-forward, which is why `CustomEditor` guards it."""
        exits: list[int] = []
        terminal = FakeTerminal()
        app = _app(terminal, on_exit=exits.append)
        app.ui.start()
        app.editor.set_text("still writing")
        terminal.send_input("\x04")
        assert exits == []


class TestKeybindings:
    def test_the_apps_bindings_become_the_process_wide_ones(self):
        """The base editor resolves `tui.*` bindings through the global manager.

        So an app that keeps its bindings to itself would honour a user's
        `app.clear` override and silently ignore their `tui.input.submit` one.
        Rebinding submit is what tells the two apart.
        """
        terminal = FakeTerminal()
        app = _app(terminal, keybindings=KeybindingsManager({"tui.input.submit": "ctrl+s"}))
        try:
            app.ui.start()
            app.editor.set_text("hello")
            terminal.send_input("\r")
            assert app.editor.get_text() == "hello", "Enter submitted after being rebound"
            terminal.send_input("\x13")
            assert app.editor.get_text() == "", "the rebound key did not submit"
        finally:
            set_keybindings(KeybindingsManager())


class TestSubmit:
    def test_a_submission_draws_nothing_by_itself(self):
        """7.4 moved the drawing onto the session's `user` message event.

        Until the session has accepted the turn there is nothing to show: a
        message the session refuses (no model, no key) must not appear in the log
        as though it had been sent.
        """
        app = _app()
        app.handle_submit("what does this repo do?")
        assert app.chat_container.children == []

    def test_later_messages_are_separated_by_a_blank_line(self):
        app = _app()
        app.render_user_message("one")
        app.render_user_message("two")
        assert [type(child).__name__ for child in app.chat_container.children] == [
            "UserMessageComponent",
            "Spacer",
            "UserMessageComponent",
        ]

    async def test_surrounding_whitespace_is_trimmed(self):
        app = _app()
        pending = asyncio.ensure_future(app.get_user_input())
        await asyncio.sleep(0)
        app.handle_submit("  padded  ")
        assert await pending == "padded"

    def test_an_empty_submission_is_ignored(self):
        app = _app()
        app.handle_submit("   \n  ")
        assert app.chat_container.children == []

    def test_enter_submits_and_clears_the_editor(self):
        terminal = FakeTerminal()
        app = _app(terminal)
        app.ui.start()
        for char in "hello":
            terminal.send_input(char)
        assert app.editor.get_text() == "hello"
        terminal.send_input("\r")
        assert app.editor.get_text() == ""
        # The text left the editor for the message loop; nothing is drawn until
        # the session turns it into a `user` message event.
        assert app.chat_container.children == []
        assert app.is_busy() is False, "there was no waiter, so no turn is pending"

    def test_shift_enter_opens_a_line_instead_of_submitting(self):
        terminal = FakeTerminal()
        app = _app(terminal)
        app.ui.start()
        for char in "first":
            terminal.send_input(char)
        terminal.send_input("\x1b[13;2u")
        for char in "second":
            terminal.send_input(char)
        assert app.editor.get_text() == "first\nsecond"
        assert app.chat_container.children == [], "Shift+Enter submitted"

    def test_a_submission_is_recalled_by_the_up_arrow(self):
        terminal = FakeTerminal()
        app = _app(terminal)
        app.ui.start()
        app.editor.set_text("remember me")
        terminal.send_input("\r")
        terminal.send_input("\x1b[A")
        assert app.editor.get_text() == "remember me"


class TestGetUserInput:
    def test_a_waiter_is_served_once(self):
        """The TS clears `onInputCallback` *before* resolving, and so does this.

        Through `get_user_input` the difference is invisible — its future
        ignores a second result — so the callback is set the way the TS sets it,
        which is the form 7.4's run loop will use.
        """
        app = _app()
        served: list[str] = []
        app._on_input_callback = served.append  # pyright: ignore[reportPrivateUsage]
        app.handle_submit("one")
        app.handle_submit("two")
        assert served == ["one"], f"the waiter was served again: {served!r}"

    async def test_resolves_with_the_next_submission(self):
        app = _app()
        pending = asyncio.ensure_future(app.get_user_input())
        await asyncio.sleep(0)
        app.handle_submit("hello")
        assert await pending == "hello"

    async def test_only_the_waiter_that_asked_is_served(self):
        """The TS clears `onInputCallback` as it resolves; a second line needs a
        second `getUserInput()`, which is what makes the run loop a loop."""
        app = _app()
        first = asyncio.ensure_future(app.get_user_input())
        await asyncio.sleep(0)
        app.handle_submit("one")
        assert await first == "one"

        second = asyncio.ensure_future(app.get_user_input())
        await asyncio.sleep(0)
        app.handle_submit("two")
        assert await second == "two"


class TestMarkdownTheme:
    def test_takes_the_code_block_indent_from_settings(self):
        """The palette has no opinion on the indent; the settings do."""
        app = _app()
        assert get_markdown_theme().code_block_indent is None
        assert (
            app.get_markdown_theme_with_settings().code_block_indent
            == app.settings_manager.get_code_block_indent()
        )


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
        """A stopped app does not act on keys, because none are delivered to it.

        Ctrl+C hangs off the editor now, and the editor is still focused after
        shutdown; what ends the app's hold on the keyboard is the terminal, whose
        `stop()` tears the stdin reader down. The fake mirrors that, so a key
        pressed after shutdown has nowhere to go.
        """
        terminal = FakeTerminal()
        app = _app(terminal)
        app.ui.start()
        app.editor.set_text("survives")
        app.shutdown()
        with pytest.raises(AssertionError, match="not delivering input"):
            terminal.send_input("\x03")
        assert app.editor.get_text() == "survives", "the app kept handling keys after shutting down"


class TestRun:
    async def test_returns_the_code_shutdown_was_given(self):
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


# ===========================================================================
# 7.4 — the session bridge
# ===========================================================================


def _chat_text(app: InteractiveMode, width: int = 80) -> str:
    """Everything the chat log would paint, as text."""
    lines: list[str] = []
    for child in app.chat_container.children:
        lines.extend(child.render(width))
    return "\n".join(lines)


def _faux_app(terminal: FakeTerminal | None = None, **overrides: object):
    """An app whose session answers from `ai/provider-faux` instead of the network.

    Returns the app and the registration, so a test can queue the next response
    and unregister the provider when it is done — the api registry is global.
    """
    from cortex.ai.providers.faux import register_faux_provider
    from cortex.code.session import SessionManager, create_agent_session

    registration = register_faux_provider()
    settings = _settings()
    created = create_agent_session(
        cwd="/w/project",
        settings_manager=settings,
        session_manager=SessionManager("/w/project", "", persist=False),
        model=registration.get_model(),
    )
    app = _app(terminal, settings=settings, session=created.session, **overrides)
    return app, registration


class TestSessionBridge:
    def test_an_app_without_a_session_builds_one(self):
        """The TS is always handed a session; a test-booted app makes its own."""
        app = _app()
        assert app.session is not None
        assert app.session.session_file is None, "the fallback session wrote a session file"

    def test_the_app_subscribes_to_the_session(self):
        app = _app()
        assert app.session._event_listeners, "nothing is listening to the session"  # pyright: ignore[reportPrivateUsage]

    def test_stopping_unsubscribes(self):
        app = _app()
        app.stop()
        assert app.session._event_listeners == []  # pyright: ignore[reportPrivateUsage]

    async def test_a_submission_reaches_the_session(self):
        app, registration = _faux_app()
        try:
            from cortex.ai.providers.faux import faux_assistant_message

            registration.set_responses([faux_assistant_message("pong")])
            loop_task = asyncio.ensure_future(app.message_loop())
            await asyncio.sleep(0)
            app.handle_submit("ping")
            for _ in range(200):
                if not app.is_busy():
                    break
                await asyncio.sleep(0)
            assert [getattr(m, "role", "") for m in app.session.messages] == ["user", "assistant"]
            rendered = [type(child).__name__ for child in app.chat_container.children]
            assert rendered == ["UserMessageComponent", "Spacer", "Text"], rendered
            loop_task.cancel()
        finally:
            registration.unregister()

    async def test_a_refused_turn_becomes_an_error_line(self):
        """No model, no key: the loop catches it and shows the guidance."""
        app = _app()
        loop_task = asyncio.ensure_future(app.message_loop())
        await asyncio.sleep(0)
        app.handle_submit("anything")
        for _ in range(20):
            if not app.is_busy():
                break
            await asyncio.sleep(0)
        assert app.chat_container.children, "nothing was drawn for the refused turn"
        assert app.is_busy() is False, "the pending turn was never cleared"
        loop_task.cancel()

    def test_is_busy_covers_the_gap_before_the_turn_starts(self):
        app = _app()
        served: list[str] = []
        app._on_input_callback = served.append  # pyright: ignore[reportPrivateUsage]
        assert app.is_busy() is False
        app.handle_submit("go")
        assert app.is_busy() is True, "a delivered submission left the app looking idle"


class TestSessionEvents:
    def test_a_user_message_event_draws_the_message(self):
        from cortex.ai.types import TextContent, UserMessage

        app = _app()
        message = UserMessage(content=[TextContent(text="hello there")], timestamp=0)
        app.handle_session_event({"type": "message_start", "message": message})
        assert [type(c).__name__ for c in app.chat_container.children] == ["UserMessageComponent"]

    def test_an_assistant_message_end_draws_its_text(self):
        from cortex.ai.providers.faux import faux_assistant_message

        app = _app()
        app.handle_session_event(
            {"type": "message_end", "message": faux_assistant_message("the answer")}
        )
        assert "the answer" in _chat_text(app), "the assistant text is not in the chat log"

    def test_an_error_turn_draws_its_error_message(self):
        from cortex.ai.providers.faux import faux_assistant_message

        app = _app()
        app.handle_session_event(
            {
                "type": "message_end",
                "message": faux_assistant_message(
                    "", stop_reason="error", error_message="Provider is overloaded"
                ),
            }
        )
        assert "Provider is overloaded" in _chat_text(app)

    def test_an_error_turn_with_no_message_still_says_something(self):
        from cortex.ai.providers.faux import faux_assistant_message

        app = _app()
        app.handle_session_event(
            {"type": "message_end", "message": faux_assistant_message("", stop_reason="error")}
        )
        assert "Error" in _chat_text(app)

    def test_an_aborted_turn_says_so(self):
        from cortex.ai.providers.faux import faux_assistant_message

        app = _app()
        app.handle_session_event(
            {
                "type": "message_end",
                "message": faux_assistant_message("half", stop_reason="aborted"),
            }
        )
        assert "half" in _chat_text(app), "the partial answer was thrown away"
        assert "Operation aborted" in _chat_text(app)

    def test_a_user_message_end_is_not_drawn_twice(self):
        from cortex.ai.types import TextContent, UserMessage

        app = _app()
        message = UserMessage(content=[TextContent(text="once")], timestamp=0)
        app.handle_session_event({"type": "message_start", "message": message})
        app.handle_session_event({"type": "message_end", "message": message})
        assert len(app.chat_container.children) == 1

    def test_the_turn_raises_and_lowers_the_terminal_progress_indicator(self):
        terminal = FakeTerminal()
        settings = _settings()
        settings.set_show_terminal_progress(True)
        app = _app(terminal, settings=settings)
        app.handle_session_event({"type": "agent_start"})
        app.handle_session_event({"type": "agent_end"})
        assert terminal.progress == [True, False]

    def test_the_progress_indicator_is_off_by_default(self):
        """`showTerminalProgress` defaults to false in the TS, and the app asks."""
        terminal = FakeTerminal()
        app = _app(terminal)
        app.handle_session_event({"type": "agent_start"})
        app.handle_session_event({"type": "agent_end"})
        assert terminal.progress == []


class TestEscape:
    async def test_escape_aborts_an_in_flight_turn(self):
        app, registration = _faux_app()
        try:
            released = asyncio.Event()

            async def slow(*_args: object):
                from cortex.ai.providers.faux import faux_assistant_message

                await released.wait()
                return faux_assistant_message("too late")

            registration.set_responses([slow])
            turn = asyncio.ensure_future(app.session.prompt("wait for it"))
            for _ in range(50):
                if app.session.is_streaming:
                    break
                await asyncio.sleep(0)
            assert app.session.is_streaming, "the turn never started"

            app.handle_escape()
            released.set()
            await turn
            assert not app.session.is_streaming
            assert getattr(app.session.messages[-1], "stop_reason", "") == "aborted"
        finally:
            registration.unregister()

    async def test_escape_does_nothing_when_no_turn_is_running(self):
        """Idle Escape belongs to 7.9's double-escape, not to the abort path."""
        app, registration = _faux_app()
        try:
            app.editor.set_text("draft")
            await app.session.steer("queued for the next turn")

            app.handle_escape()

            assert app.editor.get_text() == "draft", "Escape rewrote the editor"
            assert app.session.get_steering_messages() == ["queued for the next turn"], (
                "Escape emptied the queue with no turn to abort"
            )
        finally:
            registration.unregister()

    async def test_escape_puts_queued_messages_back_in_the_editor(self):
        app, registration = _faux_app()
        try:
            released = asyncio.Event()

            async def slow(*_args: object):
                from cortex.ai.providers.faux import faux_assistant_message

                await released.wait()
                return faux_assistant_message("done")

            registration.set_responses([slow])
            turn = asyncio.ensure_future(app.session.prompt("first"))
            for _ in range(50):
                if app.session.is_streaming:
                    break
                await asyncio.sleep(0)
            await app.session.steer("queued while busy")

            app.handle_escape()
            assert "queued while busy" in app.editor.get_text()
            released.set()
            await turn
        finally:
            registration.unregister()

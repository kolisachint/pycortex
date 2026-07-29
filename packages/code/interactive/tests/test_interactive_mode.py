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
from cortex.code.interactive.components import ToolExecutionComponent
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
        # The footer reads the cwd off the session it was handed (7.7), so this
        # is the app wiring the two together rather than a value copied at boot.
        assert "⬢ BUILD" in app.footer.render(80)[0]
        assert "/w/project" in app.footer.render(80)[0]

    def test_the_footer_data_provider_follows_the_session_cwd(self):
        app = _app()
        assert app.footer_data_provider.get_git_branch() is None


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


def _assistant_turn(app: InteractiveMode, message: object) -> None:
    """The event sequence a real assistant turn produces, minus the deltas.

    A finished message reaches the screen through `message_start` (which builds
    the component) and `message_end` (which fills it in). Sending only the end,
    as the tests did before 7.5, tests a path the session never takes.
    """
    app.handle_session_event({"type": "message_start", "message": message})
    app.handle_session_event({"type": "message_end", "message": message})


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
            # No spacer between them: `AssistantMessageComponent` opens with one
            # of its own when it has anything to show, which is why the TS's
            # `addMessageToChat` adds one for a user message and not for this.
            assert rendered == ["UserMessageComponent", "AssistantMessageComponent"], rendered
            assert "pong" in _chat_text(app)
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
        _assistant_turn(app, faux_assistant_message("the answer"))
        assert "the answer" in _chat_text(app), "the assistant text is not in the chat log"

    def test_an_error_turn_draws_its_error_message(self):
        from cortex.ai.providers.faux import faux_assistant_message

        app = _app()
        _assistant_turn(
            app,
            faux_assistant_message("", stop_reason="error", error_message="Provider is overloaded"),
        )
        assert "Provider is overloaded" in _chat_text(app)

    def test_an_error_turn_with_no_message_still_says_something(self):
        from cortex.ai.providers.faux import faux_assistant_message

        app = _app()
        _assistant_turn(app, faux_assistant_message("", stop_reason="error"))
        assert "Error" in _chat_text(app)

    def test_an_aborted_turn_says_so(self):
        from cortex.ai.providers.faux import faux_assistant_message

        app = _app()
        _assistant_turn(app, faux_assistant_message("half", stop_reason="aborted"))
        assert "half" in _chat_text(app), "the partial answer was thrown away"
        assert "Operation aborted" in _chat_text(app)

    def test_an_abort_after_a_retry_counts_the_attempts(self, monkeypatch: pytest.MonkeyPatch):
        """The wording the *app* owns, as opposed to the component's default.

        `AgentSession.retry_attempt` is 0 until the retry controller is wired
        (7.x), so this is the only place the branch can be reached — and without
        it the app would say "Operation aborted" after two silent retries.
        """
        from cortex.ai.providers.faux import faux_assistant_message

        app = _app()
        monkeypatch.setattr(type(app.session), "retry_attempt", property(lambda self: 2))
        _assistant_turn(app, faux_assistant_message("half", stop_reason="aborted"))
        assert "Aborted after 2 retry attempts" in _chat_text(app)

    def test_a_single_retry_is_not_pluralised(self, monkeypatch: pytest.MonkeyPatch):
        from cortex.ai.providers.faux import faux_assistant_message

        app = _app()
        monkeypatch.setattr(type(app.session), "retry_attempt", property(lambda self: 1))
        _assistant_turn(app, faux_assistant_message("half", stop_reason="aborted"))
        assert "Aborted after 1 retry attempt" in _chat_text(app)
        assert "attempts" not in _chat_text(app)

    def test_a_message_end_with_no_streaming_component_draws_nothing(self):
        """`message_end` is the *end* of something that started. It is not a draw.

        The TS guards on `this.streamingComponent`, and it has to: an assistant
        message the app never saw start (a replayed transcript, an event that
        arrived after `agent_end` tore the component down) would otherwise
        appear a second time.
        """
        from cortex.ai.providers.faux import faux_assistant_message

        app = _app()
        app.handle_session_event(
            {"type": "message_end", "message": faux_assistant_message("orphan")}
        )
        assert app.chat_container.children == []

    def test_a_user_message_end_is_not_drawn_twice(self):
        from cortex.ai.types import TextContent, UserMessage

        app = _app()
        message = UserMessage(content=[TextContent(text="once")], timestamp=0)
        app.handle_session_event({"type": "message_start", "message": message})
        app.handle_session_event({"type": "message_end", "message": message})
        assert len(app.chat_container.children) == 1

    def test_a_delta_redraws_the_message_in_place(self):
        """The component is built once and fed; it is not rebuilt per delta."""
        from cortex.ai.providers.faux import faux_assistant_message

        app = _app()
        app.handle_session_event({"type": "message_start", "message": faux_assistant_message("")})
        component = app.chat_container.children[0]
        app.handle_session_event(
            {"type": "message_update", "message": faux_assistant_message("half a")}
        )
        assert "half a" in _chat_text(app)
        app.handle_session_event(
            {"type": "message_update", "message": faux_assistant_message("half a sentence")}
        )
        assert "half a sentence" in _chat_text(app)
        assert app.chat_container.children == [component], "the component was replaced"

    def test_a_delta_after_the_turn_ended_is_ignored(self):
        """`streamingComponent` is the guard, and it is cleared on `message_end`."""
        from cortex.ai.providers.faux import faux_assistant_message

        app = _app()
        _assistant_turn(app, faux_assistant_message("final"))
        app.handle_session_event(
            {"type": "message_update", "message": faux_assistant_message("late delta")}
        )
        assert "late delta" not in _chat_text(app)
        assert "final" in _chat_text(app)

    def test_a_turn_that_produced_nothing_leaves_no_hole(self):
        """`agent_end` drops a streaming component that never got a `message_end`."""
        from cortex.ai.providers.faux import faux_assistant_message

        app = _app()
        app.handle_session_event({"type": "message_start", "message": faux_assistant_message("")})
        assert len(app.chat_container.children) == 1
        app.handle_session_event({"type": "agent_end"})
        assert app.chat_container.children == []

    async def test_the_loader_runs_for_the_turn_and_clears_after(self):
        app = _app()
        app.handle_session_event({"type": "agent_start"})
        assert [type(c).__name__ for c in app.status_container.children] == ["Loader"]
        rendered = "\n".join(app.status_container.children[0].render(80))
        assert "Working..." in rendered
        assert any(frame in rendered for frame in ("⠋", "⠙", "⠹")), rendered

        app.handle_session_event({"type": "agent_end"})
        assert app.status_container.children == []

    async def test_a_hidden_loader_stays_hidden_for_the_next_turn(self):
        """`workingVisible` gates the loader; an extension turns it off."""
        app = _app()
        app.set_working_visible(False)
        app.handle_session_event({"type": "agent_start"})
        assert app.status_container.children == []

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


def _tool_call_message(
    name: str = "grep", args: dict[str, object] | None = None, call_id: str = "call-1"
):
    from cortex.ai.providers.faux import faux_assistant_message, faux_tool_call

    return faux_assistant_message(
        faux_tool_call(name, args if args is not None else {"pattern": "needle"}, {"id": call_id})
    )


def _tool_blocks(app: InteractiveMode) -> list[ToolExecutionComponent]:
    """The tool blocks in the chat log, in order."""
    return [c for c in app.chat_container.children if isinstance(c, ToolExecutionComponent)]


def _tool_result(text: str):
    from cortex.agent.types import AgentToolResult
    from cortex.ai.types import TextContent

    return AgentToolResult(content=[TextContent(text=text)], details=None)


class TestToolBlocks:
    """The `tool_execution_*` branches, and the tool calls inside a message."""

    def test_a_streamed_tool_call_creates_its_block(self):
        app = _app()
        message = _tool_call_message()
        app.handle_session_event({"type": "message_start", "message": message})
        app.handle_session_event({"type": "message_update", "message": message})
        assert list(app.pending_tools) == ["call-1"]
        assert "grep" in _chat_text(app)

    def test_a_second_update_does_not_create_a_second_block(self):
        app = _app()
        message = _tool_call_message()
        app.handle_session_event({"type": "message_start", "message": message})
        app.handle_session_event({"type": "message_update", "message": message})
        app.handle_session_event({"type": "message_update", "message": message})
        assert len(app.pending_tools) == 1
        # The map would still hold one — it is keyed on the call id — while the
        # log grew a block per delta. The chat log is where this shows.
        assert len(_tool_blocks(app)) == 1

    def test_a_later_update_refreshes_the_arguments(self):
        app = _app()
        app.handle_session_event({"type": "message_start", "message": _tool_call_message()})
        app.handle_session_event(
            {"type": "message_update", "message": _tool_call_message(args={"pattern": "a"})}
        )
        app.handle_session_event(
            {"type": "message_update", "message": _tool_call_message(args={"pattern": "abc"})}
        )
        assert '"pattern": "abc"' in _chat_text(app)

    def test_an_execution_start_with_no_block_yet_creates_one(self):
        """A tool the app never saw streamed — a replay, or a background call."""
        app = _app()
        app.handle_session_event(
            {
                "type": "tool_execution_start",
                "tool_call_id": "call-9",
                "tool_name": "read",
                "args": {"path": "x.py"},
            }
        )
        assert list(app.pending_tools) == ["call-9"]
        assert "read" in _chat_text(app)

    def test_a_partial_result_shows_while_the_tool_runs(self):
        app = _app()
        app.handle_session_event(
            {"type": "tool_execution_start", "tool_call_id": "c", "tool_name": "bash", "args": {}}
        )
        app.handle_session_event(
            {
                "type": "tool_execution_update",
                "tool_call_id": "c",
                "partial_result": _tool_result("half the output"),
            }
        )
        assert "half the output" in _chat_text(app)
        assert app.pending_tools["c"].is_partial, "a partial result settled the block"

    def test_the_end_settles_the_block_and_forgets_it(self):
        app = _app()
        app.handle_session_event(
            {"type": "tool_execution_start", "tool_call_id": "c", "tool_name": "bash", "args": {}}
        )
        app.handle_session_event(
            {
                "type": "tool_execution_end",
                "tool_call_id": "c",
                "result": _tool_result("all the output"),
                "is_error": False,
            }
        )
        assert app.pending_tools == {}
        assert "all the output" in _chat_text(app)

    def test_an_end_for_an_unknown_call_is_ignored(self):
        app = _app()
        app.handle_session_event(
            {
                "type": "tool_execution_end",
                "tool_call_id": "nobody",
                "result": _tool_result("x"),
                "is_error": False,
            }
        )
        assert app.chat_container.children == []

    def test_a_finished_message_completes_the_arguments(self):
        app = _app()
        message = _tool_call_message()
        app.handle_session_event({"type": "message_start", "message": message})
        app.handle_session_event({"type": "message_update", "message": message})
        block = app.pending_tools["call-1"]
        assert not block.args_complete
        app.handle_session_event({"type": "message_end", "message": message})
        assert block.args_complete, "the block never learned its arguments were final"

    def test_an_aborted_message_fails_the_tools_it_never_ran(self):
        from cortex.ai.providers.faux import faux_assistant_message, faux_tool_call

        app = _app()
        message = _tool_call_message()
        app.handle_session_event({"type": "message_start", "message": message})
        app.handle_session_event({"type": "message_update", "message": message})
        aborted = faux_assistant_message(
            faux_tool_call("grep", {"pattern": "needle"}, {"id": "call-1"}),
            stop_reason="aborted",
        )
        app.handle_session_event({"type": "message_end", "message": aborted})
        # Left pending, the block would spin a yellow dot for the rest of the
        # session over a tool that is never going to run.
        assert app.pending_tools == {}
        assert "Operation aborted" in _chat_text(app)

    def test_agent_end_drops_any_block_still_pending(self):
        app = _app()
        app.handle_session_event(
            {"type": "tool_execution_start", "tool_call_id": "c", "tool_name": "bash", "args": {}}
        )
        app.handle_session_event({"type": "agent_end"})
        assert app.pending_tools == {}

    def test_blocks_are_created_at_the_current_expansion(self):
        app = _app()
        app.set_tools_expanded(True)
        app.handle_session_event(
            {"type": "tool_execution_start", "tool_call_id": "c", "tool_name": "bash", "args": {}}
        )
        assert app.pending_tools["c"].expanded

    def test_the_display_level_comes_from_settings(self):
        settings = _settings()
        settings.set_tool_output_display("peek")
        app = _app(settings=settings)
        app.handle_session_event(
            {"type": "tool_execution_start", "tool_call_id": "c", "tool_name": "bash", "args": {}}
        )
        assert app.pending_tools["c"].display_level == "peek"

    def test_old_finished_blocks_are_frozen(self):
        from cortex.code.interactive.interactive_mode import LIVE_TOOL_WINDOW

        app = _app()
        for index in range(LIVE_TOOL_WINDOW + 3):
            call_id = f"c{index}"
            app.handle_session_event(
                {
                    "type": "tool_execution_start",
                    "tool_call_id": call_id,
                    "tool_name": "bash",
                    "args": {},
                }
            )
            app.handle_session_event(
                {
                    "type": "tool_execution_end",
                    "tool_call_id": call_id,
                    "result": _tool_result(f"output {index}"),
                    "is_error": False,
                }
            )
        blocks = _tool_blocks(app)
        # The oldest three are past the window; everything nearer the viewport
        # is untouched, which is the whole point of the window being generous.
        assert not blocks[0].is_freezable(), "the oldest block was not frozen"
        assert blocks[-1].is_freezable(), "a block near the viewport was frozen"


class TestToolExpansion:
    def test_ctrl_o_toggles_the_setting(self):
        app = _app()
        assert not app.tool_output_expanded
        app.toggle_tool_output_expansion()
        assert app.tool_output_expanded
        app.toggle_tool_output_expansion()
        assert not app.tool_output_expanded

    def test_the_key_is_wired_to_the_editor(self):
        app = _app()
        assert "app.tools.expand" in app.editor.action_handlers

    def test_ctrl_o_reaches_the_handler_through_the_editor(self):
        terminal = FakeTerminal()
        app = _app(terminal)
        app.ui.start()
        terminal.send_input("\x0f")
        assert app.tool_output_expanded, "ctrl+o did not reach the app"
        app.stop()

    def test_expanding_reaches_every_block_in_the_log(self):
        app = _app()
        for call_id in ("a", "b"):
            app.handle_session_event(
                {
                    "type": "tool_execution_start",
                    "tool_call_id": call_id,
                    "tool_name": "bash",
                    "args": {},
                }
            )
        blocks = _tool_blocks(app)
        app.set_tools_expanded(True)
        assert all(block.expanded for block in blocks)

    def test_expanding_reaches_parked_bash_blocks_too(self):
        from cortex.code.interactive.components import BashExecutionComponent

        app = _app()
        block = BashExecutionComponent("sleep 1", app.ui)
        app.pending_messages_container.add_child(block)
        app.set_tools_expanded(True)
        assert block.expanded
        block.set_complete(0, False)


class TestBashMode:
    def test_a_bang_prefix_runs_a_command_instead_of_prompting(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        app = _app()
        ran: list[tuple[str, bool]] = []
        monkeypatch.setattr(
            app,
            "run_bash_command",
            lambda command, exclude: ran.append((command, exclude)),  # pyright: ignore[reportUnknownLambdaType]
        )
        served: list[str] = []
        app._on_input_callback = served.append  # pyright: ignore[reportPrivateUsage]

        app.handle_submit("!ls -la")

        assert ran == [("ls -la", False)]
        assert served == [], "the command was sent to the model as well"

    def test_a_double_bang_excludes_the_output_from_context(self, monkeypatch: pytest.MonkeyPatch):
        app = _app()
        ran: list[tuple[str, bool]] = []
        monkeypatch.setattr(
            app,
            "run_bash_command",
            lambda command, exclude: ran.append((command, exclude)),  # pyright: ignore[reportUnknownLambdaType]
        )
        app.handle_submit("!!git log")
        assert ran == [("git log", True)]

    def test_a_bare_bang_runs_nothing(self, monkeypatch: pytest.MonkeyPatch):
        app = _app()
        ran: list[object] = []
        monkeypatch.setattr(app, "run_bash_command", lambda *args: ran.append(args))  # pyright: ignore[reportUnknownLambdaType]
        app.handle_submit("!")
        assert ran == []

    def test_the_command_is_remembered_and_the_editor_cleared(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        app = _app()
        monkeypatch.setattr(app, "run_bash_command", lambda *args: None)  # pyright: ignore[reportUnknownLambdaType]
        app.editor.set_text("!ls")
        app.handle_submit("!ls")
        assert app.editor.get_text() == ""
        app.editor.handle_input("\x1b[A")  # up: history recall
        assert app.editor.get_text() == "!ls"

    def test_submitting_flushes_blocks_parked_during_the_last_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from cortex.code.interactive.components import BashExecutionComponent

        app = _app()
        block = BashExecutionComponent("sleep 1", app.ui)
        app.pending_messages_container.add_child(block)
        app.bash_execution.pending_bash_components.append(block)
        app._on_input_callback = lambda _text: None  # pyright: ignore[reportPrivateUsage]

        app.handle_submit("what next?")

        assert block in app.chat_container.children
        assert not app.pending_messages_container.children
        block.set_complete(0, False)

    async def test_a_bash_command_runs_and_lands_in_the_log(self):
        """The whole `!` path against a fake shell — the wiring, end to end."""
        app = _app()

        class Ops:
            def exec(self, command: str, cwd: str, *, on_data: object, **_: object) -> object:
                on_data(b"from the shell\n")  # type: ignore[operator]

                class Result:
                    exit_code = 0

                return Result()

        async def execute_bash(
            command: str, on_chunk: Callable[[str], None] | None = None, **kwargs: object
        ):
            from cortex.code.session.bash_executor import execute_bash_with_operations

            return await execute_bash_with_operations(
                command, "/w/project", Ops(), on_chunk=on_chunk
            )

        app.session.execute_bash = execute_bash  # type: ignore[method-assign]

        app.handle_submit("!echo hi")
        # The app has to look busy from the moment the command is scheduled, or
        # a driver that pumps once and asks would stop before it started.
        assert app.is_busy(), "a scheduled bash command left the app looking idle"
        assert app._bash_task is not None  # pyright: ignore[reportPrivateUsage]
        await app._bash_task  # pyright: ignore[reportPrivateUsage]

        assert "from the shell" in _chat_text(app)
        assert "$ echo hi" in _chat_text(app)
        assert not app.is_busy()

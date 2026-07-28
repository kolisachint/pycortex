"""Interactive mode for the coding agent.

Port of ``modes/interactive/interactive-mode.ts`` — the constructor, the
``init()``/``run()``/``stop()`` skeleton (step 7.2) and the input path: the
keybinding-aware editor, its submit handler and the chat log it writes into
(step 7.3). The TS file is 3,528 lines because it is also the tool renderer, the
command executor, the overlay host and the extension chrome; each of those
arrives with the step that has something for it to drive (7.4 onwards). What
lands here is the part that makes ``pycortex`` a program you can use:

* the component tree — header, chat, pending messages, status, editor, footer —
  assembled onto a :class:`~cortex.tui.render.TUI` in the TS's order, because the
  order *is* the layout;
* the startup banner, drawn with :func:`~cortex.code.interactive.wordmark.build_compact_wordmark`;
* focus on the editor, so the caret sits at the ``>`` prompt on the first frame;
* a :class:`~cortex.code.interactive.components.custom_editor.CustomEditor` over
  the app :class:`~cortex.code.interactive.keybindings.KeybindingsManager`, which
  is what turns Ctrl+C into ``app.clear`` and Ctrl+D into ``app.exit`` —
  once clears the editor, twice within 500 ms shuts down and hands the terminal
  back;
* the submit path: Enter submits and clears, the text goes into the editor's
  history, and a :class:`~cortex.code.interactive.components.user_message.UserMessageComponent`
  lands in the chat container.

Step 7.4 closed the loop it was all pointing at: submissions now go to an
:class:`~cortex.code.session.AgentSession` and come back as session events.

* :meth:`InteractiveMode.message_loop` is the TS's "Main interactive loop" —
  ``getUserInput()`` then ``session.prompt()``, with the ``catch`` that turns a
  refused turn into a line of red text instead of a traceback;
* :meth:`InteractiveMode.handle_session_event` is ``handleSessionEvent``, for the
  branches this port has components for: the user message (which the submit
  handler no longer draws itself — it arrives as a ``message_start``, the long
  way round, as in the TS), the finished assistant message, and the terminal's
  progress indicator over the turn;
* Escape aborts an in-flight turn.

The assistant message is the honest shortfall: the TS renders it into an
``AssistantMessageComponent`` that grows as the deltas arrive, and that component
is step **7.5** along with the loader and the markdown styling. Until then a
finished turn appends its text — or its error — as a plain line, so the round
trip is visible without pretending the streaming UI exists.

Shutdown is a callback, not ``process.exit``. The TS exits the process from
inside ``shutdown()``; doing that here would make the exit path the one thing the
end-to-end corpus could never watch. :meth:`InteractiveMode.shutdown` tears down
and resolves an exit code instead, and :func:`run_interactive_mode` is what turns
that code into a process exit.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, cast

from cortex.code.config import APP_NAME, APP_TITLE, VERSION, SettingsManager
from cortex.code.interactive.components.custom_editor import CustomEditor
from cortex.code.interactive.components.footer import FooterComponent, FooterState
from cortex.code.interactive.components.user_message import UserMessageComponent
from cortex.code.interactive.keybindings import KeybindingsManager
from cortex.code.interactive.theme import get_editor_theme, get_markdown_theme, get_theme
from cortex.code.interactive.wordmark import CompactWordmarkOptions, build_compact_wordmark
from cortex.tui.components import EditorOptions, MarkdownTheme, Spacer, Text
from cortex.tui.keys import set_keybindings
from cortex.tui.render import TUI, Container
from cortex.tui.terminal import ProcessTerminal

__all__ = [
    "InteractiveMode",
    "InteractiveModeOptions",
    "build_app_root",
    "format_display_path",
    "run_interactive_mode",
]

#: Two Ctrl+C presses closer together than this exit; further apart, the second
#: is treated as another "clear the editor".
SIGINT_EXIT_WINDOW_MS = 500


class AppTerminal(Protocol):
    """The terminal surface interactive mode uses beyond what the renderer does.

    `TUI.terminal` is typed as `cortex.tui.render.Terminal` — write, start/stop,
    cursor, size — because that is all the renderer needs. The app also names the
    window and drains pending input on the way out. Both live on
    `cortex.tui.terminal.Terminal`, which every real implementation (and the
    end-to-end harness's) subclasses; stating the requirement here is what lets
    the app ask for them without reaching past the renderer's type.
    """

    def set_title(self, title: str) -> None: ...

    def set_progress(self, active: bool) -> None: ...

    def drain_input(self, max_ms: float = 1000, idle_ms: float = 50) -> None: ...


#: Below this width the compact wordmark does not fit, and the TS falls back to
#: the bare app name and version.
MIN_BANNER_COLUMNS = 40


def _role_of(message: Any) -> str:
    """Role of a message, whether it arrived as a model or a plain dict."""
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "role", ""))


def _message_text(message: Any) -> str:
    """The text blocks of a message, joined. Port of the TS's content walk."""
    content = (
        message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    )
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
        if block_type == "text":
            text = block.get("text", "") if isinstance(block, dict) else block.text
            parts.append(str(text))
    return "".join(parts)


def _create_unpersisted_session(cwd: str, settings_manager: SettingsManager) -> Any:
    """A session with no session file and no model, for an app booted without one."""
    from cortex.code.session import SessionManager, create_agent_session

    return create_agent_session(
        cwd=cwd,
        settings_manager=settings_manager,
        session_manager=SessionManager(cwd, "", persist=False),
    ).session


def format_display_path(path: str) -> str:
    """``~``-shorten a path for display. Port of ``resource-display.formatDisplayPath``."""
    home = str(Path.home())
    if path.startswith(home):
        return f"~{path[len(home) :]}"
    return path


@dataclass
class InteractiveModeOptions:
    """Options for :class:`InteractiveMode`.

    The first five mirror ``InteractiveModeOptions`` in the TS. The rest are the
    seams a booted-in-a-test app needs: the TS reaches for ``process.cwd()``,
    the real settings file and ``process.exit`` directly, none of which a
    scenario can supply or observe.
    """

    #: Providers that were migrated to auth.json (shows warning).
    migrated_providers: list[str] = field(default_factory=list)
    #: Warning message if the session model couldn't be restored.
    model_fallback_message: str | None = None
    #: Initial message to send on startup.
    initial_message: str | None = None
    #: Additional messages to send after the initial message.
    initial_messages: list[str] = field(default_factory=list)
    #: Force verbose startup (overrides the quietStartup setting).
    verbose: bool = False

    #: Working directory the banner and footer describe.
    cwd: str | None = None
    #: The session prompts are sent to. The TS is always handed one; when it is
    #: omitted here the app builds an unpersisted, model-less one for itself, so
    #: a booted-in-a-test app neither writes to ``~/.hoocode`` nor talks to a
    #: provider. Pressing Enter on that session shows the ``/login`` guidance,
    #: which is what a fresh install shows too.
    session: Any | None = None
    #: Settings source; a file-backed manager for the cwd when omitted.
    settings: SettingsManager | None = None
    #: Keybindings; the user's ``keybindings.json`` when omitted.
    keybindings: KeybindingsManager | None = None
    #: Called once, with the exit code, when the app has torn itself down.
    on_exit: Callable[[int], None] | None = None


class InteractiveMode:
    """The interactive app: a component tree over a TUI, plus its lifecycle."""

    def __init__(self, ui: TUI, options: InteractiveModeOptions | None = None) -> None:
        self.options = options if options is not None else InteractiveModeOptions()
        self.ui = ui
        self.version = VERSION
        self.cwd = self.options.cwd if self.options.cwd is not None else os.getcwd()
        self.settings_manager = (
            self.options.settings
            if self.options.settings is not None
            else SettingsManager.create(self.cwd)
        )

        self.session = (
            self.options.session
            if self.options.session is not None
            else _create_unpersisted_session(self.cwd, self.settings_manager)
        )

        self.ui.set_clear_on_shrink(self.settings_manager.get_clear_on_shrink())
        self.ui.set_show_hardware_cursor(self.settings_manager.get_show_hardware_cursor())

        self.header_container = Container()
        self.chat_container = Container()
        self.pending_messages_container = Container()
        self.status_container = Container()
        self.editor_container = Container()

        # The app's bindings become the process-wide ones, as `setKeybindings` in
        # the TS does: the base editor resolves `tui.input.submit` and friends
        # through the global, so a user override has to be visible from there too.
        self.keybindings = (
            self.options.keybindings
            if self.options.keybindings is not None
            else KeybindingsManager.create()
        )
        set_keybindings(self.keybindings)

        self.editor = CustomEditor(
            self.ui,
            get_editor_theme(),
            self.keybindings,
            EditorOptions(
                padding_x=self.settings_manager.get_editor_padding_x(),
                autocomplete_max_visible=self.settings_manager.get_autocomplete_max_visible(),
            ),
        )
        self.editor.prompt_prefix = ">"
        self.editor.prompt_color = lambda text: get_theme().fg("accent", text)
        self.editor_container.add_child(self.editor)

        self.footer = FooterComponent(
            FooterState(
                cwd=format_display_path(self.cwd),
                auto_compact_enabled=True,
                compaction_reserve_tokens=self.settings_manager.get_compaction_reserve_tokens(),
            )
        )

        self._is_initialized = False
        self._is_shutting_down = False
        self._last_sigint_time = 0.0
        self._exit_code: int | None = None
        self._exit_waiter: asyncio.Future[int] | None = None
        self._on_input_callback: Callable[[str], None] | None = None
        self.built_in_header: Text | None = None
        self._unsubscribe_session: Callable[[], None] | None = None
        self._message_loop_task: asyncio.Task[None] | None = None
        #: Set the moment a submission is handed to the message loop and cleared
        #: when that turn is over. Between the two the session is not streaming
        #: yet but the app is anything but idle, which is the difference a test
        #: driving the app one keystroke at a time has to be able to see.
        self._turn_pending = False

    @property
    def terminal(self) -> AppTerminal:
        """The TUI's terminal, seen through the app-level surface."""
        return cast(AppTerminal, self.ui.terminal)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def build_banner(self) -> str:
        """The startup header: the compact wordmark, or a one-liner if too narrow.

        The TS wraps this in an `ExpandableText` whose expansion lists the
        keybinding hints. That component is not ported and the key it expands on
        is `app.tools.expand` (Ctrl+O), which 7.6 wires for tool output; the
        collapsed form is all there is to show until then.
        """
        theme = get_theme()
        if self.ui.terminal.columns < MIN_BANNER_COLUMNS:
            return theme.bold(theme.fg("accent", APP_NAME)) + theme.fg("dim", f" v{self.version}")
        return build_compact_wordmark(
            CompactWordmarkOptions(
                app_name=APP_NAME,
                version=self.version,
                cwd=format_display_path(self.cwd),
                accent=lambda text: theme.fg("accent", text),
                dim=lambda text: theme.fg("dim", text),
                muted=lambda text: theme.fg("muted", text),
                cursor=lambda text: theme.blink(theme.fg("accent", text)),
            )
        )

    def init(self) -> None:
        """Assemble the component tree and take the keyboard.

        Does not start the TUI: the caller owns that, because the end-to-end
        harness starts it itself after building the root.
        """
        if self._is_initialized:
            return

        # Order is layout — the TS adds these top to bottom and so does this.
        self.ui.add_child(self.header_container)
        if self.options.verbose or not self.settings_manager.get_quiet_startup():
            self.built_in_header = Text(self.build_banner(), 1, 0)
        else:
            self.built_in_header = Text("", 0, 0)
        self.header_container.add_child(self.built_in_header)

        self.ui.add_child(self.chat_container)
        self.ui.add_child(self.pending_messages_container)
        self.ui.add_child(self.status_container)
        self.ui.add_child(self.editor_container)
        self.ui.add_child(self.footer)
        self.ui.set_focus(self.editor)

        self.setup_key_handlers()
        self.setup_editor_submit_handler()
        self.setup_session_listener()
        self.update_terminal_title()
        self._is_initialized = True

    def setup_session_listener(self) -> None:
        """Subscribe to the session, so its events reach the screen."""
        if self._unsubscribe_session is None:
            self._unsubscribe_session = self.session.subscribe(self.handle_session_event)

    def update_terminal_title(self) -> None:
        """Update terminal title with session name and cwd."""
        cwd_basename = os.path.basename(self.cwd)
        self.terminal.set_title(f"{APP_TITLE} - {cwd_basename}")

    async def run(self) -> int:
        """Run interactive mode until it is asked to exit. The main entry point.

        The TS loops forever on ``getUserInput()`` and lets ``process.exit`` end
        it. Here the loop runs as a task and ``run()`` waits on the exit code
        :meth:`shutdown` resolves, because a mode that exits the process is a
        mode the end-to-end corpus can never watch finish.
        """
        self.init()
        self.ui.start()
        if self._exit_code is not None:
            return self._exit_code
        self._exit_waiter = asyncio.get_running_loop().create_future()
        self.start_message_loop()
        try:
            return await self._exit_waiter
        finally:
            self.stop_message_loop()

    def start_message_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Run :meth:`message_loop` as a task on ``loop`` (the running one by default)."""
        if self._message_loop_task is not None:
            return
        target = loop if loop is not None else asyncio.get_event_loop()
        self._message_loop_task = target.create_task(self.message_loop())

    def stop_message_loop(self) -> None:
        if self._message_loop_task is not None:
            self._message_loop_task.cancel()
            self._message_loop_task = None

    async def message_loop(self) -> None:
        """The TS's main interactive loop: read a line, send it, show what breaks."""
        while True:
            user_input = await self.get_user_input()
            try:
                await self.session.prompt(user_input)
            except Exception as error:  # noqa: BLE001 - the TS catch, one for one
                self.show_error(str(error) or "Unknown error occurred")
            finally:
                self._turn_pending = False

    def is_busy(self) -> bool:
        """Whether a submission is in flight — on its way to the session, or streaming."""
        return self._turn_pending or bool(self.session.is_streaming)

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    def setup_key_handlers(self) -> None:
        """Bind the app actions the editor dispatches.

        The TS registers eighteen of these in ``setupKeyHandlers``; the other
        sixteen drive a model controller, a task panel, selectors and an external
        editor, none of which exist before 7.6–7.9. What is here is what there is
        something to do: clear the editor, exit from an empty one, and abort a
        turn.
        """
        self.editor.on_escape = self.handle_escape
        self.editor.on_action("app.clear", self.handle_ctrl_c)
        self.editor.on_ctrl_d = self.handle_ctrl_d

    def handle_escape(self) -> None:
        """Escape: abort the turn in flight.

        The TS reaches the abort through ``messageQueue.restoreQueuedMessagesToEditor
        ({abort: true})``, which puts the queued-but-undelivered messages back in
        the editor before aborting. The queue display is 7.7's, so what is here
        is the abort and the queue-clearing behind it; the other branches of the
        TS handler (bash mode, double-escape to /tree) belong to 7.6 and 7.9.
        """
        if not self.session.is_streaming:
            return
        queued = self.session.clear_queue()
        pending = [*queued.get("steering", []), *queued.get("follow_up", [])]
        if pending:
            current = self.editor.get_text()
            combined = "\n\n".join(t for t in ["\n\n".join(pending), current] if t.strip())
            self.editor.set_text(combined)
        self.session.agent.abort()
        self.ui.request_render()

    def handle_ctrl_c(self) -> None:
        now = time.time() * 1000
        if now - self._last_sigint_time < SIGINT_EXIT_WINDOW_MS:
            self.shutdown()
        else:
            self.clear_editor()
            self._last_sigint_time = now

    def handle_ctrl_d(self) -> None:
        """Exit. Only reached with an empty editor — :class:`CustomEditor` checks."""
        self.shutdown()

    def clear_editor(self) -> None:
        self.editor.set_text("")
        self.ui.request_render()

    # ------------------------------------------------------------------
    # Submitting
    # ------------------------------------------------------------------

    def setup_editor_submit_handler(self) -> None:
        """Wire Enter to :meth:`handle_submit`.

        The TS builds the slash-command table here and the handler consults it
        before anything else, along with the bash-mode (``!``) prefix, the
        compaction queue and the streaming steer path. Commands are 7.8, bash is
        7.6 and the session — with its queue and its streaming flag — is 7.4; the
        submission itself is all this step owes.
        """
        self.editor.on_submit = self.handle_submit

    def handle_submit(self, text: str) -> None:
        """A submitted line: remember it and hand it to the loop.

        Nothing is drawn here. The text goes to ``session.prompt()``, the session
        emits a ``user`` message event, and :meth:`handle_session_event` draws it
        — the long way round the TS takes, so a message the session refused
        never appears as though it were sent.
        """
        text = text.strip()
        if not text:
            return

        callback = self._on_input_callback
        if callback is not None:
            self._on_input_callback = None
            self._turn_pending = True
            callback(text)
        self.editor.add_to_history(text)

    async def get_user_input(self) -> str:
        """Wait for the next submission. Port of the TS's ``getUserInput``."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        def deliver(text: str) -> None:
            if not future.done():
                future.set_result(text)

        self._on_input_callback = deliver
        return await future

    # ------------------------------------------------------------------
    # Messages on screen
    # ------------------------------------------------------------------

    def get_markdown_theme_with_settings(self) -> MarkdownTheme:
        """The markdown theme, with the user's code-block indent applied."""
        return replace(
            get_markdown_theme(),
            code_block_indent=self.settings_manager.get_code_block_indent(),
        )

    def handle_session_event(self, event: dict[str, Any]) -> None:
        """Draw what the session reports. Port of ``handleSessionEvent``.

        The TS switch has twenty-odd cases. The ones with something to drive here
        are the turn's start and end (the terminal progress indicator), and the
        messages themselves. Tool execution (7.6), the queue display (7.7),
        compaction and auto-retry (their controllers are unwired) are the rest,
        and each arrives with the component that shows it.
        """
        event_type = event.get("type")

        if event_type == "agent_start":
            if self.settings_manager.get_show_terminal_progress():
                self.terminal.set_progress(True)
            self.ui.request_render()
        elif event_type == "agent_end":
            if self.settings_manager.get_show_terminal_progress():
                self.terminal.set_progress(False)
            self.ui.request_render()
        elif event_type == "message_start":
            message = event.get("message")
            if _role_of(message) == "user":
                self.render_user_message(_message_text(message))
        elif event_type == "message_end":
            message = event.get("message")
            if _role_of(message) == "assistant":
                self.render_assistant_message(message)

    def render_assistant_message(self, message: Any) -> None:
        """Append a finished assistant message to the chat log.

        **This is 7.5's component, drawn flat.** The TS builds an
        ``AssistantMessageComponent`` on ``message_start`` and feeds it every
        delta; what a user sees at the end of a turn is the same text, so the
        text is what lands here until that component is ported. The error and
        aborted branches are the TS's, including the wording it puts on an
        aborted turn — which is the only thing on screen that says the Escape
        was heard.
        """
        stop_reason = getattr(message, "stop_reason", None)
        error_message: str | None = None
        if stop_reason == "aborted":
            retry_attempt = self.session.retry_attempt
            error_message = (
                f"Aborted after {retry_attempt} retry attempt{'s' if retry_attempt > 1 else ''}"
                if retry_attempt > 0
                else "Operation aborted"
            )
        elif stop_reason == "error":
            error_message = getattr(message, "error_message", None) or "Error"

        text = _message_text(message)
        if self.chat_container.children:
            self.chat_container.add_child(Spacer(1))
        theme = get_theme()
        if text:
            self.chat_container.add_child(Text(text, 0, 0))
        if error_message is not None:
            self.chat_container.add_child(Text(theme.fg("error", error_message), 0, 0))
        self.ui.request_render()

    def render_user_message(self, text: str) -> None:
        """Append a user message to the chat log.

        Port of the ``case "user"`` branch of the TS's ``renderMessage``, minus
        the skill-block split (``parseSkillBlock``, 7.6): the blank line before
        every message but the first, then the boxed message itself.
        """
        if self.chat_container.children:
            self.chat_container.add_child(Spacer(1))
        self.chat_container.add_child(
            UserMessageComponent(text, self.get_markdown_theme_with_settings())
        )
        self.ui.request_render()

    def show_error(self, error_message: str) -> None:
        theme = get_theme()
        self.chat_container.add_child(Spacer(1))
        self.chat_container.add_child(Text(theme.fg("error", f"Error: {error_message}"), 1, 0))
        self.ui.request_render()

    def show_warning(self, warning_message: str) -> None:
        self.chat_container.add_child(Text(get_theme().fg("warning", warning_message), 0, 0))
        self.ui.request_render()

    def show_status(self, message: str) -> None:
        self.chat_container.add_child(Text(get_theme().fg("dim", message), 0, 0))
        self.ui.request_render()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Tear the app down and hand the terminal back."""
        self.footer.dispose()
        if self._unsubscribe_session is not None:
            self._unsubscribe_session()
            self._unsubscribe_session = None
        if self._is_initialized:
            self.ui.stop()
            self._is_initialized = False

    def shutdown(self, code: int = 0) -> None:
        """Gracefully shut the agent down.

        Drains in-flight key-release events before stopping, as the TS does: over
        a slow SSH link an undrained Kitty release sequence leaks into the parent
        shell after the TUI has let go of the terminal.
        """
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        self.terminal.drain_input(1000)
        self.stop()
        self._exit_code = code
        if self._exit_waiter is not None and not self._exit_waiter.done():
            self._exit_waiter.set_result(code)
        if self.options.on_exit is not None:
            self.options.on_exit(code)


def build_app_root(tui: TUI, **options: Any) -> InteractiveMode:
    """Attach interactive mode's component tree to ``tui`` and return the app.

    The seam the end-to-end corpus boots through: it constructs the TUI over a
    fake terminal, hands it here, and starts it itself. Keyword arguments are
    :class:`InteractiveModeOptions` fields.
    """
    app = InteractiveMode(tui, InteractiveModeOptions(**options))
    app.init()
    return app


def run_interactive_mode(options: InteractiveModeOptions | None = None) -> int:
    """Run interactive mode against the real terminal. Returns the exit code.

    Builds the session the mode talks to, the way ``main.ts`` does through
    ``createAgentSessionRuntime``: settings and a session file for the cwd, and
    whatever model has been resolved for it — which, until the model registry
    lands in 7.11, is none.
    """
    resolved = options if options is not None else InteractiveModeOptions()

    if resolved.session is None:
        from cortex.code.session import create_agent_session

        cwd = resolved.cwd if resolved.cwd is not None else os.getcwd()
        # One settings manager, shared: the session reads the same file the app
        # does, and reading it twice is how the two drift.
        settings = (
            resolved.settings if resolved.settings is not None else SettingsManager.create(cwd)
        )
        created = create_agent_session(cwd=cwd, settings_manager=settings)
        resolved = replace(resolved, cwd=cwd, settings=settings, session=created.session)

    async def _run() -> int:
        app = InteractiveMode(TUI(ProcessTerminal()), resolved)
        try:
            return await app.run()
        finally:
            app.stop()

    return asyncio.run(_run())

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

**Where the TS puts the submission and where it is here.** In the TS a submitted
message reaches the screen the long way round: ``onSubmit`` resolves
``getUserInput()``, the run loop hands the text to ``session.prompt()``, the
session emits a ``user`` message event and ``renderMessage`` draws it. There is
no session until 7.4, so :meth:`InteractiveMode.render_user_message` — the port
of that ``renderMessage`` branch — is called from the submit handler directly.
7.4 moves the call onto the event handler, where the TS has it, and the same
component keeps drawing the same thing.

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

    def drain_input(self, max_ms: float = 1000, idle_ms: float = 50) -> None: ...


#: Below this width the compact wordmark does not fit, and the TS falls back to
#: the bare app name and version.
MIN_BANNER_COLUMNS = 40


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
        self.update_terminal_title()
        self._is_initialized = True

    def update_terminal_title(self) -> None:
        """Update terminal title with session name and cwd."""
        cwd_basename = os.path.basename(self.cwd)
        self.terminal.set_title(f"{APP_TITLE} - {cwd_basename}")

    async def run(self) -> int:
        """Run interactive mode until it is asked to exit. The main entry point.

        The TS loops forever on ``getUserInput()`` and lets ``process.exit`` end
        it. There is nothing to hand a submission to until 7.4, so the shell
        waits on the same thing the loop really waits on: shutdown.
        """
        self.init()
        self.ui.start()
        if self._exit_code is not None:
            return self._exit_code
        self._exit_waiter = asyncio.get_running_loop().create_future()
        return await self._exit_waiter

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    def setup_key_handlers(self) -> None:
        """Bind the app actions the editor dispatches.

        The TS registers eighteen of these in ``setupKeyHandlers``; the other
        sixteen drive a model controller, a task panel, selectors and an external
        editor, none of which exist before 7.6–7.9. What is here is what there is
        something to do: clear the editor, and exit from an empty one.
        """
        self.editor.on_action("app.clear", self.handle_ctrl_c)
        self.editor.on_ctrl_d = self.handle_ctrl_d

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
        """A submitted line: remember it, show it, hand it on."""
        text = text.strip()
        if not text:
            return

        callback = self._on_input_callback
        if callback is not None:
            self._on_input_callback = None
            callback(text)
        self.editor.add_to_history(text)
        # 7.4 deletes this line: by then the text has gone to `session.prompt()`,
        # which emits the `user` message event the renderer draws from.
        self.render_user_message(text)

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
    """Run interactive mode against the real terminal. Returns the exit code."""

    async def _run() -> int:
        app = InteractiveMode(TUI(ProcessTerminal()), options)
        try:
            return await app.run()
        finally:
            app.stop()

    return asyncio.run(_run())

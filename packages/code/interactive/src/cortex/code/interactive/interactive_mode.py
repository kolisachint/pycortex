"""Interactive mode for the coding agent.

Port of ``modes/interactive/interactive-mode.ts`` — the constructor and the
``init()``/``run()``/``stop()`` skeleton, which is what step 7.2 owes. The TS file
is 3,528 lines because it is also the chat log, the tool renderer, the command
executor, the overlay host and the extension chrome; each of those arrives with
the step that has something for it to drive (7.3 onwards). What lands here is the
part that makes ``pycortex`` a program you can look at:

* the component tree — header, chat, pending messages, status, editor, footer —
  assembled onto a :class:`~cortex.tui.render.TUI` in the TS's order, because the
  order *is* the layout;
* the startup banner, drawn with :func:`~cortex.code.interactive.wordmark.build_compact_wordmark`;
* focus on the editor, so the caret sits at the ``>`` prompt on the first frame;
* Ctrl+C: once clears the editor, twice within 500 ms shuts down and hands the
  terminal back.

**Where the TS puts these and where they are here.** ``handleCtrlC`` is reached in
the TS through ``CustomEditor``'s app-action dispatch (`app.clear`), which needs
``core/keybindings.ts`` and ``components/custom-editor.ts`` — both 7.3. The base
:class:`~cortex.tui.components.Editor` deliberately ignores Ctrl+C ("let the
parent handle it") and offers no hook, so the shell listens on the TUI instead.
7.3 moves it onto the editor where the TS has it.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from cortex.code.config import APP_NAME, APP_TITLE, VERSION, SettingsManager
from cortex.code.interactive.components.footer import FooterComponent, FooterState
from cortex.code.interactive.theme import get_editor_theme, get_theme
from cortex.code.interactive.wordmark import CompactWordmarkOptions, build_compact_wordmark
from cortex.tui.components import Editor, EditorOptions, Spacer, Text
from cortex.tui.keys import matches_key
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

        self.editor = Editor(
            self.ui,
            get_editor_theme(),
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
        self._dispose_input_listener: Callable[[], None] | None = None
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

        7.3 wraps this in the TS's `ExpandableText` so Ctrl+O reveals the
        keybinding hints — that list is generated from `core/keybindings.ts`,
        which does not exist yet, so the collapsed form is all there is to show.
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

        self._dispose_input_listener = self.ui.add_input_listener(self._handle_input)
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

    def _handle_input(self, data: str) -> dict[str, Any] | None:
        if matches_key(data, "ctrl+c"):
            self.handle_ctrl_c()
            return {"consume": True}
        return None

    def handle_ctrl_c(self) -> None:
        now = time.time() * 1000
        if now - self._last_sigint_time < SIGINT_EXIT_WINDOW_MS:
            self.shutdown()
        else:
            self.clear_editor()
            self._last_sigint_time = now

    def clear_editor(self) -> None:
        self.editor.set_text("")
        self.ui.request_render()

    # ------------------------------------------------------------------
    # Messages on screen
    # ------------------------------------------------------------------

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
        if self._dispose_input_listener is not None:
            self._dispose_input_listener()
            self._dispose_input_listener = None
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

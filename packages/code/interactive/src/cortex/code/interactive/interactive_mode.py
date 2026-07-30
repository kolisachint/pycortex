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

Step 7.5 made the turn something you watch rather than wait for. The assistant
message is built on ``message_start`` and fed the growing message on every
``message_update``, through a 100 ms leading+trailing throttle (:func:`_throttled`,
the TS's), so markdown renders styled as it arrives instead of appearing whole at
the end; a :class:`~cortex.tui.components.Loader` runs in the status container for
as long as the agent is working.

Step 7.6 gave the turn a body. Tool calls are blocks in the log now
(:class:`~cortex.code.interactive.components.tool_execution.ToolExecutionComponent`),
built the moment the model names a tool and updated three more times as the
arguments complete, execution starts and the result lands; ``app.tools.expand``
(Ctrl+O) expands every one of them at once; and a ``!`` prefix on a submission
runs the line as a shell command instead of sending it, streaming its output into
a :class:`~cortex.code.interactive.components.bash_execution.BashExecutionComponent`
through :class:`~cortex.code.interactive.bash_execution_controller.BashExecutionController`.

Step 7.8 gave the editor a command line. ``/`` opens the command menu, ``@``
opens the file one, and a submitted ``/command`` is dispatched by
:meth:`InteractiveMode.handle_submit` before anything can send it to the model:

* :meth:`InteractiveMode.setup_autocomplete_provider` builds the TS's
  ``CombinedAutocompleteProvider`` over
  :data:`~cortex.code.interactive.slash_commands.BUILTIN_SLASH_COMMANDS` and
  hands it to the editor;
* :meth:`InteractiveMode.create_built_in_slash_commands` is the TS's dispatch
  table, and :class:`~cortex.code.interactive.command_executor.CommandExecutor`
  holds the handlers — the app itself is the ``CommandContext`` they read
  through.

Step 7.9 gave it overlays. :meth:`InteractiveMode.show_selector` is the TS's
``showSelector`` — the component takes the *editor's place* in its container and
the keyboard with it, and the ``done`` it is handed puts both back, which is what
makes Escape out of any overlay land back at the prompt. Three commands reach it:
``/settings`` (:meth:`InteractiveMode.show_settings_selector`, the whole settings
screen over what this port can drive), ``/model`` and ``/scoped-models`` (both
through :class:`~cortex.code.interactive.model_controller.ModelController`, which
also answers Ctrl+L and the two cycle keys).

Step 7.10 made the session outlive the process. The app no longer owns an
:class:`~cortex.code.session.AgentSession` — it owns an
:class:`~cortex.code.session.AgentSessionRuntime` and reads the current session
off it (:attr:`InteractiveMode.session` is a property, as the TS's getter is), so
every command that *replaces* the session works without anything above having to
be re-wired:

* :meth:`InteractiveMode.render_current_session_state` is the TS's — drop the
  live state, then rebuild the transcript from the session's own entries
  (:meth:`InteractiveMode.render_initial_messages`). It is what ``--continue``
  puts on screen at startup and what every replacement ends with;
* :meth:`InteractiveMode.rebind_session` is ``rebindCurrentSession``, and the
  runtime calls it: unsubscribe from the old session, point the footer and the
  provider at the new one, resubscribe;
* ``/new`` and ``/clone`` and ``/import``
  (:class:`~cortex.code.interactive.command_executor.CommandExecutor`),
  ``/resume`` (:meth:`InteractiveMode.show_session_selector`), ``/fork``
  (:meth:`InteractiveMode.show_user_message_selector`) and ``/tree``
  (:meth:`InteractiveMode.show_tree_selector`) are the six commands over it, with
  Ctrl+N, Ctrl+R and the two tree keys beside them.

Shutdown is a callback, not ``process.exit``. The TS exits the process from
inside ``shutdown()``; doing that here would make the exit path the one thing the
end-to-end corpus could never watch. :meth:`InteractiveMode.shutdown` tears down
and resolves an exit code instead, and :func:`run_interactive_mode` is what turns
that code into a process exit.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, cast

from cortex.ai.types import TextContent
from cortex.code.config import (
    APP_NAME,
    APP_TITLE,
    VERSION,
    SettingsManager,
    get_agent_dir,
    get_bin_dir,
)
from cortex.code.interactive.bash_execution_controller import BashExecutionController
from cortex.code.interactive.command_executor import CommandExecutor
from cortex.code.interactive.components.assistant_message import AssistantMessageComponent
from cortex.code.interactive.components.bash_execution import BashExecutionComponent
from cortex.code.interactive.components.custom_editor import CustomEditor
from cortex.code.interactive.components.footer import FooterComponent
from cortex.code.interactive.components.session_selector import SessionSelectorComponent
from cortex.code.interactive.components.settings_selector import (
    SettingsCallbacks,
    SettingsConfig,
    SettingsSelectorComponent,
    ToolGroupInfo,
    ToolToggleInfo,
)
from cortex.code.interactive.components.tool_execution import (
    ToolExecutionComponent,
    ToolExecutionOptions,
    ToolExecutionResult,
    ToolOutputDisplayLevel,
)
from cortex.code.interactive.components.tree_selector import FilterMode, TreeSelectorComponent
from cortex.code.interactive.components.user_message import UserMessageComponent
from cortex.code.interactive.components.user_message_selector import (
    UserMessageItem,
    UserMessageSelectorComponent,
)
from cortex.code.interactive.footer_data_provider import FooterDataProvider
from cortex.code.interactive.keybindings import KeybindingsManager
from cortex.code.interactive.model_controller import ModelController, SelectorFactory
from cortex.code.interactive.slash_commands import BUILTIN_SLASH_COMMANDS
from cortex.code.interactive.startup_progress import startup_progress
from cortex.code.interactive.theme import (
    get_available_themes,
    get_editor_theme,
    get_markdown_theme,
    get_theme,
    set_theme,
)
from cortex.code.interactive.tool_renderers import resolve_tool_renderer
from cortex.code.interactive.wordmark import CompactWordmarkOptions, build_compact_wordmark
from cortex.code.session import (
    AgentSessionRuntime,
    AgentSessionServices,
    CreateAgentSessionResult,
    MissingSessionCwdError,
    SessionManager,
    create_agent_session,
)
from cortex.tui.components import (
    CombinedAutocompleteProvider,
    EditorOptions,
    Loader,
    MarkdownTheme,
    SlashCommand,
    Spacer,
    Text,
)
from cortex.tui.keys import set_keybindings
from cortex.tui.render import TUI, Container
from cortex.tui.terminal import ProcessTerminal

__all__ = [
    "BuiltInSlashCommand",
    "InteractiveMode",
    "InteractiveModeOptions",
    "build_app_root",
    "format_display_path",
    "resolve_fd_path",
    "resolve_session_manager",
    "run_interactive_mode",
]

#: Two Ctrl+C presses closer together than this exit; further apart, the second
#: is treated as another "clear the editor".
SIGINT_EXIT_WINDOW_MS = 500

#: How many finished tool blocks stay live at the bottom of the transcript.
#: Everything above that is frozen — its lines captured, its result payload and
#: image copies released (see :meth:`ToolExecutionComponent.freeze`). Generous
#: on purpose: nothing near the viewport is ever frozen, and the cap only bounds
#: what a long tool-heavy session costs the view layer.
LIVE_TOOL_WINDOW = 50

#: How often the streaming assistant message re-parses its markdown. Deltas
#: arrive far faster than this, and every application re-lexes the growing tail
#: block, so applying one per delta makes streaming cost O(message²).
STREAM_RENDER_THROTTLE_MS = 100

#: How often a startup-progress burst is allowed to repaint the footer. A
#: download streams byte counts in bursts and each repaint reassembles the whole
#: component tree, so the store's subscriber is throttled the same way the TS's
#: task-panel subscriber is.
TASK_RENDER_THROTTLE_MS = 50


def _schedule(coro: Any) -> None:
    """Run a coroutine reached from a keystroke, loop or no loop.

    Every overlay entry point is async (a model list has to be fetched) and
    every key handler is not, so this is the join. Without a running loop —
    a synchronous driver, a unit test — it is run to completion here rather
    than dropped, which is the only way a keystroke can be honoured at all.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    _ = asyncio.ensure_future(coro)


def _throttled(ms: int, fn: Callable[[], None]) -> Callable[[], None]:
    """Leading+trailing throttle. Port of the TS's ``throttled``.

    The first call runs immediately, calls landing inside the window coalesce
    into one trailing run with the latest state. Without a running loop there is
    no window to coalesce into and every call runs — which is right for a
    synchronous driver, and is what makes the throttle invisible to a test that
    is not asserting on it.
    """
    timer: list[Any] = [None]
    pending = [False]

    def run() -> None:
        fn()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def expire() -> None:
            timer[0] = None
            if pending[0]:
                pending[0] = False
                run()

        timer[0] = loop.call_later(ms / 1000, expire)

    def schedule() -> None:
        if timer[0] is not None:
            pending[0] = True
            return
        run()

    return schedule


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


def _content_field(block: Any, name: str) -> Any:
    """One field of a content block, dict or model."""
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


#: A message's fields are read the same way its content blocks' are: a session
#: restored from disk holds models for the roles that have one and plain dicts
#: for the roles that do not (`bashExecution`), exactly as the live path does.
_field_of = _content_field


def _tool_calls_of(message: Any) -> list[Any]:
    """The ``toolCall`` blocks of an assistant message, in order."""
    content = (
        message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    )
    if not content or isinstance(content, str):
        return []
    return [block for block in content if _content_field(block, "type") == "toolCall"]


def _closing(done: Callable[[], None], request_render: Callable[[], None]) -> Callable[[], None]:
    """The cancel callback every 7.10 overlay is handed: close, then repaint.

    The repaint is not redundant with ``done``: putting the editor back changes
    the component tree without asking for a frame, and a cancelled overlay that
    is still on screen is indistinguishable from one that ignored Escape.
    """

    def cancel() -> None:
        done()
        request_render()

    return cancel


def _create_unpersisted_session(cwd: str, settings_manager: SettingsManager) -> Any:
    """A session with no session file and no model, for an app booted without one."""
    return create_agent_session(
        cwd=cwd,
        settings_manager=settings_manager,
        session_manager=SessionManager(cwd, "", persist=False),
    ).session


def resolve_fd_path() -> str | None:
    """Where ``fd`` is, or ``None``.

    The TS calls ``ensureTool("fd", …)``, which resolves the binary and
    *downloads* it on first run, streaming progress into the footer. The
    downloader is a tool-binary manager this port has not reached, so what is
    here is the resolution half: the managed bin directory first (that is where
    ``ensureTool`` puts it, so a hoocode install is picked up), then ``PATH``.

    ``None`` is not an error state. The TS resolves fd in the background and
    never awaits it, so an app that has just started — or one on a machine where
    the download failed — runs with ``fdPath`` unset and simply offers no
    ``@`` completions. That is exactly what this returns.
    """
    managed = shutil.which("fd", path=get_bin_dir())
    return managed if managed else shutil.which("fd")


def format_display_path(path: str) -> str:
    """``~``-shorten a path for display. Port of ``resource-display.formatDisplayPath``."""
    home = str(Path.home())
    if path.startswith(home):
        return f"~{path[len(home) :]}"
    return path


@dataclass(frozen=True)
class BuiltInSlashCommand:
    """One entry of the submit handler's command table.

    The TS's ``{ withArgs?: boolean; run(text): Promise<void> | void }``. ``run``
    always receives the whole submitted line, arguments included, because that
    is what the handlers parse.
    """

    run: Callable[[str], None]
    with_args: bool = False


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
    #: The runtime the session hangs off. Built around ``session`` when omitted,
    #: with a factory that carries the current session's model and tools into
    #: every replacement (:meth:`InteractiveMode.create_replacement_session`).
    #: ``main.ts`` builds its own, which is how ``--continue`` opens on a
    #: restored session rather than switching to one after the fact.
    runtime_host: Any | None = None
    #: Settings source; a file-backed manager for the cwd when omitted.
    settings: SettingsManager | None = None
    #: Keybindings; the user's ``keybindings.json`` when omitted.
    keybindings: KeybindingsManager | None = None
    #: Called once, with the exit code, when the app has torn itself down.
    on_exit: Callable[[int], None] | None = None
    #: The ``fd`` binary the ``@``-mention provider searches with. Resolved from
    #: ``PATH`` and the managed bin directory when omitted (see
    #: :func:`resolve_fd_path`); ``@`` completion is simply inert without one,
    #: which is the state the TS is in until its background download settles.
    fd_path: str | None = None


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

        # The app owns a *runtime*, not a session: `/new`, `/resume`, `/fork`,
        # `/clone` and `/import` all replace the session under it, and `session`
        # is a property over whichever one is current (the TS's getter).
        initial_session = (
            self.options.session
            if self.options.session is not None
            else _create_unpersisted_session(self.cwd, self.settings_manager)
        )
        self.runtime_host = (
            self.options.runtime_host
            if self.options.runtime_host is not None
            else AgentSessionRuntime(
                initial_session,
                AgentSessionServices(
                    cwd=self.cwd,
                    agent_dir=get_agent_dir(),
                    settings_manager=self.settings_manager,
                ),
                self.create_replacement_session,
            )
        )
        self.runtime_host.set_rebind_session(self.rebind_session)

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

        self.footer_data_provider = FooterDataProvider(self.session.session_manager.get_cwd())
        self.footer = FooterComponent(self.session, self.footer_data_provider)
        self._unsubscribe_startup_progress: Callable[[], None] | None = None

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

        # Streaming state (7.5): the component the deltas are drawn into, the
        # message they carry, and the loader that says the agent is working.
        self.streaming_component: AssistantMessageComponent | None = None
        self.streaming_message: Any | None = None
        self.loading_animation: Loader | None = None
        self.working_visible = True
        self.working_message: str | None = None
        self.default_working_message = "Working..."
        self.hide_thinking_block = False
        self.hidden_thinking_label = "Thinking..."
        self.schedule_streaming_render = _throttled(
            STREAM_RENDER_THROTTLE_MS, self._render_streaming_message
        )

        # Tool state (7.6): the blocks for calls that have not finished yet, and
        # the two settings that decide how much of a finished one is on screen.
        self.pending_tools: dict[str, ToolExecutionComponent] = {}
        self.tool_output_expanded = False
        self.tool_output_display: ToolOutputDisplayLevel = cast(
            ToolOutputDisplayLevel, self.settings_manager.get_tool_output_display()
        )
        self.bash_execution = BashExecutionController(cast(Any, self))
        self._bash_task: asyncio.Future[None] | None = None
        #: Set while a `!command` is scheduled or running. See :meth:`is_busy`.
        self._bash_pending = False

        # Commands (7.8): the `fd` the `@` provider searches with, the built-in
        # command table, and the executor the handlers live on.
        self.fd_path = (
            self.options.fd_path if self.options.fd_path is not None else resolve_fd_path()
        )
        self.autocomplete_provider: CombinedAutocompleteProvider | None = None
        self._command_executor: CommandExecutor | None = None

        # Overlays (7.9): the model flows live off the app, as in the TS, and
        # the app is the context they read through.
        self._model_controller: ModelController | None = None

        self._slash_commands = self.create_built_in_slash_commands()

    @property
    def terminal(self) -> AppTerminal:
        """The TUI's terminal, seen through the app-level surface."""
        return cast(AppTerminal, self.ui.terminal)

    @property
    def session(self) -> Any:
        """Whichever session is current. Port of the TS's ``get session()``.

        A property rather than a field so that ``/new`` and ``/resume`` replacing
        the session under the runtime is invisible to everything that reads it —
        the command executor, the footer, the tool renderers.
        """
        return self.runtime_host.session

    @property
    def session_manager(self) -> Any:
        """The current session's manager. Part of the command context."""
        return self.session.session_manager

    def create_replacement_session(
        self, *, cwd: str, agent_dir: str, session_manager: Any
    ) -> CreateAgentSessionResult:
        """Build a session for the runtime to switch to.

        The TS factory closes over the process's fixed inputs and re-resolves the
        model through the registry; without one (7.11) the honest equivalent is
        to carry over what the *current* session is running with — model,
        thinking level, tools, system prompt, and the stream function a scenario
        may have substituted — so a resumed session answers the way the one
        before it did rather than dropping to no model at all.
        """
        current = self.session
        agent = current.agent
        return create_agent_session(
            cwd=cwd,
            agent_dir=agent_dir,
            settings_manager=self.settings_manager,
            session_manager=session_manager,
            model=agent.state.model,
            model_registry=current.model_registry,
            thinking_level=current.thinking_level or "off",
            system_prompt=agent.state.system_prompt or "",
            tools=list(agent.state.tools or []),
            stream_fn=agent.stream_fn,
            scoped_models=list(current.scoped_models),
        )

    async def rebind_session(self, session: Any) -> None:
        """Re-attach the app to a replacement session. Port of ``rebindCurrentSession``.

        Called by the runtime once the new session exists, and the order is the
        TS's: drop the old subscription first, then re-point everything that
        holds a session, then subscribe. Subscribing first would deliver the new
        session's events into a footer still describing the old one.
        """
        if self._unsubscribe_session is not None:
            self._unsubscribe_session()
            self._unsubscribe_session = None

        self.apply_runtime_settings()
        self.setup_session_listener()
        await self.model_controller.update_available_provider_count()
        self.update_editor_border_color()
        self.update_terminal_title()

    def apply_runtime_settings(self) -> None:
        """Point the session-shaped parts of the app at the current session.

        Port of ``applyRuntimeSettings``, minus the editor-geometry half: the
        settings that feed it cannot change during a session replacement here,
        because nothing between the two reloads them.
        """
        self.footer.set_session(self.session)
        # The TS reads `session.autoCompactionEnabled`, which is that session's
        # copy of the setting; this port's sessions do not carry one, so the
        # footer is told what the settings manager says — the same value, one
        # hop earlier.
        self.footer.set_auto_compact_enabled(self.settings_manager.get_compaction_enabled())
        self.footer_data_provider.set_cwd(self.session_manager.get_cwd())
        self.hide_thinking_block = self.settings_manager.get_hide_thinking_block()

    @property
    def command_executor(self) -> CommandExecutor:
        """The slash-command handlers, built on first use.

        The app *is* the :class:`~cortex.code.interactive.command_executor.CommandContext`
        — every member of the protocol is a property or a method here — which is
        the Python spelling of the TS's object-of-getters: a handler reads
        ``ctx.session`` at call time and gets the session that is current then,
        not the one that was current when the executor was built.
        """
        if self._command_executor is None:
            self._command_executor = CommandExecutor(self)
        return self._command_executor

    @property
    def model_controller(self) -> ModelController:
        """The model flows, built on first use. The app is its context too."""
        if self._model_controller is None:
            self._model_controller = ModelController(self)
        return self._model_controller

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
        self.setup_autocomplete_provider()
        self.setup_session_listener()
        self.setup_footer_watchers()
        self.update_terminal_title()
        self._is_initialized = True

        # Last, as in the TS: a session opened with `--continue` or `--session`
        # arrives with a transcript, and it belongs under the banner rather than
        # over it.
        self.render_initial_messages()

    def setup_session_listener(self) -> None:
        """Subscribe to the session, so its events reach the screen."""
        if self._unsubscribe_session is None:
            self._unsubscribe_session = self.session.subscribe(self.handle_session_event)

    def setup_footer_watchers(self) -> None:
        """Repaint when the footer's own sources move under it.

        Two of them: the git branch, which changes when someone switches branch
        in another terminal, and the startup-progress store, which ticks while
        first-run downloads and the index build run. Neither is a session event,
        so neither reaches the screen through :meth:`handle_session_event`.

        The branch callback arrives on the provider's watcher thread (the TS gets
        it on the event loop, where ``fs.watch`` lives), so it hops back to the
        loop before touching the TUI.
        """
        loop = self._running_loop()

        def repaint_from_thread() -> None:
            if loop is None:
                return
            try:
                loop.call_soon_threadsafe(self.ui.request_render)
            except RuntimeError:
                # The loop is gone: the app shut down while the watcher was
                # mid-poll. Nothing left to repaint.
                pass

        self.footer_data_provider.on_branch_change(repaint_from_thread)
        self._unsubscribe_startup_progress = startup_progress.subscribe(
            _throttled(TASK_RENDER_THROTTLE_MS, self.ui.request_render)
        )

    @staticmethod
    def _running_loop() -> asyncio.AbstractEventLoop | None:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

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
        """Whether the app is working — a turn in flight, or a `!command` running.

        A bash command is not a turn: the session is not streaming and nothing
        is pending on its way to the model, but the app is anything but idle, and
        a driver that treats it as idle stops pumping while the shell is still
        writing into the screen.
        """
        return self._turn_pending or self._bash_pending or bool(self.session.is_streaming)

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    def setup_key_handlers(self) -> None:
        """Bind the app actions the editor dispatches.

        The TS registers eighteen of these in ``setupKeyHandlers``; the rest
        drive a task panel, an external editor and the session tree, none of
        which exist yet. What is here is what there is something to do: clear
        the editor, exit from an empty one, abort a turn, expand tool output,
        and (7.9) the three model keys — open the picker, and step forward or
        back through the models in scope.
        """
        self.editor.on_escape = self.handle_escape
        self.editor.on_action("app.clear", self.handle_ctrl_c)
        self.editor.on_action("app.tools.expand", self.toggle_tool_output_expansion)
        self.editor.on_action(
            "app.model.select", lambda: _schedule(self.model_controller.show_model_selector())
        )
        self.editor.on_action(
            "app.model.cycleForward",
            lambda: _schedule(self.model_controller.cycle_model("forward")),
        )
        self.editor.on_action(
            "app.model.cycleBackward",
            lambda: _schedule(self.model_controller.cycle_model("backward")),
        )
        self.editor.on_action("app.thinking.cycle", self.cycle_thinking_level)
        self.editor.on_action(
            "app.session.new", lambda: _schedule(self.command_executor.handle_clear())
        )
        self.editor.on_action("app.session.tree", self.show_tree_selector)
        self.editor.on_action("app.session.fork", self.show_user_message_selector)
        self.editor.on_action("app.session.resume", self.show_session_selector)
        self.editor.on_ctrl_d = self.handle_ctrl_d

    def cycle_thinking_level(self) -> None:
        """Step the thinking level, and say where it landed.

        A model that cannot reason has nothing to cycle, and the TS says so
        rather than silently doing nothing — the key is otherwise
        indistinguishable from an unbound one.
        """
        level = self.session.cycle_thinking_level()
        if level is None:
            self.show_status("Model does not support thinking")
            return
        self.footer.invalidate()
        self.update_editor_border_color()
        self.show_status(f"Thinking level: {level}")

    def update_editor_border_color(self) -> None:
        """Recolour the editor border for the current thinking level.

        The border is the only always-visible sign of how hard the model is
        being asked to think, which is why every model and thinking change goes
        through here.
        """
        theme = get_theme()
        self.editor.border_color = theme.get_thinking_border_color(
            self.session.thinking_level or "off"
        )
        self.ui.request_render()

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

    # ------------------------------------------------------------------
    # Slash commands and autocomplete
    # ------------------------------------------------------------------

    def create_built_in_slash_commands(self) -> dict[str, BuiltInSlashCommand]:
        """The built-in commands the submit handler dispatches.

        Port of ``createBuiltInSlashCommands``, restricted to the handlers that
        have something to drive — see
        :mod:`cortex.code.interactive.command_executor` for what each of the
        others is waiting on. ``withArgs`` commands also match ``/name <args>``
        and receive the whole line; the rest only match on their own.

        Every handler clears the editor itself, in the TS's order: some show
        their output before the prompt is wiped and some after, and doing it
        centrally would flatten a difference the TS is deliberate about.

        ``/debug`` is dispatched but not advertised, exactly as in the TS: it is
        absent from :data:`~cortex.code.interactive.slash_commands.BUILTIN_SLASH_COMMANDS`,
        so it never appears in the `/` menu.
        """

        def clear_editor() -> None:
            self.editor.set_text("")

        def run_name(text: str) -> None:
            self.command_executor.handle_name(text)
            clear_editor()

        def run_session(_text: str) -> None:
            self.command_executor.handle_session()
            clear_editor()

        def run_changelog(_text: str) -> None:
            self.command_executor.handle_changelog()
            clear_editor()

        def run_hotkeys(_text: str) -> None:
            self.command_executor.handle_hotkeys()
            clear_editor()

        def run_debug(_text: str) -> None:
            self.command_executor.handle_debug()
            clear_editor()

        def run_quit(_text: str) -> None:
            clear_editor()
            self.shutdown()

        def run_settings(_text: str) -> None:
            self.show_settings_selector()
            clear_editor()

        def run_scoped_models(_text: str) -> None:
            clear_editor()
            _schedule(self.model_controller.show_models_selector())

        def run_model(text: str) -> None:
            search_term = text[len("/model ") :].strip() if text.startswith("/model ") else None
            clear_editor()
            _schedule(self.command_executor.handle_model(search_term or None))

        def run_new(_text: str) -> None:
            clear_editor()
            _schedule(self.command_executor.handle_clear())

        def run_clone(_text: str) -> None:
            clear_editor()
            _schedule(self.command_executor.handle_clone())

        def run_import(text: str) -> None:
            clear_editor()
            _schedule(self.command_executor.handle_import(text))

        def run_resume(_text: str) -> None:
            clear_editor()
            self.show_session_selector()

        def run_fork(_text: str) -> None:
            clear_editor()
            self.show_user_message_selector()

        def run_tree(_text: str) -> None:
            clear_editor()
            self.show_tree_selector()

        return {
            "/settings": BuiltInSlashCommand(run_settings),
            "/scoped-models": BuiltInSlashCommand(run_scoped_models),
            "/model": BuiltInSlashCommand(run_model, with_args=True),
            "/name": BuiltInSlashCommand(run_name, with_args=True),
            "/session": BuiltInSlashCommand(run_session),
            "/changelog": BuiltInSlashCommand(run_changelog),
            "/hotkeys": BuiltInSlashCommand(run_hotkeys),
            "/debug": BuiltInSlashCommand(run_debug),
            "/quit": BuiltInSlashCommand(run_quit),
            "/new": BuiltInSlashCommand(run_new),
            "/clone": BuiltInSlashCommand(run_clone),
            "/import": BuiltInSlashCommand(run_import, with_args=True),
            "/resume": BuiltInSlashCommand(run_resume),
            "/fork": BuiltInSlashCommand(run_fork),
            "/tree": BuiltInSlashCommand(run_tree),
        }

    def create_base_autocomplete_provider(self) -> CombinedAutocompleteProvider:
        """The provider behind `/` and `@`. Port of ``createBaseAutocompleteProvider``.

        The TS builds four lists — built-ins, prompt templates, extension
        commands and skill commands — and concatenates them. The last three come
        off ``session.promptTemplates``, the extension runner and the resource
        loader, none of which this port has, so the built-ins are the whole list.

        And the built-ins are filtered to the ones
        :meth:`create_built_in_slash_commands` dispatches. The TS needs no such
        filter because it has a handler for every row of the table; here, an
        advertised command with no handler would fall through the submit handler
        and be sent to the model as a prompt, which is a worse answer than not
        offering it.
        """
        slash_commands: list[SlashCommand] = [
            SlashCommand(name=command.name, description=command.description)
            for command in BUILTIN_SLASH_COMMANDS
            if f"/{command.name}" in self._slash_commands
        ]
        return CombinedAutocompleteProvider(
            slash_commands, self.session_manager.get_cwd(), self.fd_path
        )

    def setup_autocomplete_provider(self) -> None:
        """Build the provider and hand it to the editor.

        The TS re-runs this whenever the command list can have changed (a
        reload, an extension registering a provider wrapper). Nothing here moves
        yet, so it runs once from :meth:`init` — but it is a method rather than
        four lines inline because the things that move it are 7.9's and later.
        """
        provider = self.create_base_autocomplete_provider()
        self.autocomplete_provider = provider
        self.editor.set_autocomplete_provider(provider)

    def setup_editor_submit_handler(self) -> None:
        """Wire Enter to :meth:`handle_submit`."""
        self.editor.on_submit = self.handle_submit

    def handle_submit(self, text: str) -> None:
        """A submitted line: a command, a shell command, or a prompt.

        The order is the TS's. A built-in slash command is matched first and
        never reaches the model; then the ``!`` bash prefix; then the ordinary
        submission.

        Nothing is drawn here for a prompt. The text goes to
        ``session.prompt()``, the session emits a ``user`` message event, and
        :meth:`handle_session_event` draws it — the long way round the TS takes,
        so a message the session refused never appears as though it were sent.

        A ``!`` prefix is the exception: that is bash mode, and it never reaches
        the model. ``!!`` runs the command without putting its output in the
        model's context.
        """
        text = text.strip()
        if not text:
            return

        # A command is matched on its first word, so `/name my session` reaches
        # `/name` while `/session please` — a command that takes no arguments —
        # does not, and goes to the model as ordinary text.
        space_index = text.find(" ")
        command_name = text if space_index == -1 else text[:space_index]
        command = self._slash_commands.get(command_name)
        if command is not None and (space_index == -1 or command.with_args):
            command.run(text)
            return

        # Deferred bash rows from the last turn belong above whatever comes next.
        self.bash_execution.flush_pending_bash_components()

        if text.startswith("!"):
            self.editor.add_to_history(text)
            self.clear_editor()
            exclude_from_context = text.startswith("!!")
            command = text[2:] if exclude_from_context else text[1:]
            command = command.strip()
            if command:
                self.run_bash_command(command, exclude_from_context)
            return

        callback = self._on_input_callback
        if callback is not None:
            self._on_input_callback = None
            self._turn_pending = True
            callback(text)
        self.editor.add_to_history(text)

    def run_bash_command(self, command: str, exclude_from_context: bool = False) -> None:
        """Start a `!command` on the event loop and let the block track it.

        ``_bash_pending`` is set here rather than read off the session, for the
        same reason ``_turn_pending`` exists: between scheduling the coroutine
        and it reaching ``execute_bash`` the session looks idle, and a driver
        that pumps one pass and then asks "are you busy?" would stop right
        there.
        """
        self._bash_pending = True
        task = asyncio.ensure_future(
            self.bash_execution.handle_bash_command(command, exclude_from_context)
        )

        def finished(_task: asyncio.Future[None]) -> None:
            self._bash_pending = False

        task.add_done_callback(finished)
        self._bash_task = task

    # ------------------------------------------------------------------
    # Selectors
    # ------------------------------------------------------------------

    def show_selector(self, create: SelectorFactory) -> None:
        """Put an overlay where the editor is, and give it the keyboard.

        The overlay *replaces* the editor rather than floating over it — the
        editor container is cleared and the component put in its place — so the
        transcript above it never moves. ``done`` is what puts the editor back,
        and every selector calls it: on cancel, and on a choice once the choice
        has been applied.

        The factory is handed ``done`` rather than the app handing the component
        a callback afterwards, because a selector needs to be able to close
        itself from inside a callback it was constructed with.
        """

        def done() -> None:
            self.editor_container.clear()
            self.editor_container.add_child(self.editor)
            self.ui.set_focus(self.editor)

        component, focus = create(done)
        self.editor_container.clear()
        self.editor_container.add_child(component)
        self.ui.set_focus(focus)
        self.ui.request_render()

    def show_settings_selector(self) -> None:
        """``/settings``: the whole settings overlay, over what this port can drive.

        Two of the TS's lists come through empty, and neither is a stub:

        * **tools** — the TS unions the session's live tool registry with the
          persisted disabled set. This port's sessions are built with no tools
          (the agent's tool wiring is not part of any step so far), so the union
          is the disabled set alone: empty on a fresh install, and exactly the
          re-enable list for anyone who has disabled tools before. The four
          *group* switches beside it are settings rather than registry state, so
          they work today;
        * **flags** — extension-registered, and there is no extension runner.
          The TS omits the row entirely when there are none, so the absence is
          the TS's own behaviour rather than a hole this port left.

        Everything else on the screen is live: a change reaches the setting it
        names, and the ones with an immediate effect (theme, tool output
        display, images, cursor, padding, autocomplete) apply to what is already
        drawn rather than waiting for a restart.
        """
        settings = self.settings_manager

        def create(done: Callable[[], None]) -> tuple[Any, Any]:
            disabled_tool_names = set(settings.get_disabled_tools())
            tool_toggle_names = sorted(disabled_tool_names)
            tool_toggles = [ToolToggleInfo(name, False) for name in tool_toggle_names]

            tool_groups = [
                ToolGroupInfo(
                    "web",
                    "Web tools",
                    "webfetch + websearch (network access).",
                    settings.get_enable_web_tools(),
                ),
                ToolGroupInfo(
                    "browser",
                    "Browser tools",
                    "browser_run + browser_continue (browsertools engine).",
                    settings.get_enable_browser_tools(),
                ),
                ToolGroupInfo(
                    "file",
                    "Document tools",
                    "DocRead/DocEdit/DocWrite + DocScan/DocGrep/DocPeek (filetools binary).",
                    settings.get_enable_file_tools(),
                ),
                ToolGroupInfo(
                    "embsearch",
                    "Semantic search",
                    "Semantic index layer fused into the always-on search tool.",
                    settings.get_enable_embsearch_tools(),
                ),
            ]

            selector = SettingsSelectorComponent(
                SettingsConfig(
                    auto_compact=settings.get_compaction_enabled(),
                    tools=tool_toggles,
                    tool_groups=tool_groups,
                    flags=[],
                    tool_output_display=self.tool_output_display,
                    tool_output_max_bytes=settings.get_tool_output_max_bytes(),
                    tool_output_max_lines=settings.get_tool_output_max_lines(),
                    context_gc=settings.get_context_gc_enabled(),
                    show_images=settings.get_show_images(),
                    image_width_cells=settings.get_image_width_cells(),
                    auto_resize_images=settings.get_image_auto_resize(),
                    block_images=settings.get_block_images(),
                    enable_skill_commands=settings.get_enable_skill_commands(),
                    steering_mode=settings.get_steering_mode(),
                    follow_up_mode=settings.get_follow_up_mode(),
                    transport=settings.get_transport(),
                    thinking_level=self.session.thinking_level,
                    available_thinking_levels=self.session.get_available_thinking_levels(),
                    current_theme=settings.get_theme() or "dark",
                    available_themes=get_available_themes(),
                    hide_thinking_block=self.hide_thinking_block,
                    collapse_changelog=settings.get_collapse_changelog(),
                    enable_install_telemetry=settings.get_enable_install_telemetry(),
                    double_escape_action=settings.get_double_escape_action(),
                    tree_filter_mode=settings.get_tree_filter_mode(),
                    show_hardware_cursor=settings.get_show_hardware_cursor(),
                    editor_padding_x=settings.get_editor_padding_x(),
                    autocomplete_max_visible=settings.get_autocomplete_max_visible(),
                    quiet_startup=settings.get_quiet_startup(),
                    clear_on_shrink=settings.get_clear_on_shrink(),
                    show_terminal_progress=settings.get_show_terminal_progress(),
                    warnings=settings.get_warnings(),
                    voice_silence_ms=settings.get_voice_silence_ms(),
                    webtools_timeout_secs=settings.get_webtools_timeout_secs(),
                ),
                self._settings_callbacks(done),
            )
            return selector, selector.get_settings_list()

        self.show_selector(create)

    def show_user_message_selector(self) -> None:
        """``/fork``: pick a user message and branch the session before it."""
        user_messages = self.session.get_user_messages_for_forking()
        if not user_messages:
            self.show_status("No messages to fork from")
            return

        initial_selected_id = user_messages[-1].get("entry_id")

        def create(done: Callable[[], None]) -> tuple[Any, Any]:
            async def fork(entry_id: str) -> None:
                try:
                    result = await self.runtime_host.fork(entry_id)
                    if result.cancelled:
                        done()
                        self.ui.request_render()
                        return
                    self.render_current_session_state()
                    # The message forked *before* is out of the transcript now;
                    # it goes back into the editor so it can be asked again.
                    self.editor.set_text(result.selected_text or "")
                    done()
                    self.show_status("Forked to new session")
                except Exception as error:  # noqa: BLE001 - the TS catch, one for one
                    done()
                    self.show_error(str(error))

            selector = UserMessageSelectorComponent(
                [
                    UserMessageItem(id=message["entry_id"], text=message["text"])
                    for message in user_messages
                ],
                lambda entry_id: _schedule(fork(entry_id)),
                _closing(done, self.ui.request_render),
                initial_selected_id,
            )
            return selector, selector.get_message_list()

        self.show_selector(create)

    def show_tree_selector(self, initial_selected_id: str | None = None) -> None:
        """``/tree``: move the session's leaf to another point in its own tree.

        Unlike ``/fork`` this writes no new file — the branch pointer moves
        inside the session that is open.

        **Without the summary prompt.** The TS asks whether to summarise the
        branch being left, through ``dialogs.showSelector``; extension dialogs
        are not ported, and neither is the summariser they would drive (it needs
        the request auth of 7.11). Navigation happens unsummarised, which is the
        answer a user who has set ``branchSummarySkipPrompt`` already gets.
        """
        tree = self.session_manager.get_tree()
        real_leaf_id = self.session_manager.get_leaf_id()
        initial_filter_mode = cast(FilterMode, self.settings_manager.get_tree_filter_mode())

        if not tree:
            self.show_status("No entries in session")
            return

        def create(done: Callable[[], None]) -> tuple[Any, Any]:
            async def navigate(entry_id: str) -> None:
                if entry_id == real_leaf_id:
                    done()
                    self.show_status("Already at this point")
                    return

                done()
                try:
                    result = await self.session.navigate_tree(entry_id)
                    if result.cancelled:
                        self.show_status("Navigation cancelled")
                        return

                    self.chat_container.clear()
                    self.render_initial_messages()
                    if result.editor_text and not self.editor.get_text().strip():
                        self.editor.set_text(result.editor_text)
                    self.show_status("Navigated to selected point")
                except Exception as error:  # noqa: BLE001 - the TS catch, one for one
                    self.show_error(str(error))

            def change_label(entry_id: str, label: str | None) -> None:
                self.session_manager.append_label_change(entry_id, label)
                self.ui.request_render()

            selector = TreeSelectorComponent(
                tree,
                real_leaf_id,
                self.ui.terminal.rows,
                lambda entry_id: _schedule(navigate(entry_id)),
                _closing(done, self.ui.request_render),
                change_label,
                initial_selected_id,
                initial_filter_mode,
            )
            return selector, selector

        self.show_selector(create)

    def show_session_selector(self) -> None:
        """``/resume``: list the sessions on disk and open the one that is picked."""

        def create(done: Callable[[], None]) -> tuple[Any, Any]:
            async def load_current(on_progress: Any = None) -> list[Any]:
                return await SessionManager.list(
                    self.session_manager.get_cwd(),
                    self.session_manager.get_session_dir(),
                    on_progress,
                )

            async def load_all(on_progress: Any = None) -> list[Any]:
                return await SessionManager.list_all(on_progress)

            async def resume(session_path: str) -> None:
                done()
                await self.handle_resume_session(session_path)

            def rename(session_file_path: str, next_name: str | None) -> None:
                name = (next_name or "").strip()
                if not name:
                    return
                # Renaming reaches for the file rather than the open session: the
                # row being renamed is usually not the one that is running.
                SessionManager.open(session_file_path).append_session_info(name)

            selector = SessionSelectorComponent(
                load_current,
                load_all,
                lambda session_path: _schedule(resume(session_path)),
                _closing(done, self.ui.request_render),
                self.shutdown,
                self.ui.request_render,
                rename_session=rename,
                show_rename_hint=True,
                keybindings=self.keybindings,
                current_session_file_path=self.session_manager.get_session_file(),
            )
            return selector, selector

        self.show_selector(create)

    async def handle_resume_session(self, session_path: str) -> Any:
        """Switch to a stored session and put it on screen. Port of ``handleResumeSession``.

        The loader is stopped first: it belongs to a turn in the session being
        replaced, and an animation left running would tick over a transcript it
        has nothing to do with.
        """
        self.stop_working_loader()
        try:
            result = await self.runtime_host.switch_session(session_path)
            if result.cancelled:
                return result
            self.render_current_session_state()
            self.show_status("Resumed session")
            return result
        except MissingSessionCwdError as error:
            # The TS asks whether to open it in the current directory instead,
            # through the extension dialogs this port does not have. Saying what
            # is wrong is the honest half of that.
            self.show_error(str(error))
            return None
        except Exception as error:  # noqa: BLE001 - the TS catch, one for one
            self.show_error(f"Failed to resume session: {error}")
            return None

    def _settings_callbacks(self, done: Callable[[], None]) -> SettingsCallbacks:
        """What each settings row does. Port of the callback object in the TS."""
        settings = self.settings_manager

        def on_tool_enabled_change(name: str, enabled: bool) -> None:
            # Persisted for future sessions; this is what feeds the startup
            # denylist. The TS also applies it to the live tool registry, which
            # this port's sessions do not have (see `show_settings_selector`).
            disabled = set(settings.get_disabled_tools())
            disabled.discard(name) if enabled else disabled.add(name)
            settings.set_disabled_tools(sorted(disabled))

        def on_tool_group_change(group_id: str, enabled: bool) -> None:
            # Master switches: they gate tool creation when a session is built,
            # so they persist and take effect on the next one.
            if group_id == "web":
                settings.set_enable_web_tools(enabled)
            elif group_id == "browser":
                settings.set_enable_browser_tools(enabled)
            elif group_id == "file":
                settings.set_enable_file_tools(enabled)
            elif group_id == "embsearch":
                settings.set_enable_embsearch_tools(enabled)

        def on_tool_output_display_change(level: str) -> None:
            self.tool_output_display = cast(ToolOutputDisplayLevel, level)
            settings.set_tool_output_display(level)
            for child in self.chat_container.children:
                if isinstance(child, ToolExecutionComponent):
                    child.set_display_level(cast(ToolOutputDisplayLevel, level))
            self.ui.request_render()

        def on_show_images_change(enabled: bool) -> None:
            settings.set_show_images(enabled)
            for child in self.chat_container.children:
                if isinstance(child, ToolExecutionComponent):
                    child.set_show_images(enabled)

        def on_image_width_cells_change(width: int) -> None:
            settings.set_image_width_cells(width)
            for child in self.chat_container.children:
                if isinstance(child, ToolExecutionComponent):
                    child.set_image_width_cells(width)

        def on_enable_skill_commands_change(enabled: bool) -> None:
            settings.set_enable_skill_commands(enabled)
            self.setup_autocomplete_provider()

        def on_thinking_level_change(level: str) -> None:
            self.session.set_thinking_level(level)
            self.footer.invalidate()
            self.update_editor_border_color()

        def on_theme_change(theme_name: str) -> None:
            result = set_theme(theme_name)
            settings.set_theme(theme_name)
            self.ui.invalidate()
            if not result.success:
                self.show_error(
                    f'Failed to load theme "{theme_name}": {result.error}\nFell back to dark theme.'
                )

        def on_theme_preview(theme_name: str) -> None:
            if set_theme(theme_name).success:
                self.ui.invalidate()
                self.ui.request_render()

        def on_hide_thinking_block_change(hidden: bool) -> None:
            self.hide_thinking_block = hidden
            settings.set_hide_thinking_block(hidden)
            for child in self.chat_container.children:
                if isinstance(child, AssistantMessageComponent):
                    child.set_hide_thinking_block(hidden)
            # The TS clears the log and rebuilds it from the session's messages
            # here, because a message drawn with thinking hidden has already
            # thrown the block away. `rebuildChatFromMessages` is the same
            # machinery `renderCurrentSessionState` needs and lands with it in
            # 7.10; until then the setting reaches the components that are still
            # live, which is every one of them that can honour it.

        def on_show_hardware_cursor_change(enabled: bool) -> None:
            settings.set_show_hardware_cursor(enabled)
            self.ui.set_show_hardware_cursor(enabled)

        def on_editor_padding_x_change(padding: int) -> None:
            settings.set_editor_padding_x(padding)
            self.editor.set_padding_x(padding)

        def on_autocomplete_max_visible_change(max_visible: int) -> None:
            settings.set_autocomplete_max_visible(max_visible)
            self.editor.set_autocomplete_max_visible(max_visible)

        def on_clear_on_shrink_change(enabled: bool) -> None:
            settings.set_clear_on_shrink(enabled)
            self.ui.set_clear_on_shrink(enabled)

        def on_cancel() -> None:
            done()
            self.ui.request_render()

        return SettingsCallbacks(
            on_auto_compact_change=self.footer.set_auto_compact_enabled,
            on_tool_enabled_change=on_tool_enabled_change,
            on_tool_group_change=on_tool_group_change,
            on_tool_output_display_change=on_tool_output_display_change,
            on_tool_output_max_bytes_change=settings.set_tool_output_max_bytes,
            on_tool_output_max_lines_change=settings.set_tool_output_max_lines,
            on_context_gc_change=settings.set_context_gc_enabled,
            on_show_images_change=on_show_images_change,
            on_image_width_cells_change=on_image_width_cells_change,
            on_auto_resize_images_change=settings.set_image_auto_resize,
            on_block_images_change=settings.set_block_images,
            on_enable_skill_commands_change=on_enable_skill_commands_change,
            on_steering_mode_change=self.session.set_steering_mode,
            on_follow_up_mode_change=self.session.set_follow_up_mode,
            on_transport_change=settings.set_transport,
            on_thinking_level_change=on_thinking_level_change,
            on_theme_change=on_theme_change,
            on_theme_preview=on_theme_preview,
            on_hide_thinking_block_change=on_hide_thinking_block_change,
            on_collapse_changelog_change=settings.set_collapse_changelog,
            on_enable_install_telemetry_change=settings.set_enable_install_telemetry,
            on_double_escape_action_change=settings.set_double_escape_action,
            on_tree_filter_mode_change=settings.set_tree_filter_mode,
            on_show_hardware_cursor_change=on_show_hardware_cursor_change,
            on_editor_padding_x_change=on_editor_padding_x_change,
            on_autocomplete_max_visible_change=on_autocomplete_max_visible_change,
            on_quiet_startup_change=settings.set_quiet_startup,
            on_clear_on_shrink_change=on_clear_on_shrink_change,
            on_show_terminal_progress_change=settings.set_show_terminal_progress,
            on_warnings_change=settings.set_warnings,
            on_voice_silence_ms_change=settings.set_voice_silence_ms,
            on_webtools_timeout_secs_change=settings.set_webtools_timeout_secs,
            on_cancel=on_cancel,
        )

    async def find_exact_model_match(self, search_term: str) -> Any:
        """Part of the command context; the controller does the work."""
        return await self.model_controller.find_exact_model_match(search_term)

    async def maybe_warn_about_anthropic_subscription_auth(self, model: Any) -> None:
        """Part of the command context; the controller does the work."""
        await self.model_controller.maybe_warn_about_anthropic_subscription_auth(model)

    async def show_model_selector(self, search_term: str | None = None) -> None:
        """Part of the command context; the controller does the work."""
        await self.model_controller.show_model_selector(search_term)

    def set_available_provider_count(self, count: int) -> None:
        """Part of the model controller's context: the footer's provider badge."""
        self.footer_data_provider.set_available_provider_count(count)

    def invalidate_footer(self) -> None:
        """Part of the model controller's context."""
        self.footer.invalidate()

    # ------------------------------------------------------------------
    # Tool output
    # ------------------------------------------------------------------

    def toggle_tool_output_expansion(self) -> None:
        self.set_tools_expanded(not self.tool_output_expanded)

    def set_tools_expanded(self, expanded: bool) -> None:
        """Expand or collapse every expandable block in the log at once.

        One global flag rather than per-block state, as in the TS: the key is not
        aimed at anything, so "expand" has to mean the same thing everywhere or
        pressing it twice would leave the log in a state the user cannot reason
        about. New blocks are created at the current setting.
        """
        self.tool_output_expanded = expanded
        for child in [*self.chat_container.children, *self.pending_messages_container.children]:
            setter = getattr(child, "set_expanded", None)
            if callable(setter):
                setter(expanded)
        self.ui.request_render()

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
        are the turn's start and end (the loader and the terminal progress
        indicator), the messages themselves, and the tool calls inside them. The
        queue display (7.7), compaction and auto-retry (their controllers are
        unwired) are the rest, and each arrives with the component that shows it.
        """
        event_type = event.get("type")

        if event_type == "agent_start":
            if self.settings_manager.get_show_terminal_progress():
                self.terminal.set_progress(True)
            self.stop_working_loader()
            if self.working_visible:
                self.loading_animation = self.create_working_loader()
                self.status_container.add_child(self.loading_animation)
            self.ui.request_render()
        elif event_type == "agent_end":
            if self.settings_manager.get_show_terminal_progress():
                self.terminal.set_progress(False)
            self.stop_working_loader()
            # A turn that produced nothing leaves an empty component behind; the
            # TS drops it rather than leaving a blank hole in the log.
            if self.streaming_component is not None:
                self.chat_container.remove_child(self.streaming_component)
                self.streaming_component = None
                self.streaming_message = None
            self.pending_tools.clear()
            self.ui.request_render()
        elif event_type == "message_start":
            message = event.get("message")
            role = _role_of(message)
            if role == "user":
                self.render_user_message(_message_text(message))
            elif role == "assistant":
                self.streaming_component = AssistantMessageComponent(
                    None,
                    self.hide_thinking_block,
                    self.get_markdown_theme_with_settings(),
                    self.hidden_thinking_label,
                )
                self.streaming_message = message
                self.chat_container.add_child(self.streaming_component)
                self.streaming_component.update_content(cast(Any, message))
                self.ui.request_render()
        elif event_type == "message_update":
            message = event.get("message")
            if self.streaming_component is not None and _role_of(message) == "assistant":
                self.streaming_message = message
                self.schedule_streaming_render()
                # A tool call is part of the assistant message, so its block is
                # created here rather than on `tool_execution_start`: the model
                # names the tool before the loop has decided to run it, and the
                # block is what shows the arguments filling in.
                for content in _tool_calls_of(message):
                    tool_call_id = str(_content_field(content, "id") or "")
                    existing = self.pending_tools.get(tool_call_id)
                    if existing is None:
                        self.add_tool_component(
                            str(_content_field(content, "name") or ""),
                            tool_call_id,
                            _content_field(content, "arguments"),
                        )
                    else:
                        existing.update_args(_content_field(content, "arguments"))
                self.ui.request_render()
        elif event_type == "message_end":
            message = event.get("message")
            if _role_of(message) == "assistant":
                self.finish_assistant_message(message)
        elif event_type == "tool_execution_start":
            tool_call_id = str(event.get("tool_call_id") or "")
            component = self.pending_tools.get(tool_call_id)
            if component is None:
                component = self.add_tool_component(
                    str(event.get("tool_name") or ""), tool_call_id, event.get("args")
                )
            component.mark_execution_started()
            self.ui.request_render()
        elif event_type == "tool_execution_update":
            component = self.pending_tools.get(str(event.get("tool_call_id") or ""))
            if component is not None:
                partial = event.get("partial_result")
                component.update_result(
                    ToolExecutionResult(
                        content=list(getattr(partial, "content", None) or []),
                        details=getattr(partial, "details", None),
                        is_error=False,
                    ),
                    True,
                )
                self.ui.request_render()
        elif event_type == "tool_execution_end":
            tool_call_id = str(event.get("tool_call_id") or "")
            component = self.pending_tools.get(tool_call_id)
            if component is not None:
                result = event.get("result")
                component.update_result(
                    ToolExecutionResult(
                        content=list(getattr(result, "content", None) or []),
                        details=getattr(result, "details", None),
                        is_error=bool(event.get("is_error")),
                    )
                )
                del self.pending_tools[tool_call_id]
                self.trim_transcript_memory()
                self.ui.request_render()

    def add_tool_component(
        self, tool_name: str, tool_call_id: str, args: Any
    ) -> ToolExecutionComponent:
        """Build a block for a tool call and put it in the chat log."""
        component = ToolExecutionComponent(
            tool_name,
            tool_call_id,
            args,
            ToolExecutionOptions(
                show_images=self.settings_manager.get_show_images(),
                image_width_cells=self.settings_manager.get_image_width_cells(),
                display_level=self.tool_output_display,
            ),
            resolve_tool_renderer(self.session, tool_name),
            self.ui,
            self.session.cwd,
        )
        component.set_expanded(self.tool_output_expanded)
        self.chat_container.add_child(component)
        self.pending_tools[tool_call_id] = component
        return component

    def trim_transcript_memory(self) -> None:
        """Freeze finished tool blocks that have scrolled well out of the way.

        Runs on tool completion — infrequent, and only ever reaching blocks far
        above the viewport. The session data stays intact, so a later full
        rebuild restores full fidelity to anything frozen here.
        """
        freezable = [
            child
            for child in self.chat_container.children
            if isinstance(child, ToolExecutionComponent) and child.is_freezable()
        ]
        for component in freezable[: max(0, len(freezable) - LIVE_TOOL_WINDOW)]:
            component.freeze()

    def _render_streaming_message(self) -> None:
        """The throttle's body: redraw the in-flight message, segmented."""
        if self.streaming_component is None or self.streaming_message is None:
            return
        # streaming=True: large blocks render segmented so only the tail chunk
        # re-parses; `message_end` renders the canonical form directly.
        self.streaming_component.update_content(self.streaming_message, True)
        self.ui.request_render()

    def finish_assistant_message(self, message: Any) -> None:
        """Draw the finished assistant message. The ``message_end`` branch.

        The aborted wording is the TS's, read off ``session.retry_attempt``
        exactly where the TS reads it, and written onto the message so the
        component renders it — that line is the only thing on screen that says
        the Escape was heard.

        This is also where the *unstarted* tool calls are settled. A message that
        ended in an abort or an error has tool blocks on screen that will never
        run, so they are failed with the same message; a message that ended
        cleanly has blocks whose arguments are now final, which is what an edit's
        renderer needs before it can compute a diff preview.
        """
        if self.streaming_component is None:
            return
        self.streaming_message = message
        error_message: str | None = None
        stop_reason = getattr(message, "stop_reason", None)
        if stop_reason == "aborted":
            retry_attempt = self.session.retry_attempt
            error_message = (
                f"Aborted after {retry_attempt} retry attempt{'s' if retry_attempt > 1 else ''}"
                if retry_attempt > 0
                else "Operation aborted"
            )
            message.error_message = error_message
        self.streaming_component.update_content(message)

        if stop_reason in ("aborted", "error"):
            failure = error_message or str(getattr(message, "error_message", None) or "Error")
            for component in self.pending_tools.values():
                component.update_result(
                    ToolExecutionResult(
                        content=[TextContent(text=failure)], details=None, is_error=True
                    )
                )
            self.pending_tools.clear()
        else:
            for component in self.pending_tools.values():
                component.set_args_complete()

        self.streaming_component = None
        self.streaming_message = None
        self.footer.invalidate()
        self.ui.request_render()

    # ------------------------------------------------------------------
    # The working loader
    # ------------------------------------------------------------------

    def get_working_loader_message(self) -> str:
        return (
            self.working_message
            if self.working_message is not None
            else (self.default_working_message)
        )

    def create_working_loader(self) -> Loader:
        theme = get_theme()
        return Loader(
            self.ui,
            lambda text: theme.fg("accent", text),
            lambda text: theme.fg("muted", text),
            self.get_working_loader_message(),
        )

    def stop_working_loader(self) -> None:
        if self.loading_animation is not None:
            self.loading_animation.stop()
            self.loading_animation = None
        self.status_container.clear()

    def set_working_visible(self, visible: bool) -> None:
        self.working_visible = visible
        if not visible:
            self.stop_working_loader()
            self.ui.request_render()
            return
        if self.session.is_streaming and self.loading_animation is None:
            self.status_container.clear()
            self.loading_animation = self.create_working_loader()
            self.status_container.add_child(self.loading_animation)
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

    # ------------------------------------------------------------------
    # Rebuilding the transcript from a session
    # ------------------------------------------------------------------

    def add_message_to_chat(self, message: Any, populate_history: bool = False) -> None:
        """Draw one stored message. Port of ``addMessageToChat``.

        The branches with a component behind them: bash rows, user messages and
        assistant messages. Tool results are not among them — they are drawn
        *into* the call's block by :meth:`render_session_context`, which is the
        only place that knows which block a result belongs to. The TS's custom,
        compaction-summary and branch-summary branches need components that
        arrive with the steps that produce those entries.
        """
        role = _role_of(message)

        if role == "bashExecution":
            component = BashExecutionComponent(
                str(_field_of(message, "command") or ""),
                self.ui,
                bool(_field_of(message, "exclude_from_context")),
            )
            output = _field_of(message, "output")
            if output:
                component.append_output(str(output))
            component.set_complete(
                _field_of(message, "exit_code"),
                bool(_field_of(message, "cancelled")),
                None,
                _field_of(message, "full_output_path"),
            )
            self.chat_container.add_child(component)
            return

        if role == "user":
            text_content = _message_text(message)
            if not text_content:
                return
            self.render_user_message(text_content)
            if populate_history:
                # So Up-arrow on a resumed session walks back through what was
                # actually asked in it, not through an empty history.
                self.editor.add_to_history(text_content)
            return

        if role == "assistant":
            self.chat_container.add_child(
                AssistantMessageComponent(
                    message,
                    self.hide_thinking_block,
                    self.get_markdown_theme_with_settings(),
                    self.hidden_thinking_label,
                )
            )

    def render_session_context(
        self, session_context: Any, update_footer: bool = False, populate_history: bool = False
    ) -> None:
        """Draw a whole session context into the chat log. Port of ``renderSessionContext``.

        Tool calls are the part that needs bookkeeping: a call and its result are
        two messages, so each call gets a block that is held in
        ``rendered_pending_tools`` until the matching ``toolResult`` arrives to
        fill it in. Whatever is still unmatched at the end was in flight when the
        session was last written, so it becomes the app's pending set — which is
        what lets a tool that was running at quit finish drawing after a resume.
        """
        self.pending_tools.clear()
        rendered_pending_tools: dict[str, ToolExecutionComponent] = {}

        if update_footer:
            self.footer.invalidate()
            self.update_editor_border_color()

        for message in session_context.messages:
            role = _role_of(message)

            if role == "assistant":
                self.add_message_to_chat(message, populate_history)
                stop_reason = _field_of(message, "stop_reason")
                for content in _tool_calls_of(message):
                    tool_call_id = str(_content_field(content, "id") or "")
                    component = ToolExecutionComponent(
                        str(_content_field(content, "name") or ""),
                        tool_call_id,
                        _content_field(content, "arguments"),
                        ToolExecutionOptions(
                            show_images=self.settings_manager.get_show_images(),
                            image_width_cells=self.settings_manager.get_image_width_cells(),
                            display_level=self.tool_output_display,
                        ),
                        resolve_tool_renderer(
                            self.session, str(_content_field(content, "name") or "")
                        ),
                        self.ui,
                        self.session_manager.get_cwd(),
                    )
                    component.set_expanded(self.tool_output_expanded)
                    self.chat_container.add_child(component)

                    if stop_reason in ("aborted", "error"):
                        # A call the turn never got to run has no result and
                        # never will; it is drawn failed rather than pending.
                        if stop_reason == "aborted":
                            retry_attempt = self.session.retry_attempt
                            error_message = (
                                f"Aborted after {retry_attempt} retry attempt"
                                f"{'s' if retry_attempt > 1 else ''}"
                                if retry_attempt > 0
                                else "Operation aborted"
                            )
                        else:
                            error_message = str(_field_of(message, "error_message") or "Error")
                        component.update_result(
                            ToolExecutionResult(
                                content=[TextContent(text=error_message)],
                                details=None,
                                is_error=True,
                            )
                        )
                    else:
                        rendered_pending_tools[tool_call_id] = component
            elif role == "toolResult":
                tool_call_id = str(
                    _field_of(message, "tool_call_id") or _field_of(message, "toolCallId") or ""
                )
                component = rendered_pending_tools.pop(tool_call_id, None)
                if component is not None:
                    component.update_result(
                        ToolExecutionResult(
                            content=list(_field_of(message, "content") or []),
                            details=_field_of(message, "details"),
                            is_error=bool(_field_of(message, "is_error")),
                        )
                    )
            else:
                self.add_message_to_chat(message, populate_history)

        self.pending_tools.update(rendered_pending_tools)
        self.ui.request_render()

    def render_initial_messages(self) -> None:
        """Draw the current session's transcript. Port of ``renderInitialMessages``."""
        context = self.session_manager.build_session_context()
        self.render_session_context(context, update_footer=True, populate_history=True)

        compaction_count = sum(
            1 for entry in self.session_manager.get_entries() if entry.get("type") == "compaction"
        )
        if compaction_count > 0:
            times = "1 time" if compaction_count == 1 else f"{compaction_count} times"
            self.show_status(f"Session compacted {times}")

    def render_current_session_state(self) -> None:
        """Throw the screen away and rebuild it from the session that is current.

        Port of ``renderCurrentSessionState``, and the second half of every
        session replacement. The live state goes first — a streaming component
        and a pending tool block both belong to a session that no longer exists,
        and leaving either would attach the next turn's deltas to a dead
        component.
        """
        self.chat_container.clear()
        self.pending_messages_container.clear()
        self.streaming_component = None
        self.streaming_message = None
        self.pending_tools.clear()
        self.render_initial_messages()

    def rebuild_chat_from_messages(self) -> None:
        """Redraw the transcript in place, for a change of how it looks.

        The same rebuild without the state reset: the session has not changed,
        only the way it renders (hiding thinking blocks, a theme change).
        """
        self.chat_container.clear()
        self.render_session_context(self.session_manager.build_session_context())

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
        # The provider owns the git watcher thread and its timers; left running,
        # they outlive the terminal they were repainting.
        self.footer_data_provider.dispose()
        if self._unsubscribe_startup_progress is not None:
            self._unsubscribe_startup_progress()
            self._unsubscribe_startup_progress = None
        # The loader animates off a repeating timer; leaving it running holds a
        # callback on the event loop after the TUI has let go of the terminal.
        self.stop_working_loader()
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


def resolve_session_manager(
    cwd: str,
    *,
    continue_session: bool = False,
    session_path: str | None = None,
    session_dir: str | None = None,
    no_session: bool = False,
) -> SessionManager:
    """Which session file the process starts on. Port of ``main.ts``'s ``resolveSessionManager``.

    The order is the TS's, minus the two branches that need a picker or an id
    lookup (``--resume`` and ``--session <partial-uuid>``, whose resolver is
    7.11's): an explicit path, then ``--continue``, then a new session.
    """
    if no_session:
        return SessionManager.in_memory(cwd)
    if session_path:
        return SessionManager.open(session_path, session_dir)
    if continue_session:
        return SessionManager.continue_recent(cwd, session_dir)
    return SessionManager.create(cwd, session_dir)


def run_interactive_mode(
    options: InteractiveModeOptions | None = None,
    session_manager: SessionManager | None = None,
) -> int:
    """Run interactive mode against the real terminal. Returns the exit code.

    Builds the session the mode talks to, the way ``main.ts`` does through
    ``createAgentSessionRuntime``: settings and a session file for the cwd, and
    whatever model has been resolved for it — which, until the model registry
    lands in 7.11, is none.

    ``session_manager`` is how ``--continue`` reaches here: the file is chosen
    before the session is built (:func:`resolve_session_manager`), so the app
    opens *on* the restored transcript rather than switching to it afterwards.
    """
    resolved = options if options is not None else InteractiveModeOptions()

    if resolved.session is None:
        cwd = resolved.cwd if resolved.cwd is not None else os.getcwd()
        # One settings manager, shared: the session reads the same file the app
        # does, and reading it twice is how the two drift.
        settings = (
            resolved.settings if resolved.settings is not None else SettingsManager.create(cwd)
        )
        created = create_agent_session(
            cwd=cwd, settings_manager=settings, session_manager=session_manager
        )
        resolved = replace(resolved, cwd=cwd, settings=settings, session=created.session)

    async def _run() -> int:
        app = InteractiveMode(TUI(ProcessTerminal()), resolved)
        try:
            return await app.run()
        finally:
            app.stop()

    return asyncio.run(_run())

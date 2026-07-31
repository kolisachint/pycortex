"""The end-to-end corpus: what `pycortex` must show the user, step by step.

Each scenario is one thing a person can check by running the app and looking at
the screen. Every scenario names the migration step that delivers it
(`blocked_by`), so the corpus doubles as Phase 7's progress ledger:

* a scenario with `run=None` is **pending** — nobody has built it yet;
* a scenario that runs and passes is **passing**;
* a scenario that runs and fails is **failing**, which is a regression.

`scripts/tui_e2e.py` executes the corpus and writes `docs/tui-e2e-report.json`.
`scripts/migrate_next.py` reads that report and **refuses to tick a Phase 7 box
while scenarios attributed to that step are still pending or failing**. That is
the whole point: step 5.2 was ticked with a 151-line stub because the audit only
asked "does this leaf have code and a test file", and a stub answers yes. A
scenario answers "does the screen show the thing", which a stub cannot fake.
"""

from __future__ import annotations

import json
import os
import re
import traceback
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from cortex.code.e2e._harness import AppHarness
from cortex.code.interactive import BRAND_MARK, resolve_session_manager
from cortex.tui.render import TUI

__all__ = [
    "SCENARIOS",
    "Scenario",
    "ScenarioResult",
    "boot_app",
    "run_all",
    "run_scenario",
    "scenario",
]

ScenarioFn = Callable[[], None]
Status = Literal["passing", "failing", "pending"]


@dataclass(frozen=True)
class Scenario:
    """One user-checkable outcome."""

    id: str
    title: str
    blocked_by: str
    run: ScenarioFn | None = None

    @property
    def pending(self) -> bool:
        return self.run is None


@dataclass(frozen=True)
class ScenarioResult:
    id: str
    blocked_by: str
    status: Status
    detail: str = ""


SCENARIOS: list[Scenario] = []


def scenario(id: str, title: str, blocked_by: str) -> Callable[[ScenarioFn], ScenarioFn]:
    """Register an implemented scenario."""

    def decorate(fn: ScenarioFn) -> ScenarioFn:
        SCENARIOS.append(Scenario(id=id, title=title, blocked_by=blocked_by, run=fn))
        return fn

    return decorate


def pending(id: str, title: str, blocked_by: str) -> None:
    """Register a scenario nobody has built yet."""
    SCENARIOS.append(Scenario(id=id, title=title, blocked_by=blocked_by, run=None))


def boot_app(*, columns: int = 80, rows: int = 24, **options: object) -> AppHarness:
    """Boot the real interactive mode under the harness.

    `cortex.code.interactive.build_app_root` (step 7.2) is the single seam every
    app scenario goes through, so pointing the corpus at a different root — a
    later step's, or a test double's — is a one-line change here rather than an
    edit to thirty scenarios. `options` are `InteractiveModeOptions` fields.

    The app's message loop (7.4) is started on the harness's loop rather than by
    `run()`: `run()` also starts the TUI and waits for an exit code, and the
    harness owns both.
    """
    from cortex.code.interactive import build_app_root

    built: list[Any] = []

    def build(tui: TUI) -> None:
        built.append(build_app_root(tui, **options))

    harness = AppHarness(build, columns=columns, rows=rows)
    app = built[0]
    harness.attach(app=app, busy=app.is_busy, run=app.message_loop())
    return harness


# ===========================================================================
# 7.1 — the harness proves itself
#
# These do not touch the app. They exist so a failure in the corpus can be told
# apart from a failure in the thing running the corpus.
# ===========================================================================


@scenario("harness/renders-a-frame", "The harness paints a component onto a real surface", "7.1")
def harness_renders_a_frame() -> None:
    from cortex.tui.components import Text

    with AppHarness(lambda tui: tui.add_child(Text("pycortex e2e"))) as h:
        h.assert_shows("pycortex e2e")


@scenario("harness/receives-input", "Keystrokes reach the focused component", "7.1")
def harness_receives_input() -> None:
    from cortex.tui.components import Text

    seen: list[str] = []
    label = Text("")

    def build(tui: object) -> None:
        tui.add_child(label)  # type: ignore[attr-defined]
        tui.add_input_listener(lambda data: seen.append(data) or None)  # type: ignore[attr-defined]

    with AppHarness(build) as h:
        h.type("hi")
        h.key("enter")
        assert seen == ["h", "i", "\r"], f"input did not arrive intact: {seen!r}"


@scenario("harness/resize-repaints", "A resize repaints at the new width", "7.1")
def harness_resize_repaints() -> None:
    from cortex.tui.components import Text

    with AppHarness(lambda tui: tui.add_child(Text("wide enough to matter")), columns=40) as h:
        h.assert_shows("wide enough to matter")
        h.resize(24, 12)
        assert h.surface().cols == 24, "surface did not adopt the new width"


# ===========================================================================
# 7.2 — the shell boots
# ===========================================================================


@dataclass
class FauxSession:
    """A session wired to `ai/provider-faux`: a real turn, and no network."""

    session: Any
    #: Queue what the provider answers with next. Takes `AssistantMessage`s (see
    #: `faux_assistant_message`) or callables the provider invokes per request.
    set_responses: Callable[[list[Any]], None]
    unregister: Callable[[], None]
    #: Every model the faux provider registered, in order.
    models: list[Any] = field(default_factory=list)


class FauxModelRegistry:
    """A model registry over the faux provider's models.

    The real one is step 7.11's: it resolves credentials, watches `models.json`
    and decides which models a user can actually reach. What the overlays ask it
    is much smaller — the list, and whether a model has auth — so this answers
    exactly that, for models with no credentials to have. A scenario about
    *switching* models cannot wait for the machinery that decides which ones you
    are allowed to switch to.
    """

    def __init__(self, models: list[Any]) -> None:
        self._models = list(models)
        self.refreshes = 0

    def has_configured_auth(self, model: Any) -> bool:
        return any(m.provider == model.provider and m.id == model.id for m in self._models)

    def is_using_oauth(self, model: Any) -> bool:
        return False

    async def get_api_key_and_headers(self, model: Any) -> Any:
        """A key for anything this registry lists — the faux provider ignores it.

        The session wraps the agent's stream function around this the moment a
        registry is present, so a stand-in that did not answer would take every
        faux scenario's turn down with it.
        """
        from cortex.code.config import ResolvedRequestAuth

        if self.has_configured_auth(model):
            return ResolvedRequestAuth(ok=True, api_key="faux-key")
        return ResolvedRequestAuth(ok=False, error=f'No API key found for "{model.provider}"')

    def refresh(self) -> None:
        self.refreshes += 1

    async def get_available(self) -> list[Any]:
        return list(self._models)


def faux_session(
    *,
    cwd: str = "/w/project",
    settings: Any = None,
    stream_fn: Any = None,
    tools: list[Any] | None = None,
    models: list[Any] | None = None,
    with_model_registry: bool = False,
    model_registry: Any = None,
    model: Any = None,
    session_manager: Any = None,
) -> FauxSession:
    """Build the session the shell scenarios prompt against.

    Everything real except the provider: a real `Agent`, a real `AgentSession`,
    the real agent loop — and a faux provider in place of the HTTP call, which is
    what step 7.4 promises ("round-trip a prompt against `ai/provider-faux` with
    no network"). The session manager does not persist: a scenario must not
    leave a session file on the machine that ran it.

    `stream_fn` replaces the *byte source* and nothing else — the model, the
    preflight and the loop are still the real ones. A scenario about what the
    screen does **between** two deltas needs to own when the second one arrives,
    and the faux provider streams whole responses on its own schedule; see
    `chat/streaming-incremental`.

    `models` overrides the faux model definitions. The footer's context meter is
    the reason it exists: the default model declares a 128k window, and no turn a
    scenario can afford to run moves a gauge scaled to that.

    `session_manager` is 7.10's: the scenarios about a session outliving the
    process need one that writes, and they bring a temporary directory of their
    own to write into.

    `model_registry` and `model` are 7.11's, and the auth scenarios need both to
    be *real*: the question those scenarios ask is which providers you can log
    into and whether the one you are on has a key, and `FauxModelRegistry` — or
    the faux model, whose provider needs no key at all — answers "yes" to
    everything. Both are constructor arguments rather than assignments because
    `AgentSession.model_registry` is read-only and `model` lives on the agent.
    """
    from cortex.ai.providers.faux import register_faux_provider
    from cortex.code.session import SessionManager, create_agent_session

    registration = register_faux_provider(models=models)
    created = create_agent_session(
        cwd=cwd,
        settings_manager=settings,
        session_manager=(
            session_manager
            if session_manager is not None
            else SessionManager(cwd, "", persist=False)
        ),
        model=model if model is not None else registration.get_model(),
        stream_fn=stream_fn,
        tools=tools,
        model_registry=(
            model_registry
            if model_registry is not None
            else (FauxModelRegistry(registration.models) if with_model_registry else None)
        ),
    )
    return FauxSession(
        session=created.session,
        set_responses=registration.set_responses,
        unregister=registration.unregister,
        models=list(registration.models),
    )


def boot_shell(*, columns: int = 80, rows: int = 24, **options: object) -> AppHarness:
    """Boot the shell with nothing of the machine it runs on in the picture.

    Settings come from memory rather than `~/.hoocode`, and the cwd is a fixed
    string: both feed the banner and the footer, so a scenario that used the real
    ones would render a different screen on every machine — and would quietly
    start failing the day someone set `quietStartup` in their own settings file.
    Keybindings are the same story one layer down: with the user's
    `keybindings.json` in play, "press Enter to submit" is only true by default.
    The session is the same story one layer up: the real one would need a model,
    a key and a network, so the shell boots against `faux_session()` unless the
    scenario brings its own.
    """
    from cortex.code.config import SettingsManager
    from cortex.code.config.settings_storage import InMemorySettingsStorage
    from cortex.code.interactive import KeybindingsManager

    settings = options.setdefault(
        "settings", SettingsManager.from_storage(InMemorySettingsStorage())
    )
    options.setdefault("keybindings", KeybindingsManager())
    cwd = options.setdefault("cwd", "/w/project")
    options.setdefault("session", faux_session(cwd=str(cwd), settings=settings).session)
    return boot_app(columns=columns, rows=rows, **options)


def _prompt_row(harness: AppHarness) -> int:
    """Index of the editor's prompt line while the editor is empty, or -1."""
    for index, line in enumerate(harness.surface().lines()):
        if line.strip() == ">":
            return index
    return -1


def _editor_row(harness: AppHarness) -> int:
    """Index of the editor's first line, empty or not, or -1."""
    for index, line in enumerate(harness.surface().lines()):
        if line == ">" or line.startswith("> "):
            return index
    return -1


@scenario("shell/boot-banner", "Startup banner and brand mark are on screen", "7.2")
def shell_boot_banner() -> None:
    with boot_shell() as h:
        # The owl glyph, the split brand name, and the tagline/version line —
        # the three rows `build_compact_wordmark` draws.
        h.assert_shows("▟▀▀▀▀▀▙", "hoo│code", "agentic coding agent · v", "/w/project")


@scenario("shell/editor-prompt", "The editor is focused and shows its `>` prompt", "7.2")
def shell_editor_prompt() -> None:
    with boot_shell() as h:
        row = _prompt_row(h)
        assert row >= 0, f"no editor prompt line on screen\n\n{h.snapshot()}"
        # Focus is not a string on the screen, it is where the caret sits: the
        # hardware cursor parks just past the `> ` prefix on the prompt row.
        surface = h.surface()
        assert (surface.cursor_row, surface.cursor_x) == (row, 2), (
            f"caret is not at the prompt: {(surface.cursor_row, surface.cursor_x)}"
            f"\n\n{h.snapshot()}"
        )


@scenario("shell/footer-present", "A footer line is present under the editor", "7.2")
def shell_footer_present() -> None:
    with boot_shell() as h:
        lines = h.surface().lines()
        footer_rows = [i for i, line in enumerate(lines) if line.startswith("⬢ ")]
        assert footer_rows, f"no footer line on screen\n\n{h.snapshot()}"
        assert footer_rows[0] > _prompt_row(h), f"footer is above the editor\n\n{h.snapshot()}"
        # The footer reports where you are and what model is answering. Both are
        # read off the live session (7.7): before it, the second half of this
        # line was the hard-coded `no-model`.
        h.assert_shows("⬢ BUILD  /w/project", "faux-1")


@scenario("shell/resize-reflows", "Resizing reflows the shell without corruption", "7.2")
def shell_resize_reflows() -> None:
    with boot_shell() as h:
        h.assert_shows("▟▀▀▀▀▀▙")
        h.resize(52, 18)
        surface = h.surface()
        assert surface.cols == 52, "surface did not adopt the new width"
        overlong = [line for line in surface.lines() if len(line) > 52]
        assert not overlong, f"line(s) wider than the terminal: {overlong!r}\n\n{h.snapshot()}"
        # Every band of the shell survived the reflow, and the footer re-laid
        # itself out rather than keeping the 80-column line it was drawn with.
        assert _prompt_row(h) >= 0, f"editor prompt lost on resize\n\n{h.snapshot()}"
        h.assert_shows("▟▀▀▀▀▀▙", "⬢ BUILD  /w/project", scrollback=False)


@scenario("shell/ctrl-c-exits", "Ctrl+C twice exits and restores the terminal", "7.2")
def shell_ctrl_c_exits() -> None:
    exits: list[int] = []
    with boot_shell(on_exit=exits.append) as h:
        h.key("ctrl+c")
        assert exits == [], "a single Ctrl+C exited; it should only clear the editor"
        h.key("ctrl+c")
        assert exits == [0], f"two Ctrl+C presses did not exit: {exits!r}"
        # Restoring the terminal is the other half of exiting: the TUI is
        # stopped, the cursor is visible again, and pending input was drained so
        # no escape sequence leaks into the parent shell.
        assert h.terminal.stopped, "the terminal was never stopped"
        assert h.terminal.cursor_hidden is False, "the cursor was left hidden"
        assert h.terminal.drained >= 1, "input was not drained before exiting"


# ===========================================================================
# 7.3 — typing and the chat log
# ===========================================================================


@scenario("input/type-and-submit", "Typed text appears, Enter clears the editor", "7.3")
def input_type_and_submit() -> None:
    with boot_shell() as h:
        h.type("hello world")
        row = _editor_row(h)
        assert row >= 0, f"no editor line on screen\n\n{h.snapshot()}"
        h.assert_shows("> hello world", scrollback=False)
        surface = h.surface()
        # The caret trails the last character typed, not the start of the line.
        assert (surface.cursor_row, surface.cursor_x) == (row, 2 + len("hello world")), (
            f"caret is not after the typed text: {(surface.cursor_row, surface.cursor_x)}"
            f"\n\n{h.snapshot()}"
        )

        h.key("enter")
        prompt = _prompt_row(h)
        assert prompt >= 0, f"the editor still holds the submitted text\n\n{h.snapshot()}"
        surface = h.surface()
        assert (surface.cursor_row, surface.cursor_x) == (prompt, 2), (
            f"caret did not return to the empty prompt: {(surface.cursor_row, surface.cursor_x)}"
            f"\n\n{h.snapshot()}"
        )


@scenario("input/multiline", "Shift+Enter opens a second line instead of submitting", "7.3")
def input_multiline() -> None:
    with boot_shell() as h:
        h.type("first")
        h.key("shift+enter")
        h.type("second")

        row = _editor_row(h)
        lines = h.surface().lines()
        # Still in the editor — a submission would have emptied it — and the
        # second line is directly under the first, with the caret on it.
        assert row >= 0 and lines[row] == "> first", (
            f"the first line left the editor\n\n{h.snapshot()}"
        )
        assert lines[row + 1].strip() == "second", f"no second line\n\n{h.snapshot()}"
        assert h.surface().cursor_row == row + 1, (
            f"caret stayed on the first line: {h.surface().cursor_row}\n\n{h.snapshot()}"
        )

        # Enter, on the other hand, submits the whole thing.
        h.key("enter")
        assert _prompt_row(h) >= 0, f"Enter did not submit\n\n{h.snapshot()}"
        h.assert_shows("first", "second")


@scenario("input/history-recall", "Up-arrow recalls the previous submission", "7.3")
def input_history_recall() -> None:
    with boot_shell() as h:
        h.type("remember me")
        h.key("enter")
        assert _prompt_row(h) >= 0, f"the submission did not clear the editor\n\n{h.snapshot()}"

        h.key("up")
        h.assert_shows("> remember me", scrollback=False)
        surface = h.surface()
        assert surface.cursor_x == 2 + len("remember me"), (
            f"caret is not at the end of the recalled line: {surface.cursor_x}\n\n{h.snapshot()}"
        )


@scenario("chat/user-message-renders", "A submitted message renders as a user message", "7.3")
def chat_user_message_renders() -> None:
    with boot_shell() as h:
        h.type("what does this repo do?")
        h.key("enter")

        surface = h.surface()
        rows = [i for i, line in enumerate(surface.lines()) if "what does this repo do?" in line]
        assert rows, f"the submitted text is nowhere on screen\n\n{h.snapshot()}"
        # In the chat log above the editor, not still sitting in it.
        assert rows[0] < _prompt_row(h), f"the message is not in the chat log\n\n{h.snapshot()}"
        # And rendered as a *user message*: the component boxes it against the
        # theme's background, which a bare line of text would not have.
        assert any(cell.style.bg is not None for cell in surface.grid[rows[0]]), (
            f"the message is not drawn as a user message\n\n{h.snapshot()}"
        )


# ===========================================================================
# 7.4 — the agent session is wired in
# ===========================================================================


@scenario("chat/assistant-round-trip", "A prompt round-trips against the faux provider", "7.4")
def chat_assistant_round_trip() -> None:
    from cortex.ai.providers.faux import faux_assistant_message

    faux = faux_session()
    faux.set_responses([faux_assistant_message("Ada Lovelace wrote the first algorithm.")])
    try:
        with boot_shell(session=faux.session) as h:
            h.type("who wrote the first algorithm?")
            h.key("enter")

            # The whole round trip, in the order a user sees it: the question in
            # the chat log, then the answer under it, and the editor empty and
            # ready again.
            h.assert_shows("who wrote the first algorithm?")
            h.assert_shows("Ada Lovelace wrote the first algorithm.")
            assert _prompt_row(h) >= 0, f"the editor did not come back\n\n{h.snapshot()}"

            # It really went through the session: the agent's transcript holds
            # the user message and the assistant reply, in that order.
            roles = [getattr(m, "role", "") for m in faux.session.messages]
            assert roles == ["user", "assistant"], f"transcript is {roles!r}"
            assert not faux.session.is_streaming, "the turn never finished"
    finally:
        faux.unregister()


@scenario("chat/error-surface", "A provider error renders as an error message, not a crash", "7.4")
def chat_error_surface() -> None:
    from cortex.ai.providers.faux import faux_assistant_message

    faux = faux_session()
    faux.set_responses(
        [faux_assistant_message("", stop_reason="error", error_message="Provider is overloaded")]
    )
    try:
        with boot_shell(session=faux.session) as h:
            h.type("hello?")
            h.key("enter")

            h.assert_shows("Provider is overloaded")
            # Not a crash: the app is still running and still takes input.
            assert _prompt_row(h) >= 0, f"the editor is gone\n\n{h.snapshot()}"
            h.type("still here")
            h.assert_shows("> still here", scrollback=False)
    finally:
        faux.unregister()


@scenario("chat/abort-turn", "Escape aborts an in-flight turn and says so", "7.4")
def chat_abort_turn() -> None:
    import asyncio

    from cortex.ai.providers.faux import faux_assistant_message

    faux = faux_session()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_response(*_args: Any) -> Any:
        # Hold the turn open until the scenario lets go, so "in-flight" is a
        # state the test controls rather than a race it hopes to win.
        started.set()
        await release.wait()
        return faux_assistant_message("...eventually")

    faux.set_responses([slow_response])
    try:
        with boot_shell(session=faux.session) as h:
            h.type("take your time")
            h.key("enter")
            assert started.is_set(), f"the turn never started\n\n{h.snapshot()}"
            assert faux.session.is_streaming, "the session does not think it is streaming"

            h.key("escape")
            release.set()
            h.settle()

            # The turn ended, and the screen says why.
            assert not faux.session.is_streaming, f"still streaming after Escape\n\n{h.snapshot()}"
            h.assert_shows("Operation aborted")
            last = faux.session.messages[-1]
            assert getattr(last, "stop_reason", "") == "aborted", (
                f"the turn was not recorded as aborted: {getattr(last, 'stop_reason', None)!r}"
            )
    finally:
        faux.unregister()


# ===========================================================================
# 7.5 — streaming
# ===========================================================================


def gated_text_stream(first: str, second: str, gate: Any) -> Any:
    """A stream function that emits `first`, waits on `gate`, then emits `second`.

    "Progressively" is a statement about the middle of a turn, so the middle has
    to be a state the scenario holds open rather than a race it hopes to win. The
    events are the ones a provider really sends (`start`, `text_start`,
    `text_delta`…), so the app is driven through the same path as a real turn.
    """
    from cortex.ai.providers.faux import faux_assistant_message
    from cortex.ai.stream import AssistantMessageEventStream
    from cortex.ai.types import (
        DoneEvent,
        StartEvent,
        TextDeltaEvent,
        TextEndEvent,
        TextStartEvent,
    )

    whole = first + second

    def stream_fn(model: Any, context: Any, options: Any) -> Any:
        import asyncio

        stream = AssistantMessageEventStream()

        async def produce() -> None:
            stream.push(StartEvent(partial=faux_assistant_message("")))
            stream.push(TextStartEvent(content_index=0, partial=faux_assistant_message("")))
            stream.push(
                TextDeltaEvent(content_index=0, delta=first, partial=faux_assistant_message(first))
            )
            await gate.wait()
            stream.push(
                TextDeltaEvent(content_index=0, delta=second, partial=faux_assistant_message(whole))
            )
            stream.push(
                TextEndEvent(content_index=0, content=whole, partial=faux_assistant_message(whole))
            )
            final = faux_assistant_message(whole)
            stream.push(DoneEvent(reason="stop", message=final))
            stream.end(final)

        asyncio.ensure_future(produce())
        return stream

    return stream_fn


@scenario(
    "chat/streaming-incremental", "Assistant text appears progressively while streaming", "7.5"
)
def chat_streaming_incremental() -> None:
    import asyncio

    gate = asyncio.Event()
    faux = faux_session(
        stream_fn=gated_text_stream("Ada Lovelace ", "wrote the first algorithm.", gate)
    )
    try:
        with boot_shell(session=faux.session) as h:
            h.type("who was first?")
            h.key("enter")
            # The streaming redraw is throttled to 100 ms (`text_start` spends
            # the leading edge, the delta lands inside the window), so this is a
            # wait on a real timer rather than on ready callbacks. The gate holds
            # the turn open throughout, so the deadline is what ends this call.
            h.settle(timeout=0.25)

            # Mid-turn: the first delta is on screen and the second has not been
            # sent yet. Both halves matter — without the second assertion this
            # would pass against an app that only draws finished messages, since
            # by then it would have drawn nothing at all.
            assert faux.session.is_streaming, f"the turn is not in flight\n\n{h.snapshot()}"
            h.assert_shows("Ada Lovelace")
            h.assert_hides("wrote the first algorithm")

            gate.set()
            h.settle()

            h.assert_shows("Ada Lovelace wrote the first algorithm.")
            assert not faux.session.is_streaming, "the turn never finished"
    finally:
        faux.unregister()


@scenario("chat/loader-while-busy", "A loader runs during the turn and clears after", "7.5")
def chat_loader_while_busy() -> None:
    import asyncio

    gate = asyncio.Event()
    faux = faux_session(stream_fn=gated_text_stream("thinking it ", "over", gate))
    try:
        with boot_shell(session=faux.session) as h:
            # Nothing is running, so nothing says anything is.
            h.assert_hides("Working...", scrollback=False)

            h.type("take a moment")
            h.key("enter")

            # A spinner frame and the label, in the status band under the chat
            # log and above the editor.
            h.assert_shows("Working...", scrollback=False)
            lines = h.surface().lines()
            loader_row = next(i for i, line in enumerate(lines) if "Working..." in line)
            assert any(frame in lines[loader_row] for frame in ("⠋", "⠙", "⠹", "⠸")), (
                f"the loader has no spinner\n\n{h.snapshot()}"
            )
            assert loader_row < _editor_row(h), f"the loader is below the editor\n\n{h.snapshot()}"

            gate.set()
            h.settle()

            # The turn is over, and the loader went with it.
            h.assert_hides("Working...", scrollback=False)
            h.assert_shows("thinking it over")
    finally:
        faux.unregister()


@scenario("chat/markdown-rendering", "Assistant markdown renders styled, not raw", "7.5")
def chat_markdown_rendering() -> None:
    from cortex.ai.providers.faux import faux_assistant_message

    faux = faux_session()
    faux.set_responses(
        [
            faux_assistant_message(
                "## Ada Lovelace\n\nShe wrote the **first** algorithm.\n\n- one\n- two"
            )
        ]
    )
    try:
        with boot_shell(session=faux.session) as h:
            h.type("tell me about her")
            h.key("enter")
            h.settle()

            # The markdown source is gone: no `##` before the heading, no `**`
            # around the emphasis, and the list bullets are drawn, not typed.
            h.assert_shows("Ada Lovelace", "She wrote the first algorithm.")
            h.assert_hides("## Ada Lovelace", "**first**")

            # And it is *styled*, which is the half a plain-text renderer would
            # also pass: the heading is bold and the emphasised word is too,
            # while the words either side of it are not.
            surface = h.surface()
            heading = _row_containing(h, "Ada Lovelace")
            assert any(cell.style.bold for cell in surface.grid[heading]), (
                f"the heading is not styled\n\n{h.snapshot()}"
            )

            body = _row_containing(h, "She wrote the first algorithm.")
            row = surface.grid[body]
            text = "".join(cell.char for cell in row)
            start = text.index("first")
            assert all(row[i].style.bold for i in range(start, start + len("first"))), (
                f"the emphasised word is not bold\n\n{h.snapshot()}"
            )
            assert not row[start - 2].style.bold, (
                f"the whole line is bold, so nothing is emphasised\n\n{h.snapshot()}"
            )
    finally:
        faux.unregister()


def _row_containing(harness: AppHarness, text: str) -> int:
    """Index of the first visible row holding `text`, for a style assertion."""
    for index, line in enumerate(harness.surface().lines()):
        if text in line:
            return index
    raise AssertionError(f"{text!r} is not on screen\n\n{harness.snapshot()}")


# ===========================================================================
# 7.6 — tools
#
# A tool block is not a message: it is built while the model is still streaming
# the call, updated when execution starts, again for every partial result, and a
# last time when the result lands. So these scenarios drive a *real turn* — the
# faux provider answers with a tool call, the agent loop runs the tool for real,
# and what the block shows is whatever came back — rather than constructing a
# component and calling methods on it. A unit test can prove the component draws
# what it is told; only a turn proves it is told the right things.
# ===========================================================================


def tool_turn(
    tool: Any,
    call_args: dict[str, Any],
    *,
    cwd: str = "/w/project",
    settings: Any = None,
    reply: str = "done.",
) -> FauxSession:
    """A session whose next turn calls *tool* once, then answers with *reply*.

    Two queued responses, because a tool call is two round trips: the model asks
    for the tool, the loop runs it and sends the result back, and the model
    answers. Without the second response the turn would loop.
    """
    from cortex.ai.providers.faux import faux_assistant_message, faux_tool_call

    faux = faux_session(cwd=cwd, settings=settings, tools=[tool])
    faux.set_responses(
        [
            faux_assistant_message(faux_tool_call(tool.name, call_args, {"id": "call-1"})),
            faux_assistant_message(reply),
        ]
    )
    return faux


def echo_tool(text_for: Callable[[dict[str, Any]], str]) -> Any:
    """A tool that answers with whatever *text_for* makes of its arguments."""
    from cortex.agent.types import AgentTool, AgentToolResult
    from cortex.ai.types import TextContent

    async def execute(_tool_call_id: str, params: dict[str, Any], *_rest: Any) -> Any:
        return AgentToolResult(content=[TextContent(text=text_for(params))], details=None)

    return AgentTool(
        name="echo",
        label="echo",
        description="Echo a message back.",
        parameters={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        execute=execute,
    )


@scenario("tools/execution-renders", "A tool call renders with name, args and result", "7.6")
def tools_execution_renders() -> None:
    faux = tool_turn(
        echo_tool(lambda params: f"echoed: {params['message']}"),
        {"message": "hello from the tool"},
    )
    try:
        with boot_shell(session=faux.session) as h:
            h.type("say hello")
            h.key("enter")
            h.settle()

            # The three things a tool block is for, in the order they are drawn:
            # which tool ran, what it was asked, and what it said.
            h.assert_shows("echo")
            h.assert_shows('"message": "hello from the tool"')
            h.assert_shows("echoed: hello from the tool")

            # And it really ran: the transcript holds the call and its result,
            # and the turn carried on to the model's answer afterwards.
            roles = [getattr(m, "role", "") for m in faux.session.messages]
            assert "toolResult" in roles, f"the tool never ran: {roles!r}\n\n{h.snapshot()}"
            h.assert_shows("done.")

            # The block is a *status* line, not just text: the dot in front of
            # the call is coloured, and green now that the call succeeded.
            surface = h.surface()
            dot_rows = [
                i for i, line in enumerate(surface.lines()) if line.lstrip().startswith("●")
            ]
            assert dot_rows, f"no tool status dot on screen\n\n{h.snapshot()}"
            row = surface.grid[dot_rows[-1]]
            dot = next(cell for cell in row if cell.char == "●")
            assert dot.style.fg is not None, f"the status dot is not coloured\n\n{h.snapshot()}"
    finally:
        faux.unregister()


@scenario("tools/diff-renders", "An edit renders as a coloured diff", "7.6")
def tools_diff_renders() -> None:
    import tempfile
    from pathlib import Path

    from cortex.code.tools import create_edit_tool

    with tempfile.TemporaryDirectory() as workdir:
        target = Path(workdir) / "greet.py"
        target.write_text('def greet():\n    print("hello")\n', encoding="utf-8")

        faux = tool_turn(
            create_edit_tool(workdir),
            {
                "path": "greet.py",
                "edits": [{"oldText": 'print("hello")', "newText": 'print("goodbye")'}],
            },
            cwd=workdir,
            reply="renamed the greeting.",
        )
        try:
            with boot_shell(session=faux.session, cwd=workdir, columns=100) as h:
                h.type("change the greeting")
                h.key("enter")
                h.settle()

                # The edit is on screen as a diff, not as "Successfully replaced
                # 1 block(s)": the line that went, the line that came, and a
                # line of context that did neither — each with its marker and
                # the file's own line number.
                h.assert_shows("edit", "greet.py")
                removed = _row_containing(h, 'print("hello")')
                added = _row_containing(h, 'print("goodbye")')
                lines = h.surface().lines()
                assert lines[removed].strip().startswith("-"), (
                    f"the old line is not marked as removed\n\n{h.snapshot()}"
                )
                assert lines[added].strip().startswith("+"), (
                    f"the new line is not marked as added\n\n{h.snapshot()}"
                )
                h.assert_shows("def greet():")

                # And the file really changed — a diff drawn over an edit that
                # did not happen is the failure this catches.
                assert 'print("goodbye")' in target.read_text(encoding="utf-8")

                # Coloured, which is the half a plain-text renderer would pass:
                # the removed line and the added line are not the same colour,
                # and the words that actually changed are picked out inside them.
                surface = h.surface()
                removed_fg = next(c.style.fg for c in surface.grid[removed] if c.char == "-")
                added_fg = next(c.style.fg for c in surface.grid[added] if c.char == "+")
                assert removed_fg is not None and added_fg is not None, (
                    f"the diff is not coloured\n\n{h.snapshot()}"
                )
                assert removed_fg != added_fg, (
                    f"removed and added lines are the same colour\n\n{h.snapshot()}"
                )
                assert any(cell.style.inverse for cell in surface.grid[added]), (
                    f"the changed words are not picked out\n\n{h.snapshot()}"
                )
        finally:
            faux.unregister()


@scenario("tools/bash-renders", "A bash call streams its output into the log", "7.6")
def tools_bash_renders() -> None:
    import tempfile

    from cortex.code.tools import create_bash_tool

    with tempfile.TemporaryDirectory() as workdir:
        faux = tool_turn(
            create_bash_tool(workdir),
            {"command": "echo first-line && echo second-line"},
            cwd=workdir,
            reply="that is the listing.",
        )
        updates: list[Any] = []

        def watch(event: dict[str, Any]) -> None:
            if event.get("type") == "tool_execution_update":
                updates.append(event)

        try:
            with boot_shell(session=faux.session, cwd=workdir) as h:
                faux.session.subscribe(watch)
                h.type("run it for me")
                h.key("enter")
                h.settle()

                # The command the model asked for, and both lines the shell
                # printed, in the log under the prompt.
                h.assert_shows("bash")
                h.assert_shows("echo first-line && echo second-line")
                h.assert_shows("first-line")
                h.assert_shows("second-line")
                h.assert_shows("that is the listing.")

                # It arrived as *output*, not as a finished payload the block
                # was handed at the end: the tool reports partial results while
                # it runs, and the block is what turns those into lines.
                assert updates, f"the block never saw a partial result\n\n{h.snapshot()}"
        finally:
            faux.unregister()


@scenario("tools/output-expand", "Ctrl+O expands and collapses truncated tool output", "7.6")
def tools_output_expand() -> None:
    from cortex.code.config import SettingsManager
    from cortex.code.config.settings_storage import InMemorySettingsStorage

    # `peek` is the display level whose whole point is that the body is off
    # screen until asked for, which is what makes the expand key's effect
    # something a scenario can *see* rather than something it has to read off a
    # flag. (Under `standard` the same key switches a truncated preview for the
    # full result — but truncation lives in the per-tool renderers, which this
    # port does not have; see the notes for 7.6.)
    settings = SettingsManager.from_storage(InMemorySettingsStorage())
    settings.set_tool_output_display("peek")

    faux = tool_turn(
        echo_tool(lambda params: f"the whole answer to {params['message']}"),
        {"message": "everything"},
        settings=settings,
    )
    try:
        with boot_shell(session=faux.session, settings=settings, rows=30) as h:
            h.type("tell me everything")
            h.key("enter")
            h.settle()

            block = _tool_block(h)
            assert not block.expanded, "the block started expanded"
            # The call is on screen; the result it hides is not, and the caret
            # is what says there is something behind it.
            h.assert_shows("echo", "▸")
            h.assert_hides("the whole answer to everything")

            # Ctrl+O does not aim at a block — it flips every one in the log,
            # which is why the app holds the setting and hands it to blocks
            # created later.
            h.key("ctrl+o")
            assert h.app.tool_output_expanded, "the app did not record the expansion"
            assert block.expanded, f"ctrl+o did not expand the block\n\n{h.snapshot()}"
            h.assert_shows("the whole answer to everything", "▾")

            # And it is a toggle, not a one-way door.
            h.key("ctrl+o")
            assert not h.app.tool_output_expanded, "the app did not record the collapse"
            assert not block.expanded, f"ctrl+o did not collapse the block\n\n{h.snapshot()}"
            h.assert_hides("the whole answer to everything", scrollback=False)
    finally:
        faux.unregister()


def _tool_block(harness: AppHarness) -> Any:
    """The last tool block in the chat log."""
    from cortex.code.interactive import ToolExecutionComponent

    blocks = [
        child
        for child in harness.app.chat_container.children
        if isinstance(child, ToolExecutionComponent)
    ]
    assert blocks, f"no tool block in the chat log\n\n{harness.snapshot()}"
    return blocks[-1]


# ===========================================================================
# 7.7 — footer and status
#
# The footer is the one part of the screen with no event of its own: nothing
# tells it a turn cost 300 tokens or that HEAD moved, it re-derives everything
# from the session and the data provider on every frame. So these scenarios
# drive the app and read the two footer lines back — the only way to tell a
# footer that reports from one that was handed a snapshot at boot, which is
# exactly what 7.2 shipped.
# ===========================================================================


def _footer_lines(harness: AppHarness) -> list[str]:
    """The footer's two fixed lines: identity, then session vitals."""
    lines = harness.surface().lines()
    for index, line in enumerate(lines):
        if line.startswith(f"{BRAND_MARK} "):
            return [text.rstrip() for text in lines[index : index + 2]]
    raise AssertionError(f"no footer on screen\n\n{harness.snapshot()}")


@scenario("footer/model-and-tokens", "Footer shows the active model and token usage", "7.7")
def footer_model_and_tokens() -> None:
    from cortex.ai.providers.faux import faux_assistant_message

    faux = faux_session()
    faux.set_responses([faux_assistant_message("She wrote the first algorithm.")])
    try:
        with boot_shell(session=faux.session) as h:
            # The model answering is named before anything has been asked of it,
            # and nothing has been spent yet — no arrows on the vitals line.
            _, vitals = _footer_lines(h)
            assert vitals.endswith("faux-1"), f"the model is not named: {vitals!r}"
            assert "↑" not in vitals and "↓" not in vitals, (
                f"tokens are counted before the first turn: {vitals!r}"
            )

            h.type("who was ada?")
            h.key("enter")
            h.settle()

            # The turn is paid for: sent and received counts, both non-zero, and
            # the model is still named.
            _, vitals = _footer_lines(h)
            sent = re.search(r"↑([\d.]+k?)", vitals)
            received = re.search(r"↓([\d.]+k?)", vitals)
            assert sent and received, f"the turn's tokens are not on the footer: {vitals!r}"
            assert float(sent.group(1).rstrip("k")) > 0, f"nothing was sent: {vitals!r}"
            assert float(received.group(1).rstrip("k")) > 0, f"nothing came back: {vitals!r}"
            assert vitals.endswith("faux-1"), f"the model went missing mid-turn: {vitals!r}"
    finally:
        faux.unregister()


@scenario("footer/git-branch", "Footer shows the git branch it is working on", "7.7")
def footer_git_branch() -> None:
    """The dirty mark in this scenario's original title has no source to port.

    `brand.ts` defines `GIT_DIRTY_MARK` and nothing in the TS ever renders it —
    the footer shows the branch and stops there. Feature parity means this does
    too; inventing a `git status` call here would be an enhancement, and the
    corpus is not the place to smuggle one in.
    """
    import tempfile

    from cortex.code.interactive import GIT_BRANCH_GLYPH

    with tempfile.TemporaryDirectory() as parent:
        repo = os.path.join(parent, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        with open(os.path.join(repo, ".git", "HEAD"), "w") as handle:
            handle.write("ref: refs/heads/parity\n")

        faux = faux_session(cwd=repo)
        try:
            with boot_shell(cwd=repo, session=faux.session) as h:
                identity, _ = _footer_lines(h)
                assert f"{GIT_BRANCH_GLYPH} parity" in identity, (
                    f"the branch is not on the footer: {identity!r}\n\n{h.snapshot()}"
                )
        finally:
            faux.unregister()

    # And a directory that is not a repository says nothing at all, rather than
    # showing an empty glyph or the branch of whatever repo the app was started
    # from.
    with boot_shell() as h:
        identity, _ = _footer_lines(h)
        assert GIT_BRANCH_GLYPH not in identity, f"a branch outside a repo: {identity!r}"


@scenario("footer/context-meter", "Footer shows remaining context budget", "7.7")
def footer_context_meter() -> None:
    from cortex.ai.providers.faux import faux_assistant_message

    # An untouched session against the default model: the gauge is empty, and it
    # says how big the window is and where auto-compaction will trip.
    with boot_shell() as h:
        _, vitals = _footer_lines(h)
        assert vitals.startswith("▱▱▱▱▱▱▱▱ 0.0% 128k auto@"), (
            f"the context meter is not at rest: {vitals!r}\n\n{h.snapshot()}"
        )

    # Now a model with a 2k window, so one affordable turn is a visible slice of
    # it: the gauge fills and the percentage follows.
    faux = faux_session(models=[{"id": "faux-small", "context_window": 2000, "max_tokens": 512}])
    faux.set_responses([faux_assistant_message("She wrote the first algorithm. " * 40)])
    try:
        with boot_shell(session=faux.session) as h:
            h.type("who was ada?")
            h.key("enter")
            h.settle()

            _, vitals = _footer_lines(h)
            assert vitals.startswith("▰"), (
                f"the turn did not move the gauge: {vitals!r}\n\n{h.snapshot()}"
            )
            percent = re.search(r"([\d.]+)% 2\.0k", vitals)
            assert percent, f"the meter has no percentage of a 2k window: {vitals!r}"
            assert 0 < float(percent.group(1)) < 100, f"implausible context fill: {vitals!r}"
    finally:
        faux.unregister()


# ===========================================================================
# 7.8 — slash commands
#
# Two of these carry a name hoocode does not have, and both are ported to the
# command that does the thing rather than to the name. There is no `/help` in
# `slash-commands.ts`; the built-in commands are listed by the `/` menu, which
# `commands/slash-autocomplete` already checks, and the reference card the user
# reaches for is `/hotkeys`. `/clear` is the harder one — see its scenario.
# ===========================================================================


def _menu_rows(harness: AppHarness) -> list[str]:
    """The autocomplete rows, selection marker stripped.

    The menu is drawn between the editor's lower border and the footer, one row
    per suggestion, with `→ ` on the selected one and two spaces on the rest.

    A menu longer than the visible window ends with a `(n/m)` scroll counter,
    which is chrome rather than a suggestion — it appeared here the moment 7.9
    took the command table past the window's height.
    """
    lines = [line.rstrip() for line in harness.surface().lines()]
    footer = next((i for i, line in enumerate(lines) if line.startswith("⬢ ")), len(lines))
    borders = [i for i, line in enumerate(lines[:footer]) if line and set(line) == {"─"}]
    if not borders:
        return []
    rows = [line.lstrip("→ ") for line in lines[borders[-1] + 1 : footer] if line.strip()]
    return [row for row in rows if not re.fullmatch(r"\(\d+/\d+\)", row)]


@scenario("commands/slash-autocomplete", "Typing `/` opens the command autocomplete", "7.8")
def commands_slash_autocomplete() -> None:
    """`/` opens the menu, typing filters it, Escape closes it.

    The menu is asynchronous — the editor debounces, then runs the provider as a
    task — so every assertion goes through `wait_for`, which is the difference
    between checking the screen and checking the frame that happened to be up
    when the key was released.
    """
    from cortex.code.interactive import BUILTIN_SLASH_COMMANDS

    advertised = {command.name for command in BUILTIN_SLASH_COMMANDS}

    with boot_shell() as h:
        h.type("/")
        h.wait_for(lambda: len(_menu_rows(h)) > 1)

        rows = _menu_rows(h)
        # Every row is a built-in command, with the description the table gives
        # it — the menu is the table, not a list this scenario wrote down.
        names = [row.split()[0] for row in rows]
        assert names, f"the menu opened empty\n\n{h.snapshot()}"
        unknown = [name for name in names if name not in advertised]
        assert not unknown, f"the menu offers commands that are not built in: {unknown!r}"

        # `/hotkeys` is offered — reached by filtering rather than by looking
        # for it in the open menu, because since 7.9 the table is longer than
        # the window and the last rows are below the fold.
        h.type("hot")
        h.wait_for(lambda: len(_menu_rows(h)) == 1)
        assert _menu_rows(h)[0].startswith("hotkeys "), (
            f"the menu did not filter to /hotkeys: {_menu_rows(h)!r}\n\n{h.snapshot()}"
        )
        h.assert_shows("Show all keyboard shortcuts", scrollback=False)

        # Typing filters it down to the one match, and the editor still holds
        # what was typed.
        for _ in range(3):
            h.key("backspace")
        h.type("sess")
        h.wait_for(lambda: len(_menu_rows(h)) == 1)
        assert _menu_rows(h)[0].startswith("session "), (
            f"the menu did not filter to /session: {_menu_rows(h)!r}\n\n{h.snapshot()}"
        )
        h.assert_shows("> /sess", scrollback=False)

        # Escape closes the menu without submitting or clearing the line.
        h.key("escape")
        h.wait_for(lambda: not _menu_rows(h))
        h.assert_shows("> /sess", scrollback=False)


@scenario("commands/help", "`/hotkeys` lists the keyboard shortcuts", "7.8")
def commands_help() -> None:
    """hoocode has no `/help`, and this scenario's original name is the only
    place one was ever mentioned.

    `slash-commands.ts` lists twenty-two commands and none of them is `help`;
    what lists the built-in commands is the `/` menu, which the scenario above
    checks against the table itself. The command a user reaches for when they
    want to be told how to drive the app is `/hotkeys`, and that is what this
    ports — `CommandExecutor.handle_hotkeys`, keys and all. Inventing a `/help`
    alias would be an enhancement, and this migration does not do those.
    """
    with boot_shell() as h:
        h.type("/hotkeys")
        h.key("enter")

        # The card renders as markdown: a heading per section, and the keys read
        # back off the live keybindings rather than written into the table.
        h.assert_shows("Keyboard Shortcuts", "Navigation", "Editing", "Send message")
        h.assert_shows("Slash commands", "Run bash command")
        # And the command consumed the line rather than sending it to the model.
        assert _prompt_row(h) >= 0, f"/hotkeys left the editor dirty\n\n{h.snapshot()}"
        h.assert_hides("> /hotkeys")


@scenario("commands/clear", "`/new` empties the chat log", "7.10")
def commands_clear() -> None:
    """hoocode's command for a clean chat log is `/new`, and it means it.

    There is no `/clear` in `slash-commands.ts`: what empties the screen is a
    *new session*, and the handler is named `handleClear` for what the user sees
    rather than for what it does. So this checks both halves — the transcript is
    gone from the screen, and the session behind it is a different one, with the
    old turn no longer in the agent's context.
    """
    from cortex.ai.providers.faux import faux_assistant_message

    faux = faux_session()
    faux.set_responses([faux_assistant_message("Ada Lovelace wrote the first algorithm.")])
    try:
        with boot_shell(session=faux.session) as h:
            h.type("who wrote the first algorithm?")
            h.key("enter")
            h.assert_shows("Ada Lovelace wrote the first algorithm.")
            first_session_id = h.app.session.session_id

            h.type("/new")
            h.key("enter")
            h.wait_for(lambda: h.contains("New session started", scrollback=False))

            # The turn is off the *screen* — not merely scrolled away, which is
            # why this asks the viewport rather than the transcript.
            h.assert_hides("who wrote the first algorithm?", scrollback=False)
            h.assert_hides("Ada Lovelace wrote the first algorithm.", scrollback=False)

            # And it is a different session: `/new` replaces it rather than
            # clearing a container, so the next question starts from nothing.
            assert h.app.session.session_id != first_session_id, "the session was not replaced"
            assert h.app.session.messages == [], "the new session inherited the old transcript"
            # The editor is back and usable.
            assert _prompt_row(h) >= 0, f"the editor did not come back\n\n{h.snapshot()}"
    finally:
        faux.unregister()


@scenario("commands/file-mention", "`@` opens file autocomplete and inserts a path", "7.8")
def commands_file_mention() -> None:
    """`@` searches with `fd`, and this scenario brings its own.

    The provider shells out to `fd` and has no fallback — `fd_path` unset means
    no `@` completions at all, which is the state hoocode itself is in until its
    background download settles. A scenario cannot depend on the machine running
    it having `fd` installed, so it plants a stub that answers with a fixed
    listing: what is under test here is the app wiring `@` to the provider and
    the provider's answer reaching the line, not fd's own search.
    """
    import stat
    import tempfile

    with tempfile.TemporaryDirectory() as workspace:
        fd_stub = os.path.join(workspace, "fd")
        with open(fd_stub, "w") as handle:
            handle.write("#!/bin/sh\nprintf '%s\\n' src/ src/renderer.py README.md\n")
        os.chmod(fd_stub, os.stat(fd_stub).st_mode | stat.S_IXUSR)

        with boot_shell(fd_path=fd_stub) as h:
            h.type("look at @rend")
            h.wait_for(lambda: bool(_menu_rows(h)))

            rows = _menu_rows(h)
            assert any("renderer.py" in row for row in rows), (
                f"the file menu does not offer the match: {rows!r}\n\n{h.snapshot()}"
            )

            # Tab accepts the selection, and the path lands in the line in place
            # of the `@rend` that was being typed — the rest of the line intact.
            h.key("tab")
            h.wait_for(lambda: h.contains("@src/renderer.py", scrollback=False))
            h.assert_shows("> look at @src/renderer.py", scrollback=False)


# ===========================================================================
# 7.9 — overlays and selectors
# ===========================================================================


def _overlay_open(harness: AppHarness) -> bool:
    """Whether an overlay has taken the editor's place.

    Asked of the app rather than of the screen, which is unusual here and
    deliberate: an overlay *replaces* the editor in its container, and both the
    editor and a selector's search box draw a `>` prompt, so the screen cannot
    tell the two apart. What the screen can show — and what each scenario
    checks besides this — is the overlay's own content, and that a keystroke
    after Escape reaches the editor again.
    """
    app = harness.app
    return app.editor not in app.editor_container.children


def _two_faux_models() -> list[Any]:
    """Two models to switch between. One is not a choice."""
    from cortex.ai.providers.faux import FauxModelDefinition

    return [
        FauxModelDefinition(
            id=f"faux-{index}",
            name=f"Faux {index}",
            reasoning=False,
            input=["text"],
            cost={"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
            context_window=128000,
            max_tokens=16384,
        )
        for index in (1, 2)
    ]


@scenario("overlay/model-selector", "`/model` switches model and the footer follows", "7.9")
def overlay_model_selector() -> None:
    """`/model` opens the picker, Enter takes a row, and the footer follows.

    The session is booted with a registry over two faux models, because "switch
    model" needs something to switch *to* — the real registry resolves
    credentials and arrives in 7.11, and this is the half of it the overlay
    asks for.
    """
    from cortex.code.config import SettingsManager
    from cortex.code.config.settings_storage import InMemorySettingsStorage

    settings = SettingsManager.from_storage(InMemorySettingsStorage())
    faux = faux_session(
        cwd="/w/project",
        settings=settings,
        models=_two_faux_models(),
        with_model_registry=True,
    )
    try:
        with boot_shell(settings=settings, session=faux.session) as h:
            h.assert_shows("faux-1", scrollback=False)

            h.type("/model")
            h.key("enter")
            h.wait_for(lambda: _overlay_open(h))

            # The overlay lists both models and says which one is in use.
            h.assert_shows("faux-1", "faux-2", scrollback=False)

            # Down then Enter takes the *other* model, which is the whole point:
            # picking the one already in use would prove nothing.
            h.key("down")
            h.key("enter")
            h.wait_for(lambda: faux.session.model.id == "faux-2")

            # The overlay closed, the editor is back, and the footer names the
            # model that is now in use rather than the one that was.
            h.wait_for(lambda: not _overlay_open(h))
            h.wait_for(lambda: h.contains("faux-2", scrollback=False))
            h.assert_shows("Model: faux-2")
            assert settings.get_default_model() == "faux-2", (
                "the switch did not reach the settings manager"
            )
    finally:
        faux.unregister()


@scenario("overlay/session-selector", "`/resume` lists sessions and loads one", "7.10")
def overlay_session_selector() -> None:
    """`/resume` is a picker you can pick from, which is the whole point of it.

    Listing sessions and loading one are two different pieces of machinery — the
    list is `session-selector.ts` over `SessionManager.list`, the load is the
    runtime's `switch_session` plus `render_current_session_state` — and a
    scenario that only opened the overlay would pass over either one alone. So
    this drives it end to end: two sessions on disk, open the list, pick the
    other one, and read its transcript off the screen.
    """
    with session_workspace() as workspace:
        write_session(workspace, "how do I list files?", "Use ls.")
        write_session(workspace, "how do I count lines?", "Use wc -l.")

        faux = faux_session(cwd=workspace.cwd, session_manager=workspace.new_manager())
        try:
            with boot_shell(cwd=workspace.cwd, session=faux.session) as h:
                h.type("/resume")
                h.key("enter")
                h.wait_for(lambda: h.contains("Resume Session", scrollback=False))

                # Both sessions are offered, by their first user message.
                h.wait_for(lambda: h.contains("how do I list files?", scrollback=False))
                h.assert_shows("how do I count lines?", scrollback=False)

                # Search down to one row and take it. Typing goes to the
                # overlay's search box, which is what the list filters on.
                h.type("count lines")
                h.wait_for(lambda: not h.contains("how do I list files?", scrollback=False))
                h.key("enter")

                # The picked session is on screen, both sides of it, and the
                # overlay is gone.
                h.wait_for(lambda: h.contains("Use wc -l.", scrollback=False))
                h.assert_shows("how do I count lines?", "Use wc -l.", scrollback=False)
                assert not _overlay_open(h), f"the overlay stayed open\n\n{h.snapshot()}"
                assert _prompt_row(h) >= 0, f"the editor did not come back\n\n{h.snapshot()}"
        finally:
            faux.unregister()


@scenario("overlay/settings", "`/settings` opens settings and a change persists", "7.9")
def overlay_settings() -> None:
    """`/settings` opens the settings list, and a row that is changed stays changed.

    Tool output display is the row under test because it is a leaf setting with
    an effect on both sides: the settings manager records it, and the tool blocks
    already in the transcript re-render at the new level. The check is that the
    *setting* took — reopening the overlay shows the new value, which is what a
    user means by "it stuck".
    """
    from cortex.code.config import SettingsManager
    from cortex.code.config.settings_storage import InMemorySettingsStorage

    settings = SettingsManager.from_storage(InMemorySettingsStorage())
    assert settings.get_tool_output_display() == "standard", (
        "this scenario assumes the default it is about to change"
    )

    with boot_shell(settings=settings) as h:
        h.type("/settings")
        h.key("enter")
        h.wait_for(lambda: _overlay_open(h))
        h.assert_shows("Auto-compact", "Tool output display", scrollback=False)

        # The list searches: typing narrows it to the row, Enter cycles its
        # value. No space in the query — space is the activate key in a settings
        # list, so it never reaches the search box.
        h.type("tooloutput")
        h.wait_for(lambda: not h.contains("Auto-compact", scrollback=False))
        h.assert_shows("Tool output display", scrollback=False)
        h.key("enter")
        h.wait_for(lambda: settings.get_tool_output_display() == "collapsed")

        # Escape closes the overlay; reopening it shows the value that was set,
        # not the one it opened on the first time.
        h.key("escape")
        h.wait_for(lambda: not _overlay_open(h))

        h.type("/settings")
        h.key("enter")
        h.wait_for(lambda: _overlay_open(h))
        h.type("tooloutput")
        h.wait_for(lambda: not h.contains("Auto-compact", scrollback=False))
        h.assert_shows("collapsed", scrollback=False)


@scenario("overlay/escape-closes", "Escape closes an overlay and restores editor focus", "7.9")
def overlay_escape_closes() -> None:
    """Escape out of an overlay and the editor has the keyboard again.

    Focus is the part worth checking on the screen rather than in a field: an
    overlay that is gone but still holding the keyboard looks exactly like one
    that closed properly, right up until the next thing you type disappears.
    """
    with boot_shell() as h:
        h.type("/settings")
        h.key("enter")
        h.wait_for(lambda: _overlay_open(h))
        # The overlay is on screen and the editor is not.
        h.assert_shows("Auto-compact", scrollback=False)

        h.key("escape")
        h.wait_for(lambda: not _overlay_open(h))

        # The editor is back, empty (the command consumed the line), and it is
        # the thing receiving keystrokes.
        h.type("after")
        h.wait_for(lambda: h.contains("> after", scrollback=False))


# ===========================================================================
# 7.10 — sessions
#
# These are the only scenarios that touch the filesystem, and they bring their
# own directory: a session that survives a restart has to be a real file, and a
# scenario must not leave one on the machine that ran it.
# ===========================================================================


def _role_of(message: Any) -> str:
    """Role of a message, model or dict."""
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "role", ""))


@dataclass
class SessionWorkspace:
    """A throwaway project directory and the session directory beside it."""

    cwd: str
    session_dir: str

    def new_manager(self) -> Any:
        """A fresh persisted session in this workspace."""
        from cortex.code.session import SessionManager

        return SessionManager(self.cwd, self.session_dir)


@contextmanager
def session_workspace() -> Generator[SessionWorkspace]:
    """A real cwd and session directory, removed afterwards.

    The cwd has to exist rather than be a fixed string like the other scenarios
    use: the runtime refuses to open a session whose recorded directory is gone,
    which is exactly the check `switch_session` makes.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as root:
        cwd = os.path.join(root, "project")
        session_dir = os.path.join(root, "sessions")
        os.makedirs(cwd)
        os.makedirs(session_dir)
        yield SessionWorkspace(cwd=cwd, session_dir=session_dir)


def write_session(workspace: SessionWorkspace, question: str, answer: str) -> str:
    """One finished exchange, written to its own session file. Returns the path.

    Both halves are needed: `SessionManager` does not flush a session to disk
    until it has an assistant message, so a question on its own is not a session
    anything can resume.
    """
    from cortex.ai.providers.faux import faux_assistant_message

    manager = workspace.new_manager()
    manager.append_message({"role": "user", "content": question, "timestamp": 1})
    manager.append_message(faux_assistant_message(answer).model_dump())
    path = manager.get_session_file()
    assert path is not None
    return path


@scenario("session/resume", "`--continue` restores the previous transcript on screen", "7.10")
def session_resume() -> None:
    """`pycortex --continue` opens *on* the last session, not next to it.

    The flag is resolved before the app exists — `resolve_session_manager` picks
    the file, the session is built over it, and `init()` draws whatever it finds
    — so what this checks is that the transcript is on the first frame rather
    than something a later command has to fetch.
    """
    with session_workspace() as workspace:
        write_session(workspace, "what is a monad?", "A monoid in the category of endofunctors.")

        # Exactly what `main` does for `--continue`.
        manager = resolve_session_manager(
            workspace.cwd, continue_session=True, session_dir=workspace.session_dir
        )
        faux = faux_session(cwd=workspace.cwd, session_manager=manager)
        try:
            with boot_shell(cwd=workspace.cwd, session=faux.session) as h:
                h.assert_shows("what is a monad?")
                h.assert_shows("A monoid in the category of endofunctors.")
                # Restored, not re-asked: the agent holds both messages already,
                # and the editor is empty and waiting.
                # The agent holds the restored conversation too, not just the
                # screen: the next question is asked with this history behind it.
                assert [_role_of(m) for m in h.app.session.messages] == ["user", "assistant"], (
                    "the restored transcript did not reach the agent's context"
                )
                assert _prompt_row(h) >= 0, f"the editor is not ready\n\n{h.snapshot()}"

                # And Up-arrow walks back through what was asked in it, which is
                # the half of a resume that is not on the screen.
                h.key("up")
                h.assert_shows("> what is a monad?", scrollback=False)
        finally:
            faux.unregister()


@scenario("session/persist-across-restart", "A turn survives quit and relaunch", "7.10")
def session_persist_across_restart() -> None:
    """The whole round trip, driven through the app both times.

    Nothing here writes a session file by hand: the first app is asked a
    question, answers it, and is quit with Ctrl+C twice; the second app is
    started the way `--continue` starts one and has to show the same exchange.
    That is the difference between this and `session/resume` — there, the file
    was a fixture; here, the app produced it.
    """
    from cortex.ai.providers.faux import faux_assistant_message

    with session_workspace() as workspace:
        first = faux_session(cwd=workspace.cwd, session_manager=workspace.new_manager())
        first.set_responses([faux_assistant_message("Grace Hopper wrote the first compiler.")])
        try:
            exits: list[int] = []
            with boot_shell(cwd=workspace.cwd, session=first.session, on_exit=exits.append) as h:
                h.type("who wrote the first compiler?")
                h.key("enter")
                h.assert_shows("Grace Hopper wrote the first compiler.")

                h.key("ctrl+c")
                h.key("ctrl+c")
                assert exits == [0], f"the app did not quit: {exits!r}"
        finally:
            first.unregister()

        # A second process would resolve the file the same way `--continue` does.
        manager = resolve_session_manager(
            workspace.cwd, continue_session=True, session_dir=workspace.session_dir
        )
        second = faux_session(cwd=workspace.cwd, session_manager=manager)
        try:
            with boot_shell(cwd=workspace.cwd, session=second.session) as h:
                h.assert_shows("who wrote the first compiler?")
                h.assert_shows("Grace Hopper wrote the first compiler.")
        finally:
            second.unregister()


# ===========================================================================
# 7.11 — auth and models
# ===========================================================================


@scenario("auth/login-dialog", "`/login` opens the provider picker", "7.11")
def auth_login_dialog() -> None:
    """`/login`, three screens deep, over the *real* registry.

    `FauxModelRegistry` is deliberately not used here: what this scenario is
    about is the machinery that decides which providers you can log into, and a
    stand-in that answers "all of them" would prove nothing. So the session gets
    a real `ModelRegistry` over an in-memory `AuthStorage` — every built-in
    provider, and no credentials for any of them.
    """
    from cortex.code.config import AuthStorage, ModelRegistry, SettingsManager
    from cortex.code.config.settings_storage import InMemorySettingsStorage

    settings = SettingsManager.from_storage(InMemorySettingsStorage())
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    faux = faux_session(cwd="/w/project", settings=settings, model_registry=registry)
    try:
        with boot_shell(settings=settings, session=faux.session) as h:
            h.type("/login")
            h.key("enter")
            h.wait_for(lambda: _overlay_open(h))

            # Screen one: how do you want to authenticate.
            h.assert_shows(
                "Select authentication method:",
                "Use a subscription",
                "Use an API key",
                scrollback=False,
            )

            # Screen two: which provider. Down+Enter takes "Use an API key",
            # because that is the branch nearly every provider is on.
            h.key("down")
            h.key("enter")
            h.wait_for(lambda: "Select provider to configure:" in h.snapshot())

            # The rows are real providers with a real auth column, and with no
            # credentials anywhere that column says so rather than being blank.
            h.assert_shows("Select provider to configure:", "unconfigured", scrollback=False)

            # Escape goes *back*, not out — the auth-type screen is reachable
            # again after a mis-step.
            h.key("escape")
            h.wait_for(lambda: "Select authentication method:" in h.snapshot())

            # Escape again closes, and the editor has the keyboard back.
            h.key("escape")
            h.wait_for(lambda: not _overlay_open(h))
            h.type("back in the editor")
            h.assert_shows("> back in the editor", scrollback=False)
    finally:
        faux.unregister()


@contextmanager
def github_device_endpoint(
    *, user_code: str = "ABCD-1234", verification_uri: str = "https://github.com/login/device"
) -> Generator[list[str]]:
    """GitHub's device-flow endpoints, on the near side of `httpx`.

    The provider builds its URLs from the domain (`https://github.com/...`), so
    there is no `base_url` override to point at a local server the way
    `models.json` lets :class:`AnthropicApiStub` be reached. The tightest seam
    left is the one function that makes the call: everything above it —
    `_start_device_flow`'s field validation, `login_github_copilot`, the
    callbacks it invokes, the dialog they draw — is the real code.

    The access-token endpoint answers `authorization_pending` forever, which is
    what GitHub really says while the user is still on the device page: the login
    stays on the waiting screen until the scenario cancels it.

    Yields the list of URLs called, so a scenario can assert the flow got as far
    as asking.
    """
    from cortex.ai.oauth import github_copilot

    module: Any = github_copilot
    called: list[str] = []
    original = module._fetch_json

    async def fake_fetch_json(url: str, **_kwargs: Any) -> Any:
        called.append(url)
        if url.endswith("/login/device/code"):
            return {
                "device_code": "device-code",
                "user_code": user_code,
                "verification_uri": verification_uri,
                "interval": 1,
                "expires_in": 900,
            }
        return {"error": "authorization_pending"}

    module._fetch_json = fake_fetch_json
    try:
        yield called
    finally:
        module._fetch_json = original


@scenario("auth/copilot-device-code", "`/login` to GitHub Copilot reaches the device code", "7.11")
def auth_copilot_device_code() -> None:
    """The whole subscription login, down to the code the user has to type in.

    Every OAuth provider used to die on the first callback: they invoked
    `on_auth`/`on_prompt` with dict literals while `login_controller` reads
    `info.url` and `prompt.message`, so `/login` ended in "Failed to login to
    GitHub Copilot: 'dict' object has no attribute 'message'". Nothing below the
    controller could catch that — the dialog is the only thing that reads those
    objects — which is why this is a scenario and not a unit test.

    So: three screens to the provider, answer its enterprise-domain prompt, and
    the device code has to be on screen with no error toast anywhere. Escape then
    cancels a *polling* flow, which must also be quiet: "Login cancelled" is
    something the user did on purpose.
    """
    from cortex.code.config import AuthStorage, ModelRegistry, SettingsManager
    from cortex.code.config.settings_storage import InMemorySettingsStorage

    settings = SettingsManager.from_storage(InMemorySettingsStorage())
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    faux = faux_session(cwd="/w/project", settings=settings, model_registry=registry)
    try:
        with (
            github_device_endpoint() as called,
            boot_shell(settings=settings, session=faux.session) as h,
        ):
            h.type("/login")
            h.key("enter")
            h.wait_for(lambda: _overlay_open(h))

            # Screen one: "Use a subscription" is the first row, so Enter takes it.
            h.assert_shows("Select authentication method:", "Use a subscription", scrollback=False)
            h.key("enter")
            h.wait_for(lambda: "Select provider to configure:" in h.snapshot())

            # Screen two: the subscription providers, sorted by name — Anthropic,
            # GitHub Copilot, OpenAI Codex — so one Down lands on Copilot.
            h.assert_shows("GitHub Copilot", scrollback=False)
            h.key("down")
            h.key("enter")

            # Screen three is the provider's own: its first callback is the
            # enterprise-domain prompt, and reaching it at all is the fix.
            h.wait_for(lambda: h.contains("GitHub Enterprise URL", scrollback=False), timeout=5)
            h.assert_hides("Failed to login", scrollback=False)

            # Blank means github.com, which starts the device flow.
            h.key("enter")
            h.wait_for(lambda: h.contains("ABCD-1234", scrollback=False), timeout=5)

            # The device-code screen: the URL to visit, the code to type, and the
            # line that says the app is now polling.
            h.assert_shows(
                "https://github.com/login/device",
                "Enter code: ABCD-1234",
                "Waiting for browser authentication...",
                scrollback=False,
            )
            assert any(url.endswith("/login/device/code") for url in called), (
                f"the device flow was never started: {called!r}"
            )

            # No toast, at any point — not on the prompt, not on the code.
            h.assert_hides("Failed to login")

            # Escape backs out of a flow that is mid-poll, and says nothing.
            h.key("escape")
            h.wait_for(lambda: not _overlay_open(h), timeout=5)
            h.assert_hides("Failed to login", "Login cancelled")
            h.type("back in the editor")
            h.assert_shows("> back in the editor", scrollback=False)
    finally:
        faux.unregister()


@scenario(
    "auth/missing-key-message", "A missing API key explains itself instead of crashing", "7.11"
)
def auth_missing_key_message() -> None:
    """Enter, on a real provider with no key: a message, and a usable app.

    This is the screen a fresh install shows, and the whole point is what it is
    *not* — a traceback, and a dead prompt. The preflight raises before the
    provider is ever reached, the message names `/login`, and the editor still
    takes the next keystroke.
    """
    from cortex.ai.models import get_models
    from cortex.code.config import AuthStorage, ModelRegistry, SettingsManager
    from cortex.code.config.settings_storage import InMemorySettingsStorage

    settings = SettingsManager.from_storage(InMemorySettingsStorage())
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    # A real provider's model, with no credentials for it. The faux model would
    # not do: its provider needs no key, so the preflight would pass.
    faux = faux_session(
        cwd="/w/project",
        settings=settings,
        model_registry=registry,
        model=get_models("anthropic")[0],
    )
    try:
        with boot_shell(settings=settings, session=faux.session) as h:
            h.type("hello")
            h.key("enter")
            h.wait_for(lambda: "No API key found" in h.snapshot())

            # It names the provider, points at `/login`, and points at the docs —
            # `format_no_api_key_found_message`, reached through the real
            # preflight rather than called directly.
            h.assert_shows("No API key found for anthropic", "Use /login to log into a provider")

            # Not a crash: the app is still up and the editor still takes input.
            h.type("still alive")
            h.assert_shows("> still alive", scrollback=False)
    finally:
        faux.unregister()


# ===========================================================================
# 7.12 — the whole product
#
# The one scenario that fakes nothing above the socket. Every other scenario
# swaps the provider out (`faux_session`) and hands the app a settings manager
# with no file behind it, because they are about what the screen does. This one
# is about what a person gets when they install the thing and run it, so the
# only stand-in is the endpoint: a clean config directory, the real registry
# over it, the real Anthropic provider, and an HTTP server on localhost where
# api.anthropic.com would be.
# ===========================================================================


@dataclass
class CleanInstall:
    """A machine with the tool freshly installed on it, and nothing else."""

    #: The project the user is running in.
    cwd: str
    #: `HOOCODE_CODING_AGENT_DIR` for this run: no settings, no auth, no sessions.
    agent_dir: str
    #: The endpoint `models.json` points `anthropic` at.
    api: Any

    def sessions_written(self) -> list[str]:
        """Session files this install has produced, newest last."""
        root = os.path.join(self.agent_dir, "sessions")
        if not os.path.isdir(root):
            return []
        found = [
            os.path.join(parent, name)
            for parent, _dirs, files in os.walk(root)
            for name in files
            if name.endswith(".jsonl")
        ]
        return sorted(found)


@contextmanager
def clean_install(
    *,
    reply: str = "Hello from the stand-in.",
    env_key: str | None = "sk-ant-first-run",
    stored_keys: dict[str, str] | None = None,
) -> Generator[CleanInstall]:
    """A fresh config directory, one credential, and an endpoint that answers.

    What a first run really is, minus the network: nothing in the agent
    directory, so settings, `auth.json` and the session directory are all
    created by the run itself; `ANTHROPIC_API_KEY` in the environment, which is
    the fastest of the ways the TS lets a new user supply a key; and a
    `models.json` pointing the provider at :class:`AnthropicApiStub`, which is
    the same override a user aims at a gateway.

    `env_key` and `stored_keys` are what let a scenario pick *which* of those
    ways supplies the credential. They are not interchangeable, which is the
    whole point of `e2e/stored-key`: an environment key is found by the provider
    itself, so a request reaches the endpoint with it even when nothing above the
    provider passed one — and that is exactly what hid the missing `stream_fn`
    wrapper. `stored_keys` writes `auth.json` the way `/login` does, which is the
    path that only works if the session resolves the key and puts it on the
    request.

    The environment is restored afterwards — the corpus runs in one process, and
    a scenario that leaked `HOOCODE_CODING_AGENT_DIR` would point every scenario
    after it at a directory that no longer exists.
    """
    import tempfile

    from cortex.code.config import ENV_AGENT_DIR
    from cortex.code.e2e._api_stub import AnthropicApiStub

    previous = {name: os.environ.get(name) for name in (ENV_AGENT_DIR, "ANTHROPIC_API_KEY", "HOME")}
    with tempfile.TemporaryDirectory() as root, AnthropicApiStub(reply=reply) as api:
        agent_dir = os.path.join(root, "config")
        cwd = os.path.join(root, "project")
        os.makedirs(agent_dir)
        os.makedirs(cwd)
        with open(os.path.join(agent_dir, "models.json"), "w", encoding="utf-8") as handle:
            json.dump({"providers": {"anthropic": {"base_url": api.base_url}}}, handle)

        if stored_keys:
            auth_path = os.path.join(agent_dir, "auth.json")
            with open(auth_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        provider: {"type": "api_key", "key": key}
                        for provider, key in stored_keys.items()
                    },
                    handle,
                )
            os.chmod(auth_path, 0o600)

        os.environ[ENV_AGENT_DIR] = agent_dir
        if env_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = env_key
        # Nothing should read `HOME` once the agent dir is set; pointing it at the
        # sandbox too means a component that does writes there rather than into a
        # real home directory.
        os.environ["HOME"] = root
        try:
            yield CleanInstall(cwd=cwd, agent_dir=agent_dir, api=api)
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


@scenario("e2e/first-run", "A clean install boots, prompts, answers and exits cleanly", "7.12")
def e2e_first_run() -> None:
    """Install, run, ask, quit — with the provider code in the loop.

    The startup is `main`'s, not a copy of it: `resolve_session_manager` picks
    the session file and `build_startup_session` builds settings, registry,
    model and session, which is exactly what `run_interactive_mode` does before
    it opens a terminal. What this scenario adds is everything either side —
    that the resolution finds the provider whose key is in the environment, that
    the request really goes out over HTTP with that key, that the answer comes
    back through the SSE parser onto the screen, and that quitting leaves a
    session file behind and a terminal that was put back.
    """
    from cortex.code.interactive import build_startup_session, format_display_path

    with clean_install(reply="Ada Lovelace wrote the first algorithm.") as install:
        session_manager = resolve_session_manager(install.cwd)
        startup = build_startup_session(cwd=install.cwd, session_manager=session_manager)

        # Startup resolved a model on its own: no settings file to read a default
        # from, so this is the `ANTHROPIC_API_KEY` branch of `find_initial_model`
        # finding the one provider this machine can reach.
        assert startup.error is None, f"startup failed: {startup.error}"
        model = startup.session.agent.state.model
        assert model is not None and model.provider == "anthropic", (
            f"a clean install with an Anthropic key opened on {model!r}"
        )
        assert model.base_url == install.api.base_url, (
            f"models.json did not redirect the provider: {model.base_url}"
        )

        exits: list[int] = []
        with boot_app(
            cwd=install.cwd,
            settings=startup.settings,
            session=startup.session,
            on_exit=exits.append,
        ) as h:
            # It boots: banner, the cwd it is working in (abbreviated against
            # `$HOME`, as the banner and footer both do), an editor with focus,
            # and a footer naming the model that will answer.
            where = format_display_path(install.cwd)
            h.assert_shows("hoo│code", where, f"⬢ BUILD  {where}", model.id)
            assert _prompt_row(h) >= 0, f"no editor prompt on a fresh install\n\n{h.snapshot()}"

            # It answers. The wait is on the screen rather than on the app's busy
            # flag because the turn is real I/O: a socket the harness's loop has
            # to poll, not a callback already queued.
            h.type("who wrote the first algorithm?")
            h.key("enter")
            h.wait_for(lambda: h.contains("Ada Lovelace wrote the first algorithm."), timeout=10)
            h.assert_shows("who wrote the first algorithm?")
            assert _prompt_row(h) >= 0, f"the editor did not come back\n\n{h.snapshot()}"

            # The answer came from the provider, not from a shortcut: one POST to
            # the Messages endpoint, carrying the environment's key and the
            # model the footer names, with the question in the body.
            assert len(install.api.requests) == 1, (
                f"expected one provider request, got {len(install.api.requests)}"
            )
            request = install.api.requests[0]
            assert request.path == "/v1/messages", f"posted to {request.path}"
            assert request.headers.get("x-api-key") == "sk-ant-first-run", (
                "the environment's API key never reached the request"
            )
            assert request.body["model"] == model.id, f"asked {request.body['model']}"
            assert "who wrote the first algorithm?" in json.dumps(request.body), (
                "the prompt never reached the provider"
            )

            # And the usage the provider reported is on the footer, which is the
            # half of a turn that only a real response can produce.
            h.assert_shows("↑11", "↓7", scrollback=False)

            h.key("ctrl+c")
            h.key("ctrl+c")
            assert exits == [0], f"two Ctrl+C presses did not exit cleanly: {exits!r}"
            assert h.terminal.stopped, "the terminal was never stopped"
            assert h.terminal.cursor_hidden is False, "the cursor was left hidden"

        # The install left its state where it was told to: a session file under
        # the agent directory, holding the exchange that just happened.
        sessions = install.sessions_written()
        assert len(sessions) == 1, f"expected one session file, found {sessions!r}"
        written = open(sessions[0], encoding="utf-8").read()
        assert "who wrote the first algorithm?" in written, "the question was not persisted"
        assert "Ada Lovelace wrote the first algorithm." in written, "the answer was not persisted"


@scenario("e2e/stored-key", "A key in `auth.json` reaches the request", "7.12")
def e2e_stored_key() -> None:
    """The credential path `/login` writes to, with no environment key to mask it.

    `e2e/first-run` puts `ANTHROPIC_API_KEY` in the environment, and that is a
    key the *provider* finds for itself: `stream_simple_anthropic` falls back to
    `get_env_api_key` when nothing above it supplied one. So it passes whether or
    not the session ever resolves a credential — which is why it went on passing
    while every logged-in user got "No API key for provider: github-copilot".

    Here the only credential is an `ApiKeyCredential` in `auth.json`, which no
    provider can find on its own. The key can only reach the endpoint if the
    session wrapped the agent's stream function around
    `ModelRegistry.get_api_key_and_headers` — so the assertion on the header is
    an assertion that the wrapper is installed and used.
    """
    from cortex.code.interactive import build_startup_session

    with clean_install(
        reply="A stored credential is enough.",
        env_key=None,
        stored_keys={"anthropic": "sk-ant-from-auth-json"},
    ) as install:
        session_manager = resolve_session_manager(install.cwd)
        startup = build_startup_session(cwd=install.cwd, session_manager=session_manager)

        # The stored credential is what makes the provider reachable at all: with
        # no environment key, `find_initial_model` has only `auth.json` to go on.
        assert startup.error is None, f"startup failed: {startup.error}"
        model = startup.session.agent.state.model
        assert model is not None and model.provider == "anthropic", (
            f"a stored Anthropic key did not open an Anthropic model: {model!r}"
        )

        with boot_app(
            cwd=install.cwd,
            settings=startup.settings,
            session=startup.session,
        ) as h:
            h.type("does a stored key work?")
            h.key("enter")
            h.wait_for(lambda: h.contains("A stored credential is enough."), timeout=10)

            assert len(install.api.requests) == 1, (
                f"expected one provider request, got {len(install.api.requests)}"
            )
            request = install.api.requests[0]
            assert request.headers.get("x-api-key") == "sk-ant-from-auth-json", (
                "the stored API key never reached the request: "
                f"{request.headers.get('x-api-key')!r}"
            )


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def run_scenario(target: Scenario) -> ScenarioResult:
    """Run one scenario, converting any exception into a result rather than raising."""
    if target.run is None:
        return ScenarioResult(target.id, target.blocked_by, "pending", "not implemented yet")
    try:
        target.run()
    except Exception:
        return ScenarioResult(target.id, target.blocked_by, "failing", traceback.format_exc())
    return ScenarioResult(target.id, target.blocked_by, "passing")


def run_all() -> list[ScenarioResult]:
    return [run_scenario(s) for s in SCENARIOS]

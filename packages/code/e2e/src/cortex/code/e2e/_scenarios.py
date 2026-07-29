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

import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from cortex.code.e2e._harness import AppHarness
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


def faux_session(
    *,
    cwd: str = "/w/project",
    settings: Any = None,
    stream_fn: Any = None,
    tools: list[Any] | None = None,
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
    """
    from cortex.ai.providers.faux import register_faux_provider
    from cortex.code.session import SessionManager, create_agent_session

    registration = register_faux_provider()
    created = create_agent_session(
        cwd=cwd,
        settings_manager=settings,
        session_manager=SessionManager(cwd, "", persist=False),
        model=registration.get_model(),
        stream_fn=stream_fn,
        tools=tools,
    )
    return FauxSession(
        session=created.session,
        set_responses=registration.set_responses,
        unregister=registration.unregister,
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
        # The footer reports where you are and what model is answering.
        h.assert_shows("⬢ BUILD  /w/project", "no-model")


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
# ===========================================================================

pending("footer/model-and-tokens", "Footer shows the active model and token usage", "7.7")
pending("footer/git-branch", "Footer shows the git branch and dirty mark", "7.7")
pending("footer/context-meter", "Footer shows remaining context budget", "7.7")

# ===========================================================================
# 7.8 — slash commands
# ===========================================================================

pending("commands/slash-autocomplete", "Typing `/` opens the command autocomplete", "7.8")
pending("commands/help", "`/help` lists the built-in commands", "7.8")
pending("commands/clear", "`/clear` empties the chat log", "7.8")
pending("commands/file-mention", "`@` opens file autocomplete and inserts a path", "7.8")

# ===========================================================================
# 7.9 — overlays and selectors
# ===========================================================================

pending("overlay/model-selector", "`/model` switches model and the footer follows", "7.9")
pending("overlay/session-selector", "`/sessions` lists sessions and loads one", "7.9")
pending("overlay/settings", "`/settings` opens settings and a change persists", "7.9")
pending("overlay/escape-closes", "Escape closes an overlay and restores editor focus", "7.9")

# ===========================================================================
# 7.10 — sessions
# ===========================================================================

pending("session/resume", "`--continue` restores the previous transcript on screen", "7.10")
pending("session/persist-across-restart", "A turn survives quit and relaunch", "7.10")

# ===========================================================================
# 7.11 — auth and models
# ===========================================================================

pending("auth/login-dialog", "`/login` opens the provider picker", "7.11")
pending("auth/missing-key-message", "A missing API key explains itself instead of crashing", "7.11")

# ===========================================================================
# 7.12 — the whole product
# ===========================================================================

pending("e2e/first-run", "A clean install boots, prompts, answers and exits cleanly", "7.12")


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

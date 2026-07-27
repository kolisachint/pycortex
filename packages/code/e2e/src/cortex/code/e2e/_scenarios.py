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
from typing import Literal

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
    """
    from cortex.code.interactive import build_app_root

    def build(tui: TUI) -> None:
        build_app_root(tui, **options)

    return AppHarness(build, columns=columns, rows=rows)


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


def boot_shell(*, columns: int = 80, rows: int = 24, **options: object) -> AppHarness:
    """Boot the shell with nothing of the machine it runs on in the picture.

    Settings come from memory rather than `~/.hoocode`, and the cwd is a fixed
    string: both feed the banner and the footer, so a scenario that used the real
    ones would render a different screen on every machine — and would quietly
    start failing the day someone set `quietStartup` in their own settings file.
    """
    from cortex.code.config import SettingsManager
    from cortex.code.config.settings_storage import InMemorySettingsStorage

    options.setdefault("settings", SettingsManager.from_storage(InMemorySettingsStorage()))
    options.setdefault("cwd", "/w/project")
    return boot_app(columns=columns, rows=rows, **options)


def _prompt_row(harness: AppHarness) -> int:
    """Index of the editor's prompt line, or -1."""
    for index, line in enumerate(harness.surface().lines()):
        if line.strip() == ">":
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

pending("input/type-and-submit", "Typed text appears, Enter clears the editor", "7.3")
pending("input/multiline", "Shift+Enter opens a second line instead of submitting", "7.3")
pending("input/history-recall", "Up-arrow recalls the previous submission", "7.3")
pending("chat/user-message-renders", "A submitted message renders as a user message", "7.3")

# ===========================================================================
# 7.4 — the agent session is wired in
# ===========================================================================

pending("chat/assistant-round-trip", "A prompt round-trips against the faux provider", "7.4")
pending("chat/error-surface", "A provider error renders as an error message, not a crash", "7.4")
pending("chat/abort-turn", "Escape aborts an in-flight turn and says so", "7.4")

# ===========================================================================
# 7.5 — streaming
# ===========================================================================

pending("chat/streaming-incremental", "Assistant text appears progressively while streaming", "7.5")
pending("chat/loader-while-busy", "A loader runs during the turn and clears after", "7.5")
pending("chat/markdown-rendering", "Assistant markdown renders styled, not raw", "7.5")

# ===========================================================================
# 7.6 — tools
# ===========================================================================

pending("tools/execution-renders", "A tool call renders with name, args and result", "7.6")
pending("tools/diff-renders", "An edit renders as a coloured diff", "7.6")
pending("tools/bash-renders", "A bash call streams its output into the log", "7.6")
pending("tools/output-expand", "Ctrl+O expands and collapses truncated tool output", "7.6")

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

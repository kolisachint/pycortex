"""The slash-command handlers, driven through a hand-built command context.

That is the payoff of ``command-executor.ts`` being extracted from the app in
the first place: a handler only ever touches its context, so these run with no
terminal, no session file and no agent. The screen-level counterpart is
`commands/help` in the end-to-end corpus.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from cortex.code.interactive import CommandExecutor, DynamicBorder
from cortex.code.interactive.components.keybinding_hints import key_display_text
from cortex.code.interactive.keybindings import KeybindingsManager
from cortex.code.interactive.theme import get_markdown_theme
from cortex.code.session.stats import SessionStats, TokenStats
from cortex.tui.components import Markdown, MarkdownTheme, Spacer, Text
from cortex.tui.keys import set_keybindings
from cortex.tui.render import Container


class FakeTerminal:
    columns = 80
    rows = 24


class FakeUI:
    """Just the three members a handler reaches for on the TUI."""

    def __init__(self, lines: list[str] | None = None) -> None:
        self.terminal = FakeTerminal()
        self.renders = 0
        self._lines = lines if lines is not None else ["one", "two"]

    def request_render(self) -> None:
        self.renders += 1

    def render(self, width: int) -> list[str]:
        return list(self._lines)


class FakeSessionManager:
    def __init__(self, name: str | None = None) -> None:
        self._name = name

    def get_session_name(self) -> str | None:
        return self._name

    def append_session_info(self, name: str) -> str:
        self._name = name.strip() or None
        return "entry-1"


class FakeModel:
    """The four fields the model handlers read."""

    def __init__(self, provider: str, model_id: str, name: str | None = None) -> None:
        self.provider = provider
        self.id = model_id
        self.name = name if name is not None else model_id
        self.reasoning = False


class FakeSession:
    def __init__(
        self,
        session_manager: FakeSessionManager,
        stats: SessionStats | None = None,
        messages: list[Any] | None = None,
    ) -> None:
        self.session_manager = session_manager
        self.messages = messages if messages is not None else []
        self._stats = stats
        self.model: Any = None
        self.set_model_error: str | None = None

    def get_session_stats(self) -> SessionStats:
        assert self._stats is not None, "this context was not given stats"
        return self._stats

    def set_session_name(self, name: str) -> None:
        self.session_manager.append_session_info(name)

    async def set_model(self, model: Any) -> None:
        if self.set_model_error is not None:
            raise RuntimeError(self.set_model_error)
        self.model = model


class FakeFooter:
    def __init__(self) -> None:
        self.invalidations = 0

    def invalidate(self) -> None:
        self.invalidations += 1


class Context:
    """A :class:`CommandContext` built by hand."""

    def __init__(
        self,
        *,
        session_name: str | None = None,
        stats: SessionStats | None = None,
        messages: list[Any] | None = None,
        ui: FakeUI | None = None,
    ) -> None:
        self._session_manager = FakeSessionManager(session_name)
        self._session = FakeSession(self._session_manager, stats, messages)
        self._ui = ui if ui is not None else FakeUI()
        self._chat = Container()
        self._keybindings = KeybindingsManager()
        self.warnings: list[str] = []
        self.statuses: list[str] = []
        self.errors: list[str] = []
        self.border_updates = 0
        self.selector_searches: list[str | None] = []
        self.warned_models: list[Any] = []
        #: What `find_exact_model_match` answers with, by search term.
        self.model_matches: dict[str, Any] = {}
        self._footer = FakeFooter()

    @property
    def session(self) -> Any:
        return self._session

    @property
    def session_manager(self) -> Any:
        return self._session_manager

    @property
    def ui(self) -> Any:
        return self._ui

    @property
    def chat_container(self) -> Container:
        return self._chat

    @property
    def keybindings(self) -> Any:
        return self._keybindings

    def show_status(self, message: str) -> None:
        self.statuses.append(message)

    def show_warning(self, message: str) -> None:
        self.warnings.append(message)

    def show_error(self, message: str) -> None:
        self.errors.append(message)

    def get_markdown_theme_with_settings(self) -> MarkdownTheme:
        return get_markdown_theme()

    @property
    def footer(self) -> Any:
        return self._footer

    def update_editor_border_color(self) -> None:
        self.border_updates += 1

    async def find_exact_model_match(self, search_term: str) -> Any:
        return self.model_matches.get(search_term)

    async def maybe_warn_about_anthropic_subscription_auth(self, model: Any) -> None:
        self.warned_models.append(model)

    async def show_model_selector(self, search_term: str | None) -> None:
        self.selector_searches.append(search_term)


def _stats(**overrides: Any) -> SessionStats:
    defaults: dict[str, Any] = {
        "session_file": None,
        "session_id": "abc123",
        "user_messages": 0,
        "assistant_messages": 0,
        "tool_calls": 0,
        "tool_results": 0,
        "total_messages": 0,
        "tokens": TokenStats(),
        "cost": 0.0,
    }
    defaults.update(overrides)
    return SessionStats(**defaults)


def _chat_text(ctx: Context) -> str:
    """Everything the handler put in the chat log, rendered and de-styled."""
    lines: list[str] = []
    for child in ctx.chat_container.children:
        lines.extend(re.sub(r"\x1b\[[0-9;]*m", "", line) for line in child.render(80))
    return "\n".join(lines)


@pytest.fixture(autouse=True)
def _default_keybindings() -> None:  # pyright: ignore[reportUnusedFunction]
    # `key_display_text` reads the process-wide bindings; the app installs its
    # own at boot and nothing here does, so pin the defaults.
    set_keybindings(KeybindingsManager())


class TestHandleName:
    def test_sets_the_session_name(self):
        ctx = Context()
        CommandExecutor(ctx).handle_name("/name Ada's session")
        assert ctx.session_manager.get_session_name() == "Ada's session"
        assert "Session name set: Ada's session" in _chat_text(ctx)

    def test_reports_the_current_name_when_given_none(self):
        ctx = Context(session_name="already named")
        CommandExecutor(ctx).handle_name("/name")
        assert "Session name: already named" in _chat_text(ctx)
        assert ctx.warnings == []

    def test_warns_with_no_name_and_none_set(self):
        ctx = Context()
        CommandExecutor(ctx).handle_name("/name")
        assert ctx.warnings == ["Usage: /name <name>"]
        assert ctx.chat_container.children == []

    def test_trailing_space_alone_is_not_a_name(self):
        ctx = Context()
        CommandExecutor(ctx).handle_name("/name   ")
        assert ctx.warnings == ["Usage: /name <name>"]

    def test_repaints(self):
        ctx = Context()
        CommandExecutor(ctx).handle_name("/name x")
        assert ctx.ui.renders == 1

    def test_a_blank_stored_name_reads_as_unset(self):
        # `getSessionName` returns undefined for a whitespace-only entry, so the
        # empty-argument branch has to fall through to the warning.
        ctx = Context(session_name=None)
        ctx.session_manager.append_session_info("   ")
        CommandExecutor(ctx).handle_name("/name")
        assert ctx.warnings == ["Usage: /name <name>"]


class TestHandleSession:
    def test_reports_counts_and_tokens(self):
        ctx = Context(
            stats=_stats(
                session_id="sess-9",
                user_messages=3,
                assistant_messages=2,
                tool_calls=4,
                tool_results=4,
                total_messages=9,
                tokens=TokenStats(input=1234, output=56, total=1290),
            )
        )
        CommandExecutor(ctx).handle_session()
        text = _chat_text(ctx)
        assert "Session Info" in text
        assert "ID: sess-9" in text
        assert "User: 3" in text
        assert "Tool Calls: 4" in text
        # Thousands separators, as `toLocaleString` gives.
        assert "Input: 1,234" in text

    def test_an_unpersisted_session_says_in_memory(self):
        ctx = Context(stats=_stats(session_file=None))
        CommandExecutor(ctx).handle_session()
        assert "File: In-memory" in _chat_text(ctx)

    def test_a_persisted_session_names_its_file(self):
        ctx = Context(stats=_stats(session_file="/s/one.jsonl"))
        CommandExecutor(ctx).handle_session()
        assert "File: /s/one.jsonl" in _chat_text(ctx)

    def test_the_name_line_is_absent_when_unnamed(self):
        ctx = Context(stats=_stats())
        CommandExecutor(ctx).handle_session()
        assert "Name:" not in _chat_text(ctx)

    def test_the_name_line_is_present_when_named(self):
        ctx = Context(session_name="ada", stats=_stats())
        CommandExecutor(ctx).handle_session()
        assert "Name: ada" in _chat_text(ctx)

    def test_cache_lines_only_appear_when_non_zero(self):
        # Each line needs its own zero case: a scenario where only *one* of the
        # two is spent cannot tell `> 0` from `>= 0` on the other.
        neither = Context(stats=_stats(tokens=TokenStats()))
        CommandExecutor(neither).handle_session()
        assert "Cache Read" not in _chat_text(neither)
        assert "Cache Write" not in _chat_text(neither)

        read_only = Context(stats=_stats(tokens=TokenStats(cache_read=10)))
        CommandExecutor(read_only).handle_session()
        assert "Cache Read: 10" in _chat_text(read_only)
        assert "Cache Write" not in _chat_text(read_only)

        write_only = Context(stats=_stats(tokens=TokenStats(cache_write=7)))
        CommandExecutor(write_only).handle_session()
        assert "Cache Write: 7" in _chat_text(write_only)
        assert "Cache Read" not in _chat_text(write_only)

    def test_cost_only_appears_when_spent(self):
        ctx = Context(stats=_stats())
        CommandExecutor(ctx).handle_session()
        assert "Cost" not in _chat_text(ctx)

        spent = Context(stats=_stats(cost=1.23456))
        CommandExecutor(spent).handle_session()
        # Four decimal places, as `toFixed(4)` gives.
        assert "Total: 1.2346" in _chat_text(spent)


class TestHandleChangelog:
    def test_says_so_when_there_are_no_entries(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(
            "cortex.code.interactive.command_executor.get_changelog_path",
            lambda: str(tmp_path / "absent.md"),
        )
        ctx = Context()
        CommandExecutor(ctx).handle_changelog()
        assert "No changelog entries found." in _chat_text(ctx)
        assert not any(isinstance(c, DynamicBorder) for c in ctx.chat_container.children)

    def test_frames_the_entries_newest_first(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        path = tmp_path / "CHANGELOG.md"
        path.write_text("## 1.0.0\n\n- older\n\n## 2.0.0\n\n- newer\n")
        monkeypatch.setattr(
            "cortex.code.interactive.command_executor.get_changelog_path", lambda: str(path)
        )
        ctx = Context()
        CommandExecutor(ctx).handle_changelog()

        children = ctx.chat_container.children
        assert isinstance(children[0], Spacer)
        assert isinstance(children[1], DynamicBorder)
        assert isinstance(children[-1], DynamicBorder)
        assert any(isinstance(c, Markdown) for c in children)

        text = _chat_text(ctx)
        assert "What's New" in text
        assert text.index("newer") < text.index("older"), "entries are not newest-first"


class TestHandleHotkeys:
    def test_lists_every_section(self):
        ctx = Context()
        CommandExecutor(ctx).handle_hotkeys()
        text = _chat_text(ctx)
        assert "Keyboard Shortcuts" in text
        for heading in ("Navigation", "Editing", "Other"):
            assert heading in text, f"missing section: {heading}"

    def test_reads_the_keys_off_the_bindings(self):
        ctx = Context()
        CommandExecutor(ctx).handle_hotkeys()
        assert key_display_text("tui.input.submit") in _chat_text(ctx)

    def test_a_rebound_key_changes_the_card(self):
        # Both rows have to be *rebound*, not merely present: every key on this
        # card renders as its own default, so "the default is on screen" cannot
        # tell a generated row from a hard-coded one. Submit is the row most
        # likely to be written out by hand, which is why it is one of the two.
        bindings = KeybindingsManager(
            {"tui.editor.undo": ["ctrl+q"], "tui.input.submit": ["ctrl+j"]}
        )
        set_keybindings(bindings)
        ctx = Context()
        ctx._keybindings = bindings  # pyright: ignore[reportPrivateUsage]
        CommandExecutor(ctx).handle_hotkeys()
        text = _chat_text(ctx)
        assert "Ctrl+Q" in text, "the card still shows the default undo key"
        assert "Ctrl+J" in text, "the card still shows the default submit key"
        # Submit's default is bare `Enter`; `Shift+Enter` is still the new-line
        # binding, so the row has to be checked rather than the whole card.
        submit_row = next(row for row in text.splitlines() if "Send message" in row)
        assert "Ctrl+J" in submit_row, f"the submit row is hard-coded: {submit_row!r}"

    def test_frames_the_card_between_two_rules(self):
        ctx = Context()
        CommandExecutor(ctx).handle_hotkeys()
        children = ctx.chat_container.children
        assert isinstance(children[1], DynamicBorder)
        assert isinstance(children[-1], DynamicBorder)

    def test_documents_the_prefixes_that_are_not_keys(self):
        ctx = Context()
        CommandExecutor(ctx).handle_hotkeys()
        text = _chat_text(ctx)
        assert "Slash commands" in text
        assert "Run bash command" in text


class TestHandleDebug:
    def test_writes_the_frame_with_measured_widths(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        log = tmp_path / "logs" / "debug.log"
        monkeypatch.setattr(
            "cortex.code.interactive.command_executor.get_debug_log_path", lambda: str(log)
        )
        ctx = Context(ui=FakeUI(["\x1b[31mred\x1b[0m", "plain"]))
        CommandExecutor(ctx).handle_debug()

        assert os.path.exists(log), "the debug log was not written"
        dumped = log.read_text()
        assert "Terminal: 80x24" in dumped
        assert "Total lines: 2" in dumped
        # The width is the *visible* one, so the colour codes do not count.
        assert "[0] (w=3)" in dumped
        assert "[1] (w=5) " + json.dumps("plain") in dumped

    def test_creates_the_directory(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        log = tmp_path / "a" / "b" / "debug.log"
        monkeypatch.setattr(
            "cortex.code.interactive.command_executor.get_debug_log_path", lambda: str(log)
        )
        CommandExecutor(Context()).handle_debug()
        assert log.is_file()

    def test_dumps_the_messages_as_jsonl(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        log = tmp_path / "debug.log"
        monkeypatch.setattr(
            "cortex.code.interactive.command_executor.get_debug_log_path", lambda: str(log)
        )
        ctx = Context(messages=[{"role": "user", "content": "hi"}])
        CommandExecutor(ctx).handle_debug()
        dumped = log.read_text()
        assert '{"role": "user", "content": "hi"}' in dumped

    def test_names_the_log_on_screen(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        log = tmp_path / "debug.log"
        monkeypatch.setattr(
            "cortex.code.interactive.command_executor.get_debug_log_path", lambda: str(log)
        )
        ctx = Context()
        CommandExecutor(ctx).handle_debug()
        text = _chat_text(ctx)
        assert "✓ Debug log written" in text
        assert str(log) in text


class TestDynamicBorder:
    def test_fills_the_width(self):
        assert DynamicBorder(lambda text: text).render(10) == ["─" * 10]

    def test_never_renders_an_empty_line(self):
        # `Math.max(1, width)`: a zero-width render still draws one cell.
        assert DynamicBorder(lambda text: text).render(0) == ["─"]

    def test_re_measures_on_every_render(self):
        border = DynamicBorder(lambda text: text)
        assert border.render(4) == ["────"]
        assert border.render(7) == ["───────"]

    def test_uses_the_theme_border_colour_by_default(self):
        rendered = DynamicBorder().render(4)[0]
        assert "─" * 4 in rendered
        assert rendered != "─" * 4, "the default colour was not applied"

    def test_invalidate_is_safe(self):
        border = DynamicBorder(lambda text: text)
        border.invalidate()
        assert border.render(3) == ["───"]


class TestChatComponents:
    """The handlers write `Text` blocks, not raw strings, into the container."""

    def test_name_writes_a_text_block(self):
        ctx = Context()
        CommandExecutor(ctx).handle_name("/name x")
        assert [type(c) for c in ctx.chat_container.children] == [Spacer, Text]


class TestHandleModel:
    """`/model` with an argument switches outright; without one it opens the picker."""

    async def test_a_bare_command_opens_the_picker(self):
        ctx = Context()
        await CommandExecutor(ctx).handle_model(None)
        assert ctx.selector_searches == [None]

    async def test_an_exact_match_switches_without_opening_anything(self):
        ctx = Context()
        model = FakeModel("openai", "gpt-5-mini")
        ctx.model_matches["gpt-5-mini"] = model
        await CommandExecutor(ctx).handle_model("gpt-5-mini")

        assert ctx.session.model is model
        assert ctx.selector_searches == [], "an exact match should not open the picker"
        assert ctx.statuses == ["Model: gpt-5-mini"]
        assert ctx.footer.invalidations == 1
        assert ctx.border_updates == 1
        assert ctx.warned_models == [model]

    async def test_a_term_that_matches_nothing_opens_the_picker_pre_filtered(self):
        """Better than "no such model": the half-remembered name becomes the
        search, so the list opens on whatever it does match."""
        ctx = Context()
        await CommandExecutor(ctx).handle_model("sonn")
        assert ctx.selector_searches == ["sonn"]

    async def test_a_refused_switch_is_reported(self):
        ctx = Context()
        model = FakeModel("openai", "gpt-5-mini")
        ctx.model_matches["gpt-5-mini"] = model
        ctx.session.set_model_error = "No API key for openai/gpt-5-mini"

        await CommandExecutor(ctx).handle_model("gpt-5-mini")

        assert ctx.errors == ["No API key for openai/gpt-5-mini"]
        assert ctx.statuses == []
        assert ctx.selector_searches == []

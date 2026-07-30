"""The three overlays that load a different point in the session (step 7.10).

Driven directly, like `test_selectors.py`: construct, send keystrokes, read what
came back through the callbacks. What the *app* does with them — open one over
the editor, load what was picked, put the focus back — is the end-to-end
corpus's job, in `overlay/session-selector`.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from cortex.code.interactive.components.session_selector import (
    SessionSelectorComponent,
    build_session_tree,
    canonicalize_path,
    flatten_session_tree,
    format_session_date,
)
from cortex.code.interactive.components.session_selector_search import (
    filter_and_sort_sessions,
    has_session_name,
    match_session,
    parse_search_query,
)
from cortex.code.interactive.components.tree_selector import TreeSelectorComponent
from cortex.code.interactive.components.user_message_selector import (
    UserMessageItem,
    UserMessageSelectorComponent,
)
from cortex.code.interactive.keybindings import KeybindingsManager
from cortex.code.session import SessionInfo, SessionTreeNode
from cortex.tui.keys import set_keybindings

DOWN = "\x1b[B"
UP = "\x1b[A"
ENTER = "\r"
ESCAPE = "\x1b"
TAB = "\t"
BACKSPACE = "\x7f"
#: `app.tree.editLabel` is bound to shift+L, which only arrives as a distinct key
#: under the Kitty protocol — a legacy terminal sends a bare "L", and the TS does
#: not match that either.
SHIFT_L = "\x1b[108;2u"


@pytest.fixture(autouse=True)
def _app_keybindings() -> None:  # pyright: ignore[reportUnusedFunction]
    """The selectors resolve keys through the process-wide manager, as the app does."""
    set_keybindings(KeybindingsManager())


def _plain(lines: list[str]) -> list[str]:
    return [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in lines]


def _rendered(component: Any, width: int = 80) -> str:
    return "\n".join(_plain(component.render(width)))


def _session(
    path: str,
    first_message: str = "a question",
    *,
    name: str | None = None,
    parent: str | None = None,
    minutes_ago: int = 0,
    message_count: int = 2,
    all_messages_text: str | None = None,
) -> SessionInfo:
    modified = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return SessionInfo(
        path=path,
        id=path.rsplit("/", 1)[-1],
        cwd="/w/project",
        name=name,
        parent_session_path=parent,
        created=modified,
        modified=modified,
        message_count=message_count,
        first_message=first_message,
        all_messages_text=(all_messages_text if all_messages_text is not None else first_message),
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestParseSearchQuery:
    def test_bare_words_are_fuzzy_tokens(self):
        parsed = parse_search_query("foo bar")
        assert [(t.kind, t.value) for t in parsed.tokens] == [
            ("fuzzy", "foo"),
            ("fuzzy", "bar"),
        ]

    def test_quotes_make_one_exact_phrase(self):
        parsed = parse_search_query('foo "node cve" bar')
        assert [(t.kind, t.value) for t in parsed.tokens] == [
            ("fuzzy", "foo"),
            ("phrase", "node cve"),
            ("fuzzy", "bar"),
        ]

    def test_an_unbalanced_quote_falls_back_to_plain_words(self):
        # Otherwise the query would stop working the moment the opening quote is
        # typed and stay broken until the closing one arrives.
        parsed = parse_search_query('foo "node cve')
        assert [(t.kind, t.value) for t in parsed.tokens] == [
            ("fuzzy", "foo"),
            ("fuzzy", '"node'),
            ("fuzzy", "cve"),
        ]

    def test_re_prefix_compiles_a_regex(self):
        parsed = parse_search_query("re:^ada")
        assert parsed.mode == "regex"
        assert parsed.regex is not None and parsed.regex.search("Ada Lovelace") is not None

    def test_a_broken_regex_is_an_error_rather_than_a_crash(self):
        parsed = parse_search_query("re:[unclosed")
        assert parsed.mode == "regex"
        assert parsed.error
        assert parsed.regex is None

    def test_an_empty_regex_is_an_error(self):
        assert parse_search_query("re:").error == "Empty regex"

    def test_an_empty_query_matches_everything(self):
        parsed = parse_search_query("   ")
        assert parsed.tokens == []
        assert match_session(_session("/s/1"), parsed).matches


class TestMatchSession:
    def test_a_phrase_must_appear_in_order(self):
        session = _session("/s/1", all_messages_text="the node cve advisory")
        assert match_session(session, parse_search_query('"node cve"')).matches
        assert not match_session(session, parse_search_query('"cve node"')).matches

    def test_a_phrase_is_contiguous_where_a_fuzzy_token_is_not(self):
        # The whole reason quotes exist: every character of "node cve" appears in
        # "node and cve" in order, so a fuzzy token matches it and a phrase must
        # not. Without this the two paths are indistinguishable.
        session = _session("/s/1", all_messages_text="node and cve")
        assert match_session(session, parse_search_query("node cve")).matches
        assert not match_session(session, parse_search_query('"node cve"')).matches

    def test_a_phrase_ignores_how_the_whitespace_was_written(self):
        session = _session("/s/1", all_messages_text="the node\n  cve advisory")
        assert match_session(session, parse_search_query('"node cve"')).matches

    def test_every_token_has_to_match(self):
        session = _session("/s/1", all_messages_text="alpha beta")
        assert match_session(session, parse_search_query("alpha beta")).matches
        assert not match_session(session, parse_search_query("alpha zeta")).matches

    def test_the_name_and_the_cwd_are_searchable_too(self):
        session = _session("/s/1", name="deploy notes", all_messages_text="nothing relevant")
        assert match_session(session, parse_search_query("deploy")).matches
        assert match_session(session, parse_search_query("project")).matches

    def test_an_earlier_match_scores_better(self):
        early = _session("/s/1", all_messages_text="cve at the front")
        late = _session("/s/2", all_messages_text="a long preamble and then cve")
        assert (
            match_session(early, parse_search_query('"cve"')).score
            < match_session(late, parse_search_query('"cve"')).score
        )


class TestFilterAndSortSessions:
    def test_recent_mode_filters_without_reordering(self):
        # The list arrives most-recent-first and typing must not shuffle it.
        sessions = [
            _session("/s/1", "zebra", minutes_ago=1),
            _session("/s/2", "zebra crossing", minutes_ago=5),
        ]
        assert [s.path for s in filter_and_sort_sessions(sessions, "zebra", "recent")] == [
            "/s/1",
            "/s/2",
        ]

    def test_relevance_mode_puts_the_best_match_first(self):
        sessions = [
            _session("/s/late", all_messages_text="a long preamble and then cve"),
            _session("/s/early", all_messages_text="cve at the front"),
        ]
        ranked = filter_and_sort_sessions(sessions, '"cve"', "relevance")
        assert [s.path for s in ranked] == ["/s/early", "/s/late"]

    def test_the_named_filter_drops_the_unnamed(self):
        sessions = [_session("/s/1", name="kept"), _session("/s/2")]
        assert [s.path for s in filter_and_sort_sessions(sessions, "", "recent", "named")] == [
            "/s/1"
        ]

    def test_a_broken_query_matches_nothing_rather_than_everything(self):
        # A typo'd regex silently listing every session is the worse answer.
        sessions = [_session("/s/1"), _session("/s/2")]
        assert filter_and_sort_sessions(sessions, "re:[unclosed", "relevance") == []

    def test_an_empty_query_keeps_the_incoming_order(self):
        sessions = [_session("/s/1"), _session("/s/2")]
        assert filter_and_sort_sessions(sessions, "  ", "relevance") == sessions

    def test_has_session_name_ignores_whitespace(self):
        assert not has_session_name(_session("/s/1", name="   "))
        assert has_session_name(_session("/s/1", name=" named "))


# ---------------------------------------------------------------------------
# Session tree (the threaded view)
# ---------------------------------------------------------------------------


class TestSessionTree:
    def test_a_fork_hangs_under_the_session_it_came_from(self):
        parent = _session("/s/parent", "the original", minutes_ago=10)
        child = _session("/s/child", "the fork", parent="/s/parent", minutes_ago=1)

        flat = flatten_session_tree(build_session_tree([child, parent]))

        assert [(n.session.path, n.depth) for n in flat] == [("/s/parent", 0), ("/s/child", 1)]

    def test_a_fork_of_a_session_that_is_not_listed_is_a_root(self):
        # Scoped to one folder, the parent may simply not be in the list.
        orphan = _session("/s/child", parent="/elsewhere/parent")
        flat = flatten_session_tree(build_session_tree([orphan]))
        assert [(n.session.path, n.depth) for n in flat] == [("/s/child", 0)]

    def test_roots_are_ordered_most_recently_active_first(self):
        older = _session("/s/older", minutes_ago=60)
        newer = _session("/s/newer", minutes_ago=1)
        flat = flatten_session_tree(build_session_tree([older, newer]))
        assert [n.session.path for n in flat] == ["/s/newer", "/s/older"]

    def test_canonicalize_path_keeps_a_path_that_does_not_exist(self):
        assert canonicalize_path("/no/such/file") == "/no/such/file"
        assert canonicalize_path(None) is None


class TestFormatSessionDate:
    @pytest.mark.parametrize(
        ("minutes", "expected"),
        [
            (0, "now"),
            (5, "5m"),
            (60 * 3, "3h"),
            (60 * 24 * 2, "2d"),
            (60 * 24 * 9, "1w"),
            (60 * 24 * 40, "1mo"),
            (60 * 24 * 400, "1y"),
        ],
    )
    def test_shortens_to_the_largest_unit_that_fits(self, minutes: int, expected: str):
        when = datetime.now(UTC) - timedelta(minutes=minutes)
        assert format_session_date(when) == expected

    def test_a_session_with_no_date_shows_nothing(self):
        assert format_session_date(None) == ""


# ---------------------------------------------------------------------------
# The session selector
# ---------------------------------------------------------------------------


class SelectorHarness:
    """A session selector plus what its callbacks recorded."""

    def __init__(
        self,
        current: list[SessionInfo],
        every: list[SessionInfo] | None = None,
        *,
        current_session_file_path: str | None = None,
        rename: Any = None,
        current_error: Exception | None = None,
    ) -> None:
        self.selected: list[str] = []
        self.cancels = 0
        self.exits = 0
        self.renders = 0
        self.current_progress: list[tuple[int, int]] = []
        self._current = current
        self._every = every if every is not None else []
        self._current_error = current_error
        self.all_loads = 0

        async def load_current(on_progress: Any = None) -> list[SessionInfo]:
            if self._current_error is not None:
                raise self._current_error
            for index, _ in enumerate(self._current, start=1):
                if on_progress is not None:
                    on_progress(index, len(self._current))
                    self.current_progress.append((index, len(self._current)))
            return list(self._current)

        async def load_all(on_progress: Any = None) -> list[SessionInfo]:
            self.all_loads += 1
            return list(self._every)

        self.component = SessionSelectorComponent(
            load_current,
            load_all,
            self.selected.append,
            self._cancel,
            self._exit,
            self._render,
            rename_session=rename,
            keybindings=KeybindingsManager(),
            current_session_file_path=current_session_file_path,
            schedule=_run_now,
        )

    def _cancel(self) -> None:
        self.cancels += 1

    def _exit(self) -> None:
        self.exits += 1

    def _render(self) -> None:
        self.renders += 1

    def send(self, *keys: str) -> None:
        for key in keys:
            self.component.handle_input(key)

    def text(self, width: int = 80) -> str:
        return "\n".join(_plain(self.component.render(width)))


def _run_now(coro: Any) -> None:
    """Run a scheduled coroutine to completion, with no loop in the picture."""
    asyncio.run(coro)


class TestSessionSelector:
    def test_lists_the_current_folder_first(self):
        # Wide, because the header truncates its title to fit the scope and sort
        # readouts on its right — which is what it is supposed to do.
        harness = SelectorHarness([_session("/s/1", "how do I list files?")])
        text = harness.text(120)
        assert "Resume Session (Current Folder)" in text
        assert "◉ Current Folder" in text
        assert "how do I list files?" in text

    def test_enter_hands_back_the_highlighted_path(self):
        harness = SelectorHarness(
            [_session("/s/1", "first", minutes_ago=1), _session("/s/2", "second", minutes_ago=9)]
        )
        harness.send(DOWN, ENTER)
        assert harness.selected == ["/s/2"]

    def test_escape_cancels(self):
        harness = SelectorHarness([_session("/s/1")])
        harness.send(ESCAPE)
        assert harness.cancels == 1
        assert harness.selected == []

    def test_typing_filters_the_list(self):
        harness = SelectorHarness([_session("/s/1", "list files"), _session("/s/2", "count lines")])
        harness.send(*"count")
        text = harness.text()
        assert "count lines" in text
        assert "list files" not in text

    def test_backspace_widens_the_filter_again(self):
        harness = SelectorHarness([_session("/s/1", "list files"), _session("/s/2", "count lines")])
        harness.send(*"count", BACKSPACE, BACKSPACE, BACKSPACE, BACKSPACE, BACKSPACE)
        assert "list files" in harness.text()

    def test_tab_switches_to_every_project_and_loads_it_once(self):
        harness = SelectorHarness([_session("/s/1", "here")], [_session("/s/2", "elsewhere")])
        harness.send(TAB)
        assert "Resume Session (All)" in harness.text()
        assert "elsewhere" in harness.text()

        # Back and forth again: walking every session directory on the machine
        # is the one thing this overlay can be slow at, so it is done once.
        harness.send(TAB, TAB)
        assert harness.all_loads == 1

    def test_the_scope_toggle_shows_the_owning_folder(self):
        harness = SelectorHarness([], [_session("/s/2", "elsewhere")])
        harness.send(TAB)
        assert "/w/project" in harness.text()

    def test_an_empty_current_folder_says_how_to_see_the_others(self):
        harness = SelectorHarness([])
        assert "No sessions in current folder" in harness.text()
        # And it stays open, because Tab is the answer it just gave.
        assert harness.cancels == 0

    def test_nothing_anywhere_closes_the_overlay(self):
        # A picker with nothing to pick, in either scope.
        harness = SelectorHarness([], [])
        harness.send(TAB)
        assert harness.cancels == 1

    def test_a_failed_load_says_so_and_leaves_the_overlay_open(self):
        harness = SelectorHarness([], current_error=RuntimeError("permission denied"))
        assert "Failed to load sessions: permission denied" in harness.text()
        assert harness.cancels == 0

    def test_progress_is_reported_while_loading(self):
        harness = SelectorHarness([_session("/s/1"), _session("/s/2")])
        assert harness.current_progress == [(1, 2), (2, 2)]

    def test_the_named_filter_toggles(self):
        harness = SelectorHarness(
            [_session("/s/1", "unnamed one"), _session("/s/2", "other", name="deploy notes")]
        )
        harness.send("\x0e")  # Ctrl+N, `app.session.toggleNamedFilter`
        text = harness.text()
        assert "deploy notes" in text
        assert "unnamed one" not in text
        assert "Name: Named" in text

    def test_the_sort_mode_cycles(self):
        harness = SelectorHarness([_session("/s/1")])
        assert "Sort: Threaded" in harness.text()
        harness.send("\x13")  # Ctrl+S, `app.session.toggleSort`
        assert "Sort: Recent" in harness.text()
        harness.send("\x13")
        assert "Sort: Fuzzy" in harness.text()
        harness.send("\x13")
        assert "Sort: Threaded" in harness.text()

    def test_the_path_toggle_shows_where_each_session_lives(self):
        harness = SelectorHarness([_session("/s/somewhere/a.jsonl")])
        assert "/s/somewhere/a.jsonl" not in harness.text(120)
        harness.send("\x10")  # Ctrl+P, `app.session.togglePath`
        assert "/s/somewhere/a.jsonl" in harness.text(120)

    def test_the_current_session_cannot_be_deleted(self):
        harness = SelectorHarness([_session("/s/1")], current_session_file_path="/s/1")
        harness.send("\x04")  # Ctrl+D, `app.session.delete`
        assert "Cannot delete the currently active session" in harness.text()

    def test_delete_asks_first(self, tmp_path: Any):
        victim = tmp_path / "victim.jsonl"
        victim.write_text("{}\n", encoding="utf-8")
        harness = SelectorHarness([_session(str(victim))])

        harness.send("\x04")
        assert "Delete session?" in harness.text()
        # Escape at the prompt leaves the file alone.
        harness.send(ESCAPE)
        assert victim.exists()
        # And it was the *prompt* that Escape closed, not the overlay.
        assert harness.cancels == 0

    def test_confirming_a_delete_removes_the_file(self, tmp_path: Any):
        victim = tmp_path / "victim.jsonl"
        victim.write_text("{}\n", encoding="utf-8")
        harness = SelectorHarness([_session(str(victim))])

        harness.send("\x04", ENTER)
        assert not victim.exists()
        assert harness.selected == [], "confirming a delete must not also resume the session"

    def test_rename_opens_an_input_and_saves(self):
        renames: list[tuple[str, str | None]] = []

        def rename(path: str, name: str | None) -> None:
            renames.append((path, name))

        harness = SelectorHarness([_session("/s/1")], rename=rename)
        harness.send("\x12")  # Ctrl+R, `app.session.rename`
        assert "Rename Session" in harness.text()

        harness.send(*"nightly", ENTER)
        assert renames == [("/s/1", "nightly")]
        # And the list is back.
        assert "Resume Session" in harness.text()

    def test_rename_can_be_escaped(self):
        def rename(path: str, name: str | None) -> None:
            return None

        harness = SelectorHarness([_session("/s/1")], rename=rename)
        harness.send("\x12", ESCAPE)
        assert "Rename Session" not in harness.text()
        assert harness.cancels == 0

    def test_focus_reaches_the_search_box(self):
        # An overlay that is on screen but not holding the keyboard looks
        # exactly like one that is, until the next keystroke disappears.
        harness = SelectorHarness([_session("/s/1")])
        harness.component.focused = True
        assert harness.component.session_list.focused


# ---------------------------------------------------------------------------
# The fork picker
# ---------------------------------------------------------------------------


def _fork_selector(
    messages: list[UserMessageItem], initial: str | None = None
) -> tuple[UserMessageSelectorComponent, list[str], list[int]]:
    selected: list[str] = []
    cancels: list[int] = []
    component = UserMessageSelectorComponent(
        messages, selected.append, lambda: cancels.append(1), initial
    )
    return component, selected, cancels


class TestUserMessageSelector:
    def test_starts_on_the_most_recent_message(self):
        # The one you just sent is the one you are most likely re-asking.
        component, selected, _ = _fork_selector(
            [UserMessageItem("a", "first"), UserMessageItem("b", "second")]
        )
        component.handle_input(ENTER)
        assert selected == ["b"]

    def test_an_explicit_initial_selection_wins(self):
        component, selected, _ = _fork_selector(
            [UserMessageItem("a", "first"), UserMessageItem("b", "second")], initial="a"
        )
        component.handle_input(ENTER)
        assert selected == ["a"]

    def test_up_wraps_to_the_bottom(self):
        component, selected, _ = _fork_selector(
            [UserMessageItem("a", "first"), UserMessageItem("b", "second")], initial="a"
        )
        component.handle_input(UP)
        component.handle_input(ENTER)
        assert selected == ["b"]

    def test_down_wraps_to_the_top(self):
        component, selected, _ = _fork_selector(
            [UserMessageItem("a", "first"), UserMessageItem("b", "second")]
        )
        component.handle_input(DOWN)
        component.handle_input(ENTER)
        assert selected == ["a"]

    def test_escape_cancels(self):
        component, selected, cancels = _fork_selector([UserMessageItem("a", "first")])
        component.handle_input(ESCAPE)
        assert cancels == [1]
        assert selected == []

    def test_each_row_says_where_it_sits(self):
        component, _, _ = _fork_selector(
            [UserMessageItem("a", "first"), UserMessageItem("b", "second")]
        )
        text = _rendered(component)
        assert "Message 1 of 2" in text
        assert "Message 2 of 2" in text

    def test_a_multiline_message_is_flattened_onto_its_row(self):
        component, _, _ = _fork_selector([UserMessageItem("a", "first\nsecond")])
        assert "first second" in _rendered(component)

    def test_an_empty_list_closes_itself(self):
        _, _, cancels = _fork_selector([])
        assert cancels == [1]


# ---------------------------------------------------------------------------
# The session tree
# ---------------------------------------------------------------------------


def _entry(entry_id: str, entry_type: str = "message", **fields: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": entry_type,
        "id": entry_id,
        "parentId": None,
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    entry.update(fields)
    return entry


def _user_entry(entry_id: str, text: str, parent: str | None = None) -> dict[str, Any]:
    return _entry(
        entry_id, parentId=parent, message={"role": "user", "content": text, "timestamp": 1}
    )


def _assistant_entry(entry_id: str, text: str, parent: str | None = None) -> dict[str, Any]:
    return _entry(
        entry_id,
        parentId=parent,
        message={
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "stop",
        },
    )


def _node(entry: dict[str, Any], children: list[Any] | None = None, label: str | None = None):
    # `SessionTreeNode.entry` is typed as the dataclass the manager builds; what
    # it really holds — and what the selector reads — is the JSONL dict.
    return SessionTreeNode(
        entry=cast(Any, entry), children=children if children is not None else [], label=label
    )


class TreeHarness:
    """A tree selector plus what its callbacks recorded."""

    def __init__(
        self,
        tree: list[Any],
        current_leaf_id: str | None = None,
        initial_selected_id: str | None = None,
        initial_filter_mode: Any = None,
    ) -> None:
        self.selected: list[str] = []
        self.cancels = 0
        self.labels: list[tuple[str, str | None]] = []
        self.component = TreeSelectorComponent(
            tree,
            current_leaf_id,
            24,
            self.selected.append,
            self._cancel,
            lambda entry_id, label: self.labels.append((entry_id, label)),
            initial_selected_id,
            initial_filter_mode,
        )

    def _cancel(self) -> None:
        self.cancels += 1

    def send(self, *keys: str) -> None:
        for key in keys:
            self.component.handle_input(key)

    def text(self, width: int = 80) -> str:
        return "\n".join(_plain(self.component.render(width)))


class TestTreeSelector:
    def test_lists_the_conversation(self):
        tree = [
            _node(
                _user_entry("u1", "how do I list files?"),
                [_node(_assistant_entry("a1", "Use ls.", "u1"))],
            )
        ]
        text = TreeHarness(tree, "a1").text()
        assert "user: how do I list files?" in text
        assert "assistant: Use ls." in text

    def test_marks_the_branch_the_session_is_on(self):
        tree = [
            _node(
                _user_entry("u1", "asked"),
                [
                    _node(_assistant_entry("a1", "one answer", "u1")),
                    _node(_assistant_entry("a2", "another answer", "u1")),
                ],
            )
        ]
        lines = TreeHarness(tree, "a1").text().splitlines()
        on_path = [line for line in lines if "•" in line]
        assert any("one answer" in line for line in on_path)
        assert not any("another answer" in line for line in on_path)

    def test_enter_hands_back_the_selected_entry(self):
        tree = [
            _node(
                _user_entry("u1", "asked"),
                [_node(_assistant_entry("a1", "answered", "u1"))],
            )
        ]
        harness = TreeHarness(tree, "a1")
        harness.send(UP, ENTER)
        assert harness.selected == ["u1"]

    def test_it_opens_on_the_current_leaf(self):
        tree = [
            _node(
                _user_entry("u1", "asked"),
                [_node(_assistant_entry("a1", "answered", "u1"))],
            )
        ]
        harness = TreeHarness(tree, "a1")
        harness.send(ENTER)
        assert harness.selected == ["a1"]

    def test_escape_cancels(self):
        harness = TreeHarness([_node(_user_entry("u1", "asked"))], "u1")
        harness.send(ESCAPE)
        assert harness.cancels == 1

    def test_typing_searches_and_escape_clears_the_search_first(self):
        tree = [
            _node(
                _user_entry("u1", "about lists"),
                [_node(_user_entry("u2", "about trees", "u1"))],
            )
        ]
        harness = TreeHarness(tree, "u2")
        harness.send(*"trees")
        assert "about lists" not in harness.text()

        harness.send(ESCAPE)
        assert "about lists" in harness.text()
        # The first Escape belonged to the search, not to the overlay.
        assert harness.cancels == 0
        harness.send(ESCAPE)
        assert harness.cancels == 1

    def test_backspace_shortens_the_search(self):
        tree = [
            _node(
                _user_entry("u1", "about lists"),
                [_node(_user_entry("u2", "about trees", "u1"))],
            )
        ]
        harness = TreeHarness(tree, "u2")
        harness.send(*"trees", BACKSPACE, BACKSPACE, BACKSPACE, BACKSPACE, BACKSPACE)
        assert "about lists" in harness.text()

    def test_the_default_view_hides_bookkeeping_entries(self):
        tree = [
            _node(
                _user_entry("u1", "asked"),
                [_node(_entry("m1", "model_change", parentId="u1", modelId="faux-2"))],
            )
        ]
        harness = TreeHarness(tree, "u1")
        assert "model: faux-2" not in harness.text()

        # `Ctrl+A`-style "show everything" brings them back.
        harness.send("\x01")  # `app.tree.filter.all`
        assert "model: faux-2" in harness.text()
        assert "[all]" in harness.text()

    def test_the_user_only_filter_keeps_just_the_questions(self):
        tree = [
            _node(
                _user_entry("u1", "asked"),
                [_node(_assistant_entry("a1", "answered", "u1"))],
            )
        ]
        harness = TreeHarness(tree, "a1")
        harness.send("\x15")  # `app.tree.filter.userOnly`
        text = harness.text()
        assert "user: asked" in text
        assert "assistant: answered" not in text
        assert "[user]" in text

    def test_a_filter_key_pressed_twice_goes_back_to_the_default(self):
        tree = [
            _node(
                _user_entry("u1", "asked"),
                [_node(_assistant_entry("a1", "answered", "u1"))],
            )
        ]
        harness = TreeHarness(tree, "a1")
        harness.send("\x15", "\x15")
        assert "assistant: answered" in harness.text()

    def test_an_assistant_turn_that_only_called_tools_has_no_row(self):
        # The tool result below it is what says what happened.
        tree = [
            _node(
                _user_entry("u1", "read the file"),
                [
                    _node(
                        _entry(
                            "a1",
                            parentId="u1",
                            message={
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "toolCall",
                                        "id": "t1",
                                        "name": "read",
                                        "arguments": {"path": "/w/x.py"},
                                    }
                                ],
                                "stop_reason": "toolUse",
                            },
                        ),
                        [
                            _node(
                                _entry(
                                    "r1",
                                    parentId="a1",
                                    message={
                                        "role": "toolResult",
                                        "tool_call_id": "t1",
                                        "tool_name": "read",
                                    },
                                )
                            )
                        ],
                    )
                ],
            )
        ]
        harness = TreeHarness(tree, "r1")
        text = harness.text()
        assert "assistant:" not in text
        # And the result names the call it answers, not just "tool".
        assert "[read: /w/x.py]" in text

    def test_an_aborted_assistant_turn_keeps_its_row(self):
        tree = [
            _node(
                _user_entry("u1", "asked"),
                [
                    _node(
                        _entry(
                            "a1",
                            parentId="u1",
                            message={
                                "role": "assistant",
                                "content": [],
                                "stop_reason": "aborted",
                            },
                        )
                    )
                ],
            )
        ]
        assert "(aborted)" in TreeHarness(tree, "u1").text()

    def test_a_label_can_be_set_from_the_tree(self):
        harness = TreeHarness([_node(_user_entry("u1", "asked"))], "u1")
        harness.send(SHIFT_L)  # `app.tree.editLabel`
        assert "Label (empty to remove)" in harness.text()

        harness.send(*"before the refactor", ENTER)
        assert harness.labels == [("u1", "before the refactor")]
        assert "[before the refactor]" in harness.text()

    def test_an_empty_label_removes_it(self):
        # The editor opens pre-filled with the label it is editing, so removing
        # one means clearing the field — which is what the prompt says. The
        # caret sits at the *start* of the pre-filled value (`Input.set_value`
        # does not move it, exactly as the TS's does not), so Ctrl+K is what
        # empties it and Backspace would do nothing.
        harness = TreeHarness([_node(_user_entry("u1", "asked"), label="old")], "u1")
        harness.send(SHIFT_L)
        harness.send("\x0b", ENTER)
        assert harness.labels == [("u1", None)]

    def test_the_label_editor_can_be_escaped(self):
        harness = TreeHarness([_node(_user_entry("u1", "asked"))], "u1")
        harness.send(SHIFT_L, ESCAPE)
        assert harness.labels == []
        assert "Label (empty to remove)" not in harness.text()

    def test_the_labeled_filter_keeps_only_labelled_entries(self):
        tree = [
            _node(
                _user_entry("u1", "asked", None),
                [_node(_user_entry("u2", "asked again", "u1"), label="here")],
            )
        ]
        harness = TreeHarness(tree, "u2")
        harness.send("\x0c")  # Ctrl+L, `app.tree.filter.labeledOnly`
        text = harness.text()
        assert "asked again" in text
        assert "user: asked\n" not in text + "\n"
        assert "(1/1)" in text
        assert "[labeled]" in text

    def test_an_empty_tree_closes_itself(self):
        harness = TreeHarness([], None)
        assert harness.cancels == 1

    def test_a_branch_is_drawn_with_connectors(self):
        tree = [
            _node(
                _user_entry("u1", "asked"),
                [
                    _node(_assistant_entry("a1", "one answer", "u1")),
                    _node(_assistant_entry("a2", "another answer", "u1")),
                ],
            )
        ]
        text = TreeHarness(tree, "a1").text()
        assert "├" in text or "└" in text

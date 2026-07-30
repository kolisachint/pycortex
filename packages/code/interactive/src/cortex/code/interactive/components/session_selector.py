"""The ``/resume`` overlay. Port of ``components/session-selector.ts``.

Lists the sessions on disk and hands back the one that is picked. Three things
about it are load-bearing and none of them is the list:

* **the scope toggle** — Tab swaps between this project's sessions and every
  project's, and the second set is loaded lazily on first ask, because
  ``listAll`` walks every session directory on the machine;
* **the threaded view** — a session records the one it was forked from, so the
  default sort draws that as a tree rather than a flat list of near-identical
  first messages;
* **the loading header** — the load is asynchronous and reports progress, so the
  overlay is usable (and cancellable) while a slow directory is still being read.

Deleting and renaming are here too, because they are the only place a session's
*file* can be managed from inside the app.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from cortex.code.interactive.components.dynamic_border import DynamicBorder
from cortex.code.interactive.components.keybinding_hints import key_hint, key_text
from cortex.code.interactive.components.session_selector_search import (
    NameFilter,
    SortMode,
    filter_and_sort_sessions,
    has_session_name,
)
from cortex.code.interactive.theme import get_theme
from cortex.tui.components import Input, Spacer, Text
from cortex.tui.keys import get_keybindings
from cortex.tui.render import Container
from cortex.tui.util import truncate_to_width, visible_width

__all__ = ["SessionSelectorComponent", "SessionsLoader", "canonicalize_path", "format_session_date"]

#: Which sessions the list is showing.
SessionScope = Literal["current", "all"]

#: Loads one scope's sessions, reporting ``(loaded, total)`` as it goes.
SessionsLoader = Callable[..., Awaitable[list[Any]]]

#: How many rows the list shows at once.
MAX_VISIBLE = 10

#: How long a status line (deleted, failed to load) stays up, in seconds.
STATUS_INFO_SECONDS = 2.0
STATUS_ERROR_SECONDS = 3.0
STATUS_LOAD_ERROR_SECONDS = 4.0


def canonicalize_path(path: str | None) -> str | None:
    """A path with its symlinks resolved, or the path itself if that fails.

    Port of ``utils/paths.canonicalizePath``. Two entries in the list can name
    the same file through different links; comparing raw strings would then show
    the current session as somebody else's.
    """
    if not path:
        return path
    try:
        return os.path.realpath(path, strict=True)
    except OSError:
        return path


def shorten_path(path: str) -> str:
    """``~``-shorten a path for the right-hand column."""
    if not path:
        return path
    home = os.path.expanduser("~")
    if path.startswith(home):
        return f"~{path[len(home) :]}"
    return path


def format_session_date(date: datetime | None) -> str:
    """How long ago, in the one or two characters the row has room for."""
    if date is None:
        return ""
    now = datetime.now(date.tzinfo) if date.tzinfo is not None else datetime.now()
    diff_seconds = (now - date).total_seconds()
    diff_minutes = int(diff_seconds // 60)
    diff_hours = int(diff_seconds // 3600)
    diff_days = int(diff_seconds // 86400)

    if diff_minutes < 1:
        return "now"
    if diff_minutes < 60:
        return f"{diff_minutes}m"
    if diff_hours < 24:
        return f"{diff_hours}h"
    if diff_days < 7:
        return f"{diff_days}d"
    if diff_days < 30:
        return f"{diff_days // 7}w"
    if diff_days < 365:
        return f"{diff_days // 30}mo"
    return f"{diff_days // 365}y"


@dataclass
class StatusMessage:
    """A line the header shows instead of its hints."""

    type: Literal["info", "error"]
    message: str


@dataclass
class SessionTreeNode:
    """A session and the sessions forked from it."""

    session: Any
    children: list[SessionTreeNode]


@dataclass
class FlatSessionNode:
    """A tree node flattened for display, with what its prefix needs."""

    session: Any
    depth: int
    is_last: bool
    #: For each ancestor level, whether it has more siblings below.
    ancestor_continues: list[bool]


def build_session_tree(sessions: list[Any]) -> list[SessionTreeNode]:
    """Group sessions under the ones they were forked from."""
    by_path: dict[str, SessionTreeNode] = {}
    for session in sessions:
        session_path = canonicalize_path(session.path) or session.path
        by_path[session_path] = SessionTreeNode(session=session, children=[])

    roots: list[SessionTreeNode] = []
    for session in sessions:
        session_path = canonicalize_path(session.path) or session.path
        node = by_path[session_path]
        parent_path = canonicalize_path(session.parent_session_path)

        if parent_path and parent_path in by_path:
            by_path[parent_path].children.append(node)
        else:
            roots.append(node)

    def modified_key(node: SessionTreeNode) -> float:
        modified = node.session.modified
        return modified.timestamp() if modified is not None else 0.0

    def sort_nodes(nodes: list[SessionTreeNode]) -> None:
        nodes.sort(key=modified_key, reverse=True)
        for node in nodes:
            sort_nodes(node.children)

    sort_nodes(roots)
    return roots


def flatten_session_tree(roots: list[SessionTreeNode]) -> list[FlatSessionNode]:
    """Depth-first order, carrying the continuation flags each row draws."""
    result: list[FlatSessionNode] = []

    def walk(
        node: SessionTreeNode, depth: int, ancestor_continues: list[bool], is_last: bool
    ) -> None:
        result.append(
            FlatSessionNode(
                session=node.session,
                depth=depth,
                is_last=is_last,
                ancestor_continues=ancestor_continues,
            )
        )
        for index, child in enumerate(node.children):
            child_is_last = index == len(node.children) - 1
            # Root-level rows never draw a continuation line: there is no gutter
            # to the left of column zero.
            continues = (not is_last) if depth > 0 else False
            walk(child, depth + 1, [*ancestor_continues, continues], child_is_last)

    for index, root in enumerate(roots):
        walk(root, 0, [], index == len(roots) - 1)

    return result


def delete_session_file(session_path: str) -> tuple[bool, str, str | None]:
    """Delete a session file. Returns ``(ok, method, error)``.

    ``trash`` first so a mis-hit Ctrl+D is recoverable, ``unlink`` when it is not
    installed. A file that is gone after ``trash`` ran counts as trashed even if
    it reported failure — some implementations exit non-zero having done the job.
    """
    trash = shutil.which("trash")
    trash_error: str | None = None
    if trash is not None:
        # A path starting with `-` would be read as a flag.
        args = ["--", session_path] if session_path.startswith("-") else [session_path]
        try:
            result = subprocess.run(  # noqa: S603 - a fixed program, one path argument
                [trash, *args], capture_output=True, text=True, check=False
            )
            if result.returncode == 0 or not os.path.exists(session_path):
                return True, "trash", None
            stderr = (result.stderr or "").strip()
            if stderr:
                trash_error = f"trash: {stderr.splitlines()[0][:200]}"
        except OSError as error:
            trash_error = f"trash: {error}"
    elif not os.path.exists(session_path):
        return True, "trash", None

    try:
        os.unlink(session_path)
        return True, "unlink", None
    except OSError as error:
        message = str(error)
        return False, "unlink", f"{message} ({trash_error})" if trash_error else message


class SessionSelectorHeader:
    """The title row and the two hint lines under it."""

    def __init__(
        self,
        scope: SessionScope,
        sort_mode: SortMode,
        name_filter: NameFilter,
        request_render: Callable[[], None],
    ) -> None:
        self._scope = scope
        self._sort_mode = sort_mode
        self._name_filter = name_filter
        self._request_render = request_render
        self._loading = False
        self._load_progress: tuple[int, int] | None = None
        self._show_path = False
        self._confirming_delete_path: str | None = None
        self._status_message: StatusMessage | None = None
        self._status_expires_at: float | None = None
        self._show_rename_hint = False

    def set_scope(self, scope: SessionScope) -> None:
        self._scope = scope

    def set_sort_mode(self, sort_mode: SortMode) -> None:
        self._sort_mode = sort_mode

    def set_name_filter(self, name_filter: NameFilter) -> None:
        self._name_filter = name_filter

    def set_loading(self, loading: bool) -> None:
        self._loading = loading
        # Progress belongs to one load; a new loading state starts without it.
        self._load_progress = None

    def set_progress(self, loaded: int, total: int) -> None:
        self._load_progress = (loaded, total)

    def set_show_path(self, show_path: bool) -> None:
        self._show_path = show_path

    def set_show_rename_hint(self, show: bool) -> None:
        self._show_rename_hint = show

    def set_confirming_delete_path(self, path: str | None) -> None:
        self._confirming_delete_path = path

    def set_status_message(
        self, message: StatusMessage | None, auto_hide_seconds: float | None = None
    ) -> None:
        """Show a status line, optionally for a while.

        The TS hides it on a ``setTimeout`` that also repaints. There is no timer
        here: the expiry is checked when the header next renders, which is the
        same thing without a callback that can outlive the overlay — the TS has
        an explicit ``clearStatusMessage`` on select and cancel precisely because
        its timer can.
        """
        self._status_message = message
        if message is None or auto_hide_seconds is None:
            self._status_expires_at = None
            return
        self._status_expires_at = _monotonic() + auto_hide_seconds

    def _active_status(self) -> StatusMessage | None:
        if self._status_message is None:
            return None
        if self._status_expires_at is not None and _monotonic() >= self._status_expires_at:
            self._status_message = None
            self._status_expires_at = None
            return None
        return self._status_message

    def invalidate(self) -> None:
        """Nothing is cached."""

    def render(self, width: int) -> list[str]:
        theme = get_theme()
        title = (
            "Resume Session (Current Folder)"
            if self._scope == "current"
            else "Resume Session (All)"
        )
        left_text = theme.bold(title)

        sort_label = {"threaded": "Threaded", "recent": "Recent"}.get(self._sort_mode, "Fuzzy")
        sort_text = theme.fg("muted", "Sort: ") + theme.fg("accent", sort_label)

        name_label = "All" if self._name_filter == "all" else "Named"
        name_text = theme.fg("muted", "Name: ") + theme.fg("accent", name_label)

        if self._loading:
            progress = (
                f"{self._load_progress[0]}/{self._load_progress[1]}"
                if self._load_progress is not None
                else "..."
            )
            scope_text = theme.fg("muted", "○ Current Folder | ") + theme.fg(
                "accent", f"Loading {progress}"
            )
        elif self._scope == "current":
            scope_text = theme.fg("accent", "◉ Current Folder") + theme.fg("muted", " | ○ All")
        else:
            scope_text = theme.fg("muted", "○ Current Folder | ") + theme.fg("accent", "◉ All")

        right_text = truncate_to_width(f"{scope_text}  {name_text}  {sort_text}", width, "")
        available_left = max(0, width - visible_width(right_text) - 1)
        left = truncate_to_width(left_text, available_left, "")
        spacing = max(0, width - visible_width(left) - visible_width(right_text))

        status = self._active_status()
        if self._confirming_delete_path is not None:
            confirm_hint = (
                f"Delete session? {key_hint('tui.select.confirm', 'confirm')} · "
                f"{key_hint('tui.select.cancel', 'cancel')}"
            )
            hint_line_1 = theme.fg("error", truncate_to_width(confirm_hint, width, "…"))
            hint_line_2 = ""
        elif status is not None:
            color = "error" if status.type == "error" else "accent"
            hint_line_1 = theme.fg(color, truncate_to_width(status.message, width, "…"))
            hint_line_2 = ""
        else:
            path_state = "(on)" if self._show_path else "(off)"
            separator = theme.fg("muted", " · ")
            hint_1 = (
                key_hint("tui.input.tab", "scope")
                + separator
                + theme.fg("muted", 're:<pattern> regex · "phrase" exact')
            )
            hint_2_parts = [
                key_hint("app.session.toggleSort", "sort"),
                key_hint("app.session.toggleNamedFilter", "named"),
                key_hint("app.session.delete", "delete"),
                key_hint("app.session.togglePath", f"path {path_state}"),
            ]
            if self._show_rename_hint:
                hint_2_parts.append(key_hint("app.session.rename", "rename"))
            hint_line_1 = truncate_to_width(hint_1, width, "…")
            hint_line_2 = truncate_to_width(separator.join(hint_2_parts), width, "…")

        return [f"{left}{' ' * spacing}{right_text}", hint_line_1, hint_line_2]


class SessionList:
    """The rows, the search box above them, and every key that acts on one."""

    def __init__(
        self,
        sessions: list[Any],
        show_cwd: bool,
        sort_mode: SortMode,
        name_filter: NameFilter,
        keybindings: Any,
        current_session_file_path: str | None = None,
    ) -> None:
        self._all_sessions = list(sessions)
        self._filtered_sessions: list[FlatSessionNode] = []
        self._selected_index = 0
        self.search_input = Input()
        self._show_cwd = show_cwd
        self._sort_mode: SortMode = sort_mode
        self._name_filter: NameFilter = name_filter
        self._keybindings = keybindings
        self._show_path = False
        self._confirming_delete_path: str | None = None
        self._current_session_canonical_path = canonicalize_path(current_session_file_path)
        self._max_visible = MAX_VISIBLE

        self.on_select: Callable[[str], None] | None = None
        self.on_cancel: Callable[[], None] | None = None
        self.on_exit: Callable[[], None] = lambda: None
        self.on_toggle_scope: Callable[[], None] | None = None
        self.on_toggle_sort: Callable[[], None] | None = None
        self.on_toggle_name_filter: Callable[[], None] | None = None
        self.on_toggle_path: Callable[[bool], None] | None = None
        self.on_delete_confirmation_change: Callable[[str | None], None] | None = None
        self.on_delete_session: Callable[[str], None] | None = None
        self.on_rename_session: Callable[[str], None] | None = None
        self.on_error: Callable[[str], None] | None = None

        self._focused = False
        self._filter_sessions("")

        self.search_input.on_submit = self._submit_search

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        self._focused = value
        self.search_input.focused = value

    def _submit_search(self, _value: str) -> None:
        """Enter inside the search box picks the highlighted row."""
        selected = self._selected()
        if selected is not None and self.on_select is not None:
            self.on_select(selected.session.path)

    def _selected(self) -> FlatSessionNode | None:
        if 0 <= self._selected_index < len(self._filtered_sessions):
            return self._filtered_sessions[self._selected_index]
        return None

    def get_selected_session_path(self) -> str | None:
        selected = self._selected()
        return selected.session.path if selected is not None else None

    def set_sort_mode(self, sort_mode: SortMode) -> None:
        self._sort_mode = sort_mode
        self._filter_sessions(self.search_input.get_value())

    def set_name_filter(self, name_filter: NameFilter) -> None:
        self._name_filter = name_filter
        self._filter_sessions(self.search_input.get_value())

    def set_sessions(self, sessions: list[Any], show_cwd: bool) -> None:
        self._all_sessions = list(sessions)
        self._show_cwd = show_cwd
        self._filter_sessions(self.search_input.get_value())

    def _filter_sessions(self, query: str) -> None:
        trimmed = query.strip()
        name_filtered = (
            self._all_sessions
            if self._name_filter == "all"
            else [s for s in self._all_sessions if has_session_name(s)]
        )

        if self._sort_mode == "threaded" and not trimmed:
            # The tree only makes sense unsearched: a query hides the parents
            # that the indentation hangs off.
            self._filtered_sessions = flatten_session_tree(build_session_tree(name_filtered))
        else:
            filtered = filter_and_sort_sessions(name_filtered, query, self._sort_mode, "all")
            self._filtered_sessions = [
                FlatSessionNode(session=session, depth=0, is_last=True, ancestor_continues=[])
                for session in filtered
            ]

        self._selected_index = min(self._selected_index, max(0, len(self._filtered_sessions) - 1))

    def _set_confirming_delete_path(self, path: str | None) -> None:
        self._confirming_delete_path = path
        if self.on_delete_confirmation_change is not None:
            self.on_delete_confirmation_change(path)

    def _start_delete_confirmation_for_selected_session(self) -> None:
        selected = self._selected()
        if selected is None:
            return

        if self._is_current_session_path(selected.session.path):
            if self.on_error is not None:
                self.on_error("Cannot delete the currently active session")
            return

        self._set_confirming_delete_path(selected.session.path)

    def _is_current_session_path(self, path: str) -> bool:
        if not self._current_session_canonical_path:
            return False
        return (canonicalize_path(path) or path) == self._current_session_canonical_path

    def invalidate(self) -> None:
        """Nothing is cached."""

    def render(self, width: int) -> list[str]:  # noqa: C901 - 1:1 with the TS row builder
        theme = get_theme()
        lines: list[str] = []

        lines.extend(self.search_input.render(width))
        lines.append("")

        if not self._filtered_sessions:
            if self._name_filter == "named":
                toggle_key = key_text("app.session.toggleNamedFilter")
                if self._show_cwd:
                    empty_message = f"  No named sessions found. Press {toggle_key} to show all."
                else:
                    empty_message = (
                        f"  No named sessions in current folder. Press {toggle_key} to show all, "
                        "or Tab to view all."
                    )
            elif self._show_cwd:
                empty_message = "  No sessions found"
            else:
                empty_message = "  No sessions in current folder. Press Tab to view all."
            lines.append(theme.fg("muted", truncate_to_width(empty_message, width, "…")))
            return lines

        start_index = max(
            0,
            min(
                self._selected_index - self._max_visible // 2,
                len(self._filtered_sessions) - self._max_visible,
            ),
        )
        end_index = min(start_index + self._max_visible, len(self._filtered_sessions))

        for index in range(start_index, end_index):
            node = self._filtered_sessions[index]
            session = node.session
            is_selected = index == self._selected_index
            is_confirming_delete = session.path == self._confirming_delete_path
            is_current = self._is_current_session_path(session.path)

            prefix = self._build_tree_prefix(node)

            has_name = bool(session.name)
            display_text = session.name if has_name else session.first_message
            normalized = "".join(
                " " if ord(char) < 0x20 or ord(char) == 0x7F else char for char in str(display_text)
            ).strip()

            age = format_session_date(session.modified)
            right_part = f"{session.message_count} {age}"
            if self._show_cwd and session.cwd:
                right_part = f"{shorten_path(session.cwd)} {right_part}"
            if self._show_path:
                right_part = f"{shorten_path(session.path)} {right_part}"

            cursor = theme.fg("accent", "› ") if is_selected else "  "

            prefix_width = visible_width(prefix)
            right_width = visible_width(right_part) + 2
            available_for_message = width - 2 - prefix_width - right_width
            truncated = truncate_to_width(normalized, max(10, available_for_message), "…")

            message_color: str | None = None
            if is_confirming_delete:
                message_color = "error"
            elif is_current:
                message_color = "accent"
            elif has_name:
                message_color = "warning"
            styled_message = theme.fg(message_color, truncated) if message_color else truncated
            if is_selected:
                styled_message = theme.bold(styled_message)

            left_part = cursor + theme.fg("dim", prefix) + styled_message
            spacing = max(1, width - visible_width(left_part) - visible_width(right_part))
            styled_right = theme.fg("error" if is_confirming_delete else "dim", right_part)

            line = left_part + " " * spacing + styled_right
            if is_selected:
                line = theme.bg("selectedBg", line)
            lines.append(truncate_to_width(line, width))

        if start_index > 0 or end_index < len(self._filtered_sessions):
            scroll_text = f"  ({self._selected_index + 1}/{len(self._filtered_sessions)})"
            lines.append(theme.fg("muted", truncate_to_width(scroll_text, width, "")))

        return lines

    def _build_tree_prefix(self, node: FlatSessionNode) -> str:
        if node.depth == 0:
            return ""
        parts = ["│  " if continues else "   " for continues in node.ancestor_continues]
        branch = "└─ " if node.is_last else "├─ "
        return "".join(parts) + branch

    def handle_input(self, data: str) -> None:  # noqa: C901 - 1:1 with the TS dispatch
        keybindings = get_keybindings()

        # A pending delete swallows every key: the next Enter must mean "yes",
        # not "resume this session".
        if self._confirming_delete_path is not None:
            if keybindings.matches(data, "tui.select.confirm"):
                path_to_delete = self._confirming_delete_path
                self._set_confirming_delete_path(None)
                if self.on_delete_session is not None:
                    self.on_delete_session(path_to_delete)
            elif keybindings.matches(data, "tui.select.cancel"):
                self._set_confirming_delete_path(None)
            return

        if keybindings.matches(data, "tui.input.tab"):
            if self.on_toggle_scope is not None:
                self.on_toggle_scope()
            return

        if keybindings.matches(data, "app.session.toggleSort"):
            if self.on_toggle_sort is not None:
                self.on_toggle_sort()
            return

        # The one binding read off the app's own manager rather than the global,
        # as in the TS.
        if self._keybindings.matches(data, "app.session.toggleNamedFilter"):
            if self.on_toggle_name_filter is not None:
                self.on_toggle_name_filter()
            return

        if keybindings.matches(data, "app.session.togglePath"):
            self._show_path = not self._show_path
            if self.on_toggle_path is not None:
                self.on_toggle_path(self._show_path)
            return

        if keybindings.matches(data, "app.session.delete"):
            self._start_delete_confirmation_for_selected_session()
            return

        if keybindings.matches(data, "app.session.rename"):
            selected = self._selected()
            if selected is not None and self.on_rename_session is not None:
                self.on_rename_session(selected.session.path)
            return

        # Ctrl+Backspace deletes a session only when there is no query to delete
        # a word from; otherwise it is an editing key like any other.
        if keybindings.matches(data, "app.session.deleteNoninvasive"):
            if self.search_input.get_value():
                self.search_input.handle_input(data)
                self._filter_sessions(self.search_input.get_value())
                return
            self._start_delete_confirmation_for_selected_session()
            return

        if keybindings.matches(data, "tui.select.up"):
            self._selected_index = max(0, self._selected_index - 1)
        elif keybindings.matches(data, "tui.select.down"):
            self._selected_index = min(len(self._filtered_sessions) - 1, self._selected_index + 1)
        elif keybindings.matches(data, "tui.select.pageUp"):
            self._selected_index = max(0, self._selected_index - self._max_visible)
        elif keybindings.matches(data, "tui.select.pageDown"):
            self._selected_index = min(
                len(self._filtered_sessions) - 1, self._selected_index + self._max_visible
            )
        elif keybindings.matches(data, "tui.select.confirm"):
            selected = self._selected()
            if selected is not None and self.on_select is not None:
                self.on_select(selected.session.path)
        elif keybindings.matches(data, "tui.select.cancel"):
            if self.on_cancel is not None:
                self.on_cancel()
        else:
            self.search_input.handle_input(data)
            self._filter_sessions(self.search_input.get_value())


class SessionSelectorComponent(Container):
    """The overlay: header, list, and the rename panel that replaces both."""

    def __init__(
        self,
        current_sessions_loader: SessionsLoader,
        all_sessions_loader: SessionsLoader,
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
        on_exit: Callable[[], None],
        request_render: Callable[[], None],
        rename_session: Callable[[str, str | None], Any] | None = None,
        show_rename_hint: bool | None = None,
        keybindings: Any = None,
        current_session_file_path: str | None = None,
        schedule: Callable[[Any], None] | None = None,
    ) -> None:
        super().__init__()
        from cortex.code.interactive.keybindings import KeybindingsManager

        self._keybindings = keybindings if keybindings is not None else KeybindingsManager.create()
        self._current_sessions_loader = current_sessions_loader
        self._all_sessions_loader = all_sessions_loader
        self._on_cancel = on_cancel
        self._request_render = request_render
        self._schedule = schedule if schedule is not None else _schedule_coroutine

        self._scope: SessionScope = "current"
        self._sort_mode: SortMode = "threaded"
        self._name_filter: NameFilter = "all"
        self._current_sessions: list[Any] | None = None
        self._all_sessions: list[Any] | None = None
        self._current_loading = False
        self._all_loading = False
        self._all_load_seq = 0

        self._mode: Literal["list", "rename"] = "list"
        self.rename_input = Input()
        self._rename_target_path: str | None = None
        self._rename_session = rename_session
        self._can_rename = rename_session is not None
        self._focused = False

        self.header = SessionSelectorHeader(
            self._scope, self._sort_mode, self._name_filter, request_render
        )
        self.header.set_show_rename_hint(
            show_rename_hint if show_rename_hint is not None else self._can_rename
        )

        self.session_list = SessionList(
            [],
            False,
            self._sort_mode,
            self._name_filter,
            self._keybindings,
            current_session_file_path,
        )
        self._build_base_layout(self.session_list)

        self.rename_input.on_submit = lambda value: self._schedule(self._confirm_rename(value))

        def clear_status() -> None:
            self.header.set_status_message(None)

        def select(session_path: str) -> None:
            clear_status()
            on_select(session_path)

        def cancel() -> None:
            clear_status()
            on_cancel()

        def exit_app() -> None:
            clear_status()
            on_exit()

        self.session_list.on_select = select
        self.session_list.on_cancel = cancel
        self.session_list.on_exit = exit_app
        self.session_list.on_toggle_scope = self._toggle_scope
        self.session_list.on_toggle_sort = self._toggle_sort_mode
        self.session_list.on_toggle_name_filter = self._toggle_name_filter
        self.session_list.on_rename_session = self._begin_rename
        self.session_list.on_toggle_path = self._on_toggle_path
        self.session_list.on_delete_confirmation_change = self._on_delete_confirmation_change
        self.session_list.on_error = self._on_list_error
        self.session_list.on_delete_session = lambda path: self._schedule(
            self._delete_session(path)
        )

        self._schedule(self._load_scope("current", "initial"))

    # ---- focus ------------------------------------------------------------

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        self._focused = value
        self.session_list.focused = value
        self.rename_input.focused = value

    # ---- layout -----------------------------------------------------------

    def _build_base_layout(self, content: Any, show_header: bool = True) -> None:
        self.clear()
        theme = get_theme()

        def accent(text: str) -> str:
            return theme.fg("accent", text)

        self.add_child(Spacer(1))
        self.add_child(DynamicBorder(accent))
        self.add_child(Spacer(1))
        if show_header:
            self.add_child(self.header)
            self.add_child(Spacer(1))
        self.add_child(content)
        self.add_child(Spacer(1))
        self.add_child(DynamicBorder(accent))

    # ---- loading ----------------------------------------------------------

    async def _load_scope(
        self, scope: SessionScope, reason: Literal["initial", "refresh", "toggle"]
    ) -> None:
        show_cwd = scope == "all"

        if scope == "current":
            self._current_loading = True
        else:
            self._all_loading = True

        seq: int | None = None
        if scope == "all":
            self._all_load_seq += 1
            seq = self._all_load_seq

        self.header.set_scope(scope)
        self.header.set_loading(True)
        self._request_render()

        def on_progress(loaded: int, total: int) -> None:
            # A load that finished after the user moved on must not repaint the
            # scope they are looking at now.
            if scope != self._scope:
                return
            if seq is not None and seq != self._all_load_seq:
                return
            self.header.set_progress(loaded, total)
            self._request_render()

        try:
            loader = (
                self._current_sessions_loader if scope == "current" else self._all_sessions_loader
            )
            sessions = await loader(on_progress)

            if scope == "current":
                self._current_sessions = sessions
                self._current_loading = False
            else:
                self._all_sessions = sessions
                self._all_loading = False

            if scope != self._scope:
                return
            if seq is not None and seq != self._all_load_seq:
                return

            self.header.set_loading(False)
            self.session_list.set_sessions(sessions, show_cwd)
            self._request_render()

            # Nothing anywhere: the overlay has nothing to offer, so it closes
            # rather than sitting there empty.
            if scope == "all" and not sessions and not (self._current_sessions or []):
                self._on_cancel()
        except Exception as error:  # noqa: BLE001 - the TS catch, one for one
            if scope == "current":
                self._current_loading = False
            else:
                self._all_loading = False

            if scope != self._scope:
                return
            if seq is not None and seq != self._all_load_seq:
                return

            self.header.set_loading(False)
            self.header.set_status_message(
                StatusMessage("error", f"Failed to load sessions: {error}"),
                STATUS_LOAD_ERROR_SECONDS,
            )
            if reason == "initial":
                self.session_list.set_sessions([], show_cwd)
            self._request_render()

    # ---- list callbacks ---------------------------------------------------

    def _on_toggle_path(self, show_path: bool) -> None:
        self.header.set_show_path(show_path)
        self._request_render()

    def _on_delete_confirmation_change(self, path: str | None) -> None:
        self.header.set_confirming_delete_path(path)
        self._request_render()

    def _on_list_error(self, message: str) -> None:
        self.header.set_status_message(StatusMessage("error", message), STATUS_ERROR_SECONDS)
        self._request_render()

    async def _delete_session(self, session_path: str) -> None:
        ok, method, error = delete_session_file(session_path)

        if ok:
            if self._current_sessions is not None:
                self._current_sessions = [
                    s for s in self._current_sessions if s.path != session_path
                ]
            if self._all_sessions is not None:
                self._all_sessions = [s for s in self._all_sessions if s.path != session_path]

            sessions = (
                self._all_sessions if self._scope == "all" else self._current_sessions
            ) or []
            self.session_list.set_sessions(sessions, self._scope == "all")

            message = "Session moved to trash" if method == "trash" else "Session deleted"
            self.header.set_status_message(StatusMessage("info", message), STATUS_INFO_SECONDS)
            await self._load_scope(self._scope, "refresh")
        else:
            self.header.set_status_message(
                StatusMessage("error", f"Failed to delete: {error or 'Unknown error'}"),
                STATUS_ERROR_SECONDS,
            )

        self._request_render()

    # ---- renaming ---------------------------------------------------------

    def _begin_rename(self, session_path: str) -> None:
        if self._rename_session is None:
            return
        # Renaming a row the list is about to replace would write the name onto
        # whatever ends up in its place.
        if self._scope == "current" and self._current_loading:
            return
        if self._scope == "all" and self._all_loading:
            return

        sessions = (self._all_sessions if self._scope == "all" else self._current_sessions) or []
        session = next((s for s in sessions if s.path == session_path), None)
        self._enter_rename_mode(session_path, session.name if session is not None else None)

    def _enter_rename_mode(self, session_path: str, current_name: str | None) -> None:
        self._mode = "rename"
        self._rename_target_path = session_path
        self.rename_input.set_value(current_name or "")
        self.rename_input.focused = True

        theme = get_theme()
        panel = Container()
        panel.add_child(Text(theme.bold("Rename Session"), 1, 0))
        panel.add_child(Spacer(1))
        panel.add_child(self.rename_input)
        panel.add_child(Spacer(1))
        panel.add_child(
            Text(
                theme.fg(
                    "muted",
                    f"{key_text('tui.select.confirm')} to save · "
                    f"{key_text('tui.select.cancel')} to cancel",
                ),
                1,
                0,
            )
        )

        self._build_base_layout(panel, show_header=False)
        self._request_render()

    def _exit_rename_mode(self) -> None:
        self._mode = "list"
        self._rename_target_path = None
        self._build_base_layout(self.session_list)
        self._request_render()

    async def _confirm_rename(self, value: str) -> None:
        next_name = value.strip()
        if not next_name:
            return
        target = self._rename_target_path
        rename_session = self._rename_session
        if target is None or rename_session is None:
            self._exit_rename_mode()
            return

        try:
            outcome = rename_session(target, next_name)
            if hasattr(outcome, "__await__"):
                await outcome
            await self._load_scope(self._scope, "refresh")
        finally:
            self._exit_rename_mode()

    # ---- toggles ----------------------------------------------------------

    def _toggle_sort_mode(self) -> None:
        # threaded → recent → relevance → threaded
        if self._sort_mode == "threaded":
            self._sort_mode = "recent"
        elif self._sort_mode == "recent":
            self._sort_mode = "relevance"
        else:
            self._sort_mode = "threaded"
        self.header.set_sort_mode(self._sort_mode)
        self.session_list.set_sort_mode(self._sort_mode)
        self._request_render()

    def _toggle_name_filter(self) -> None:
        self._name_filter = "named" if self._name_filter == "all" else "all"
        self.header.set_name_filter(self._name_filter)
        self.session_list.set_name_filter(self._name_filter)
        self._request_render()

    def _toggle_scope(self) -> None:
        if self._scope == "current":
            self._scope = "all"
            self.header.set_scope(self._scope)

            # Loaded once and kept: walking every project's sessions again on
            # each Tab is the one thing this overlay can be slow at.
            if self._all_sessions is not None:
                self.header.set_loading(False)
                self.session_list.set_sessions(self._all_sessions, True)
                self._request_render()
                return

            if not self._all_loading:
                self._schedule(self._load_scope("all", "toggle"))
            return

        self._scope = "current"
        self.header.set_scope(self._scope)
        self.header.set_loading(self._current_loading)
        self.session_list.set_sessions(self._current_sessions or [], False)
        self._request_render()

    # ---- input ------------------------------------------------------------

    def handle_input(self, data: str) -> None:
        if self._mode == "rename":
            if get_keybindings().matches(data, "tui.select.cancel"):
                self._exit_rename_mode()
                return
            self.rename_input.handle_input(data)
            return

        self.session_list.handle_input(data)

    def get_session_list(self) -> SessionList:
        return self.session_list


def _monotonic() -> float:
    import time

    return time.monotonic()


def _schedule_coroutine(coro: Any) -> None:
    """Run a coroutine reached from a keystroke, loop or no loop.

    The same join the app makes (see ``interactive_mode._schedule``): with a loop
    running the work goes onto it, and without one — a component driven directly
    by a test — it is run to completion rather than dropped.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    loop.create_task(coro)

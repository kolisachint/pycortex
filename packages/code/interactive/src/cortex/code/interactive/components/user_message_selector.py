"""The fork picker. Port of ``components/user-message-selector.ts``.

Lists the user messages on the current branch so one can be forked from. Two
lines per row — the message and its position — because the thing you are picking
is a point in a conversation, and "the third of eight" is how people remember
where that was.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cortex.code.interactive.components.dynamic_border import DynamicBorder
from cortex.code.interactive.theme import get_theme
from cortex.tui.components import Spacer, Text
from cortex.tui.keys import get_keybindings
from cortex.tui.render import Container
from cortex.tui.util import truncate_to_width

__all__ = ["UserMessageItem", "UserMessageSelectorComponent"]

#: How many messages are on screen at once.
MAX_VISIBLE = 10


@dataclass(frozen=True)
class UserMessageItem:
    """One row: an entry in the session, and the text it holds."""

    id: str
    text: str
    timestamp: str | None = None


class UserMessageList:
    """The list itself: rows, a cursor, and the four keys that move it."""

    def __init__(
        self, messages: list[UserMessageItem], initial_selected_id: str | None = None
    ) -> None:
        # Chronological, oldest first — the order they were sent.
        self._messages = list(messages)
        self.on_select: Callable[[str], None] | None = None
        self.on_cancel: Callable[[], None] | None = None
        self._max_visible = MAX_VISIBLE

        initial_index = -1
        if initial_selected_id is not None:
            initial_index = next(
                (i for i, m in enumerate(self._messages) if m.id == initial_selected_id), -1
            )
        # Default to the most recent message: the one you just sent is the one
        # you are most likely re-asking.
        self._selected_index = (
            initial_index if initial_index >= 0 else max(0, len(self._messages) - 1)
        )

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def invalidate(self) -> None:
        """Nothing is cached."""

    def render(self, width: int) -> list[str]:
        theme = get_theme()
        lines: list[str] = []

        if not self._messages:
            return [theme.fg("muted", "  No user messages found")]

        start_index = max(
            0,
            min(
                self._selected_index - self._max_visible // 2,
                len(self._messages) - self._max_visible,
            ),
        )
        end_index = min(start_index + self._max_visible, len(self._messages))

        for index in range(start_index, end_index):
            message = self._messages[index]
            is_selected = index == self._selected_index

            normalized = message.text.replace("\n", " ").strip()
            cursor = theme.fg("accent", "› ") if is_selected else "  "
            truncated = truncate_to_width(normalized, width - 2)
            lines.append(cursor + (theme.bold(truncated) if is_selected else truncated))
            lines.append(theme.fg("muted", f"  Message {index + 1} of {len(self._messages)}"))
            lines.append("")

        if start_index > 0 or end_index < len(self._messages):
            lines.append(theme.fg("muted", f"  ({self._selected_index + 1}/{len(self._messages)})"))

        return lines

    def handle_input(self, data: str) -> None:
        keybindings = get_keybindings()

        # Up and down wrap: the list is short and the ends are where the
        # interesting messages are (the first question, the last one).
        if keybindings.matches(data, "tui.select.up"):
            self._selected_index = (
                len(self._messages) - 1 if self._selected_index == 0 else self._selected_index - 1
            )
        elif keybindings.matches(data, "tui.select.down"):
            self._selected_index = (
                0 if self._selected_index == len(self._messages) - 1 else self._selected_index + 1
            )
        elif keybindings.matches(data, "tui.select.confirm"):
            if 0 <= self._selected_index < len(self._messages) and self.on_select is not None:
                self.on_select(self._messages[self._selected_index].id)
        elif keybindings.matches(data, "tui.select.cancel"):
            if self.on_cancel is not None:
                self.on_cancel()


class UserMessageSelectorComponent(Container):
    """The fork overlay: a header, a border, and the message list."""

    def __init__(
        self,
        messages: list[UserMessageItem],
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
        initial_selected_id: str | None = None,
    ) -> None:
        super().__init__()
        theme = get_theme()

        self.add_child(Spacer(1))
        self.add_child(Text(theme.bold("Fork from Message"), 1, 0))
        self.add_child(
            Text(
                theme.fg(
                    "muted",
                    "Select a user message to copy the active path up to that point "
                    "into a new session",
                ),
                1,
                0,
            )
        )
        self.add_child(Spacer(1))
        self.add_child(DynamicBorder())
        self.add_child(Spacer(1))

        self.message_list = UserMessageList(messages, initial_selected_id)
        self.message_list.on_select = on_select
        self.message_list.on_cancel = on_cancel
        self.add_child(self.message_list)

        self.add_child(Spacer(1))
        self.add_child(DynamicBorder())

        # An empty list is a picker with nothing to pick; the TS closes it on a
        # timer. The caller checks first, so this is the belt to that's braces.
        if not messages:
            on_cancel()

    def handle_input(self, data: str) -> None:
        self.message_list.handle_input(data)

    def get_message_list(self) -> UserMessageList:
        return self.message_list

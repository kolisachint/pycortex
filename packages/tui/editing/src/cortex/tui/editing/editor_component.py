"""Interface for custom editor components.

This allows extensions to provide their own editor implementation
(e.g., vim mode, emacs mode, custom keybindings) while maintaining
compatibility with the core application.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from cortex.tui.render import Component


class AutocompleteItem(Protocol):
    """Protocol for autocomplete items."""

    @property
    def value(self) -> str:
        """The value of the item."""
        ...

    @property
    def label(self) -> str:
        """The label of the item."""
        ...

    @property
    def description(self) -> str | None:
        """The description of the item."""
        ...


class AutocompleteSuggestions(Protocol):
    """Protocol for autocomplete suggestions."""

    @property
    def items(self) -> list[AutocompleteItem]:
        """The list of items."""
        ...

    @property
    def prefix(self) -> str:
        """The prefix being matched against."""
        ...


class CompletionResult(Protocol):
    """Protocol for completion result."""

    @property
    def lines(self) -> list[str]:
        """The resulting lines."""
        ...

    @property
    def cursor_line(self) -> int:
        """The resulting cursor line."""
        ...

    @property
    def cursor_col(self) -> int:
        """The resulting cursor column."""
        ...


@runtime_checkable
class AutocompleteProvider(Protocol):
    """Protocol for autocomplete providers."""

    async def get_suggestions(
        self,
        lines: list[str],
        cursor_line: int,
        cursor_col: int,
        *,
        force: bool = False,
    ) -> AutocompleteSuggestions | None:
        """Get autocomplete suggestions for current text/cursor position.

        Returns None if no suggestions available.
        """
        ...

    def apply_completion(
        self,
        lines: list[str],
        cursor_line: int,
        cursor_col: int,
        item: AutocompleteItem,
        prefix: str,
    ) -> CompletionResult:
        """Apply the selected item.

        Returns result with 'lines', 'cursor_line', 'cursor_col'.
        """
        ...


@runtime_checkable
class EditorComponent(Component, Protocol):
    """Interface for custom editor components."""

    # =========================================================================
    # Core text access (required)
    # =========================================================================

    def get_text(self) -> str:
        """Get the current text content."""
        ...

    def set_text(self, text: str) -> None:
        """Set the text content."""
        ...

    # =========================================================================
    # Callbacks (optional)
    # =========================================================================

    on_submit: Callable[[str], None] | None
    on_change: Callable[[str], None] | None

    # =========================================================================
    # History support (optional)
    # =========================================================================

    def add_to_history(self, text: str) -> None:
        """Add text to history for up/down navigation."""
        ...

    # =========================================================================
    # Advanced text manipulation (optional)
    # =========================================================================

    def insert_text_at_cursor(self, text: str) -> None:
        """Insert text at current cursor position."""
        ...

    def get_expanded_text(self) -> str:
        """Get text with any markers expanded (e.g., paste markers).

        Falls back to get_text() if not implemented.
        """
        ...

    # =========================================================================
    # Autocomplete support (optional)
    # =========================================================================

    def set_autocomplete_provider(self, provider: AutocompleteProvider) -> None:
        """Set the autocomplete provider."""
        ...

    # =========================================================================
    # Appearance (optional)
    # =========================================================================

    def border_color(self, s: str) -> str:
        """Border color function."""
        ...

    prompt_prefix: str | None
    prompt_color: Callable[[str], str] | None

    def set_padding_x(self, padding: int) -> None:
        """Set horizontal padding."""
        ...

    def set_autocomplete_max_visible(self, max_visible: int) -> None:
        """Set max visible items in autocomplete dropdown."""
        ...

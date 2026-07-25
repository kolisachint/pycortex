"""Markdown component — a `marked` token tree rendered as styled terminal lines.

Mechanical port of hoocode's ``packages/tui/src/components/markdown.ts``.

The token tree comes from :mod:`cortex.tui.components._markdown_ast`, which
normalises mistune's AST into marked's shape (step 1.18); every branch below
reads the same fields the TypeScript does.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cortex.tui.components._markdown_ast import lex_markdown
from cortex.tui.util import (
    apply_background_to_line,
    is_image_line,
    visible_width,
    wrap_text_with_ansi,
)

__all__ = ["DefaultTextStyle", "Markdown", "MarkdownTheme"]

Token = dict[str, Any]

SENTINEL = "\x00"
MAX_UNBROKEN_WORD_WIDTH = 30


def _images_symbol(name: str) -> Any:
    """Look up a `cortex.tui.images` symbol, or None while step 1.15 is pending.

    Same soft dependency `cortex.tui.render` takes on the images leaf: the leaf
    exists but is empty, so a static import of a name it does not export yet
    would not type-check, and stubbing it here would duplicate 1.15's scope.
    """
    try:
        module = importlib.import_module("cortex.tui.images")
    except ImportError:
        return None
    return getattr(module, name, None)


def _hyperlinks_supported() -> bool:
    """Whether links should be emitted as OSC 8 rather than `text (url)`.

    The TS reads ``getCapabilities().hyperlinks`` from ``terminal-image.ts``,
    which is step 1.15. Until it lands this reports "no hyperlink support" —
    a real runtime state (the TS takes the same branch on any terminal it has
    not positively identified) rather than an invented one.
    """
    get_capabilities = _images_symbol("get_capabilities")
    if get_capabilities is None:
        return False
    return bool(get_capabilities().hyperlinks)


def _hyperlink(text: str, url: str) -> str:
    """OSC 8 hyperlink sequence.

    Owned by ``terminal-image.ts`` → the images leaf (step 1.15). Inlined here
    (one format string) so the link branch is a real port and testable today;
    1.15 replaces this with an import.
    """
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


@dataclass
class DefaultTextStyle:
    """Default text styling for markdown content.

    Applied to all text unless overridden by markdown formatting.
    """

    color: Callable[[str], str] | None = None
    bg_color: Callable[[str], str] | None = None
    bold: bool = False
    italic: bool = False
    strikethrough: bool = False
    underline: bool = False


@dataclass
class MarkdownTheme:
    """Theme functions for markdown elements.

    Each function takes text and returns styled text with ANSI codes.
    """

    heading: Callable[[str], str]
    link: Callable[[str], str]
    link_url: Callable[[str], str]
    code: Callable[[str], str]
    code_block: Callable[[str], str]
    code_block_border: Callable[[str], str]
    quote: Callable[[str], str]
    quote_border: Callable[[str], str]
    hr: Callable[[str], str]
    list_bullet: Callable[[str], str]
    bold: Callable[[str], str]
    italic: Callable[[str], str]
    strikethrough: Callable[[str], str]
    underline: Callable[[str], str]
    highlight_code: Callable[[str, str | None], list[str]] | None = None
    """Optional syntax highlighter; returns one styled line per code line."""
    code_block_indent: str | None = None
    """Prefix applied to each rendered code block line (default: "  ")."""


@dataclass
class _InlineStyleContext:
    apply_text: Callable[[str], str]
    style_prefix: str


@dataclass
class _CachedTokens:
    text: str
    tokens: list[Token] = field(default_factory=list)


class Markdown:
    """Markdown component - renders markdown text with theme-driven styling."""

    # Rendered-line cache, keyed per width so a terminal resize (or pane toggle)
    # back to a recent width reuses the wrapped output instead of re-rendering.
    # Small FIFO: transcripts bounce between a handful of widths at most.
    LINE_CACHE_WIDTHS = 3

    def __init__(
        self,
        text: str,
        padding_x: int,
        padding_y: int,
        theme: MarkdownTheme,
        default_text_style: DefaultTextStyle | None = None,
    ) -> None:
        self._text = text
        self._padding_x = padding_x
        self._padding_y = padding_y
        self._theme = theme
        self._default_text_style = default_text_style
        self._default_style_prefix: str | None = None
        self._line_cache: dict[int, list[str]] = {}
        self._line_cache_text: str | None = None
        # Lexed tokens depend only on the text, not width or theme — cached
        # separately so a resize reflow only re-wraps instead of re-parsing.
        self._cached_tokens: _CachedTokens | None = None

    def set_text(self, text: str) -> None:
        self._text = text
        self.invalidate()

    def invalidate(self) -> None:
        # Drop rendered lines (they bake in theme + width) but keep the lexed
        # tokens: they are a pure function of the text, and set_text/render
        # detect text changes by comparison.
        self._line_cache.clear()
        self._line_cache_text = None

    def render(self, width: int) -> list[str]:  # noqa: C901 - 1:1 with the TS
        # Check cache
        if self._line_cache_text == self._text:
            cached = self._line_cache.get(width)
            # An empty render is a cache hit too: the TS stores `[]` for blank
            # text and `if (cached)` is true for an empty array in JS.
            if cached is not None:
                return cached
        else:
            self._line_cache.clear()
            self._line_cache_text = self._text

        # Calculate available width for content (subtract horizontal padding)
        content_width = max(1, width - self._padding_x * 2)

        # Don't render anything if there's no actual text
        if not self._text or self._text.strip() == "":
            empty: list[str] = []
            self._store_lines(width, empty)
            return empty

        # Parse markdown to HTML-like tokens (tabs normalized to 3 spaces first
        # for consistent rendering). Reused across widths for the same text.
        if self._cached_tokens is not None and self._cached_tokens.text == self._text:
            tokens = self._cached_tokens.tokens
        else:
            tokens = lex_markdown(self._text.replace("\t", "   "))
            self._cached_tokens = _CachedTokens(self._text, tokens)

        # Convert tokens to styled terminal output
        rendered_lines: list[str] = []

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None
            token_lines = self._render_token(
                token, content_width, next_token["type"] if next_token else None
            )
            rendered_lines.extend(token_lines)

        # Wrap lines (NO padding, NO background yet)
        wrapped_lines: list[str] = []
        for line in rendered_lines:
            if is_image_line(line):
                wrapped_lines.append(line)
            else:
                wrapped_lines.extend(wrap_text_with_ansi(line, content_width))

        # Add margins and background to each wrapped line
        left_margin = " " * self._padding_x
        right_margin = " " * self._padding_x
        bg_fn = self._default_text_style.bg_color if self._default_text_style else None
        content_lines: list[str] = []

        for line in wrapped_lines:
            if is_image_line(line):
                content_lines.append(line)
                continue

            line_with_margins = left_margin + line + right_margin

            if bg_fn:
                content_lines.append(apply_background_to_line(line_with_margins, width, bg_fn))
            else:
                # No background - just pad to width
                visible_len = visible_width(line_with_margins)
                padding_needed = max(0, width - visible_len)
                content_lines.append(line_with_margins + " " * padding_needed)

        # Add top/bottom padding (empty lines)
        empty_line = " " * width
        empty_lines: list[str] = []
        for _ in range(self._padding_y):
            line = apply_background_to_line(empty_line, width, bg_fn) if bg_fn else empty_line
            empty_lines.append(line)

        # Combine top padding, content, and bottom padding
        combined = [*empty_lines, *content_lines, *empty_lines]
        result = combined if combined else [""]

        self._store_lines(width, result)
        return result

    def _store_lines(self, width: int, lines: list[str]) -> None:
        if len(self._line_cache) >= Markdown.LINE_CACHE_WIDTHS:
            oldest = next(iter(self._line_cache), None)
            if oldest is not None:
                del self._line_cache[oldest]
        self._line_cache[width] = lines

    def _apply_default_style(self, text: str) -> str:
        """Apply default text style to a string.

        This is the base styling applied to all text content.
        NOTE: Background color is NOT applied here - it's applied at the padding
        stage to ensure it extends to the full line width.
        """
        if not self._default_text_style:
            return text

        styled = text

        # Apply foreground color (NOT background - that's applied at padding stage)
        if self._default_text_style.color:
            styled = self._default_text_style.color(styled)

        # Apply text decorations using this._theme
        if self._default_text_style.bold:
            styled = self._theme.bold(styled)
        if self._default_text_style.italic:
            styled = self._theme.italic(styled)
        if self._default_text_style.strikethrough:
            styled = self._theme.strikethrough(styled)
        if self._default_text_style.underline:
            styled = self._theme.underline(styled)

        return styled

    def _get_default_style_prefix(self) -> str:
        if not self._default_text_style:
            return ""

        if self._default_style_prefix is not None:
            return self._default_style_prefix

        styled = SENTINEL

        if self._default_text_style.color:
            styled = self._default_text_style.color(styled)

        if self._default_text_style.bold:
            styled = self._theme.bold(styled)
        if self._default_text_style.italic:
            styled = self._theme.italic(styled)
        if self._default_text_style.strikethrough:
            styled = self._theme.strikethrough(styled)
        if self._default_text_style.underline:
            styled = self._theme.underline(styled)

        sentinel_index = styled.find(SENTINEL)
        self._default_style_prefix = styled[:sentinel_index] if sentinel_index >= 0 else ""
        return self._default_style_prefix

    def _get_style_prefix(self, style_fn: Callable[[str], str]) -> str:
        styled = style_fn(SENTINEL)
        sentinel_index = styled.find(SENTINEL)
        return styled[:sentinel_index] if sentinel_index >= 0 else ""

    def _get_default_inline_style_context(self) -> _InlineStyleContext:
        return _InlineStyleContext(
            apply_text=self._apply_default_style,
            style_prefix=self._get_default_style_prefix(),
        )

    def _render_token(  # noqa: C901, PLR0912, PLR0915 - 1:1 with the TS token switch
        self,
        token: Token,
        width: int,
        next_token_type: str | None = None,
        style_context: _InlineStyleContext | None = None,
    ) -> list[str]:
        lines: list[str] = []
        token_type = token["type"]

        if token_type == "heading":
            heading_level = token["depth"]
            heading_prefix = f"{'#' * heading_level} "

            # Build a heading-specific style context so inline tokens (codespan,
            # bold, etc.) restore heading styling after their own ANSI resets
            # instead of falling back to the default text style.
            if heading_level == 1:

                def heading_style_fn(text: str) -> str:
                    return self._theme.heading(self._theme.bold(self._theme.underline(text)))
            else:

                def heading_style_fn(text: str) -> str:
                    return self._theme.heading(self._theme.bold(text))

            heading_style_context = _InlineStyleContext(
                apply_text=heading_style_fn,
                style_prefix=self._get_style_prefix(heading_style_fn),
            )

            heading_text = self._render_inline_tokens(
                token.get("tokens") or [], heading_style_context
            )
            styled_heading = (
                heading_style_fn(heading_prefix) + heading_text
                if heading_level >= 3
                else heading_text
            )
            lines.append(styled_heading)
            if next_token_type and next_token_type != "space":
                lines.append("")  # Add spacing after headings (unless space token follows)

        elif token_type == "paragraph":
            paragraph_text = self._render_inline_tokens(token.get("tokens") or [], style_context)
            lines.append(paragraph_text)
            # Don't add spacing if next token is space or list
            if next_token_type and next_token_type not in ("list", "space"):
                lines.append("")

        elif token_type == "text":
            lines.append(self._render_inline_tokens([token], style_context))

        elif token_type == "code":
            indent = (
                self._theme.code_block_indent if self._theme.code_block_indent is not None else "  "
            )
            lines.append(self._theme.code_block_border(f"```{token.get('lang') or ''}"))
            if self._theme.highlight_code:
                highlighted_lines = self._theme.highlight_code(token["text"], token.get("lang"))
                for hl_line in highlighted_lines:
                    lines.append(f"{indent}{hl_line}")
            else:
                # Split code by newlines and style each line
                for code_line in token["text"].split("\n"):
                    lines.append(f"{indent}{self._theme.code_block(code_line)}")
            lines.append(self._theme.code_block_border("```"))
            if next_token_type and next_token_type != "space":
                lines.append("")  # Add spacing after code blocks (unless space token follows)

        elif token_type == "list":
            # Don't add spacing after lists if a space token follows
            # (the space token will handle it)
            lines.extend(self._render_list(token, 0, width, style_context))

        elif token_type == "table":
            lines.extend(self._render_table(token, width, next_token_type, style_context))

        elif token_type == "blockquote":

            def quote_style(text: str) -> str:
                return self._theme.quote(self._theme.italic(text))

            quote_style_prefix = self._get_style_prefix(quote_style)

            def apply_quote_style(line: str) -> str:
                if not quote_style_prefix:
                    return quote_style(line)
                line_with_reapplied_style = line.replace("\x1b[0m", f"\x1b[0m{quote_style_prefix}")
                return quote_style(line_with_reapplied_style)

            # Calculate available width for quote content (subtract border "│ " = 2 chars)
            quote_content_width = max(1, width - 2)

            # Blockquotes contain block-level tokens (paragraph, list, code, etc.),
            # so render children with _render_token() instead of
            # _render_inline_tokens(). Default message style should not apply
            # inside blockquotes.
            quote_inline_style_context = _InlineStyleContext(
                apply_text=lambda text: text,
                style_prefix=quote_style_prefix,
            )
            quote_tokens: list[Token] = token.get("tokens") or []
            rendered_quote_lines: list[str] = []
            for i, quote_token in enumerate(quote_tokens):
                next_quote_token = quote_tokens[i + 1] if i + 1 < len(quote_tokens) else None
                rendered_quote_lines.extend(
                    self._render_token(
                        quote_token,
                        quote_content_width,
                        next_quote_token["type"] if next_quote_token else None,
                        quote_inline_style_context,
                    )
                )

            # Avoid rendering an extra empty quote line before the outer
            # blockquote spacing.
            while rendered_quote_lines and rendered_quote_lines[-1] == "":
                rendered_quote_lines.pop()

            for quote_line in rendered_quote_lines:
                styled_line = apply_quote_style(quote_line)
                for wrapped_line in wrap_text_with_ansi(styled_line, quote_content_width):
                    lines.append(self._theme.quote_border("│ ") + wrapped_line)
            if next_token_type and next_token_type != "space":
                lines.append("")  # Add spacing after blockquotes (unless space token follows)

        elif token_type == "hr":
            lines.append(self._theme.hr("─" * min(width, 80)))
            if next_token_type and next_token_type != "space":
                lines.append("")  # Add spacing after horizontal rules (unless space token follows)

        elif token_type == "html":
            # Render HTML as plain text (escaped for terminal)
            raw = token.get("raw")
            if isinstance(raw, str):
                lines.append(self._apply_default_style(raw.strip()))

        elif token_type == "space":
            # Space tokens represent blank lines in markdown
            lines.append("")

        else:
            # Handle any other token types as plain text
            text = token.get("text")
            if isinstance(text, str):
                lines.append(text)

        return lines

    def _render_inline_tokens(  # noqa: C901, PLR0912 - 1:1 with the TS token switch
        self,
        tokens: list[Token],
        style_context: _InlineStyleContext | None = None,
    ) -> str:
        result = ""
        resolved_style_context = style_context or self._get_default_inline_style_context()
        apply_text = resolved_style_context.apply_text
        style_prefix = resolved_style_context.style_prefix

        def apply_text_with_newlines(text: str) -> str:
            return "\n".join(apply_text(segment) for segment in text.split("\n"))

        for token in tokens:
            token_type = token["type"]

            if token_type == "text":
                # Text tokens in list items can have nested tokens for inline formatting
                if token.get("tokens"):
                    result += self._render_inline_tokens(token["tokens"], resolved_style_context)
                else:
                    result += apply_text_with_newlines(token["text"])

            elif token_type == "paragraph":
                # Paragraph tokens contain nested inline tokens
                result += self._render_inline_tokens(
                    token.get("tokens") or [], resolved_style_context
                )

            elif token_type == "strong":
                bold_content = self._render_inline_tokens(
                    token.get("tokens") or [], resolved_style_context
                )
                result += self._theme.bold(bold_content) + style_prefix

            elif token_type == "em":
                italic_content = self._render_inline_tokens(
                    token.get("tokens") or [], resolved_style_context
                )
                result += self._theme.italic(italic_content) + style_prefix

            elif token_type == "codespan":
                result += self._theme.code(token["text"]) + style_prefix

            elif token_type == "link":
                link_text = self._render_inline_tokens(
                    token.get("tokens") or [], resolved_style_context
                )
                styled_link = self._theme.link(self._theme.underline(link_text))
                if _hyperlinks_supported():
                    # OSC 8: render as a clickable hyperlink. The URL is not
                    # printed inline, so we always show only the link text
                    # regardless of whether it matches href.
                    result += _hyperlink(styled_link, token["href"]) + style_prefix
                else:
                    # Fallback: print URL in parentheses when text differs from
                    # href. Compare raw token.text (not styled) against href for
                    # the equality check. For mailto: links strip the prefix
                    # (autolinked emails use text="foo@bar.com" but
                    # href="mailto:foo@bar.com").
                    href = token["href"]
                    href_for_comparison = href[7:] if href.startswith("mailto:") else href
                    if token["text"] in (href, href_for_comparison):
                        result += styled_link + style_prefix
                    else:
                        result += styled_link + self._theme.link_url(f" ({href})") + style_prefix

            elif token_type == "br":
                result += "\n"

            elif token_type == "del":
                del_content = self._render_inline_tokens(
                    token.get("tokens") or [], resolved_style_context
                )
                result += self._theme.strikethrough(del_content) + style_prefix

            elif token_type == "html":
                # Render inline HTML as plain text
                raw = token.get("raw")
                if isinstance(raw, str):
                    result += apply_text_with_newlines(raw)

            else:
                # Handle any other inline token types as plain text
                text = token.get("text")
                if isinstance(text, str):
                    result += apply_text_with_newlines(text)

        while style_prefix and result.endswith(style_prefix):
            result = result[: -len(style_prefix)]

        return result

    def _render_list(
        self,
        token: Token,
        depth: int,
        width: int,
        style_context: _InlineStyleContext | None = None,
    ) -> list[str]:
        """Render a list with proper nesting support."""
        lines: list[str] = []
        indent = "    " * depth
        # Use the list's start property (defaults to 1 for ordered lists)
        start = token.get("start")
        start_number = start if isinstance(start, int) else 1

        for i, item in enumerate(token["items"]):
            bullet = f"{start_number + i}. " if token["ordered"] else "- "
            first_prefix = indent + self._theme.list_bullet(bullet)
            continuation_prefix = indent + " " * visible_width(bullet)
            item_width = max(1, width - visible_width(first_prefix))
            rendered_any_line = False

            for item_token in item["tokens"]:
                if item_token["type"] == "list":
                    lines.extend(self._render_list(item_token, depth + 1, width, style_context))
                    rendered_any_line = True
                    continue

                item_lines = self._render_token(item_token, item_width, None, style_context)
                for line in item_lines:
                    for wrapped_line in wrap_text_with_ansi(line, item_width):
                        line_prefix = continuation_prefix if rendered_any_line else first_prefix
                        lines.append(line_prefix + wrapped_line)
                        rendered_any_line = True

            if not rendered_any_line:
                lines.append(first_prefix)

        return lines

    def _get_longest_word_width(self, text: str, max_width: int | None = None) -> int:
        """Get the visible width of the longest word in a string."""
        longest = 0
        for word in text.split():
            longest = max(longest, visible_width(word))
        if max_width is None:
            return longest
        return min(longest, max_width)

    def _wrap_cell_text(self, text: str, max_width: int) -> list[str]:
        """Wrap a table cell to fit into a column.

        Delegates to wrap_text_with_ansi() so ANSI codes + long tokens are
        handled consistently with the rest of the renderer.
        """
        return wrap_text_with_ansi(text, max(1, max_width))

    def _render_table(  # noqa: C901, PLR0912, PLR0915 - 1:1 with the TS layout maths
        self,
        token: Token,
        available_width: int,
        next_token_type: str | None = None,
        style_context: _InlineStyleContext | None = None,
    ) -> list[str]:
        """Render a table with width-aware cell wrapping.

        Cells that don't fit are wrapped to multiple lines.
        """
        lines: list[str] = []
        header: list[Token] = token["header"]
        rows: list[list[Token]] = token["rows"]
        num_cols = len(header)

        if num_cols == 0:
            return lines

        # Calculate border overhead: "│ " + (n-1) * " │ " + " │"
        # = 2 + (n-1) * 3 + 2 = 3n + 1
        border_overhead = 3 * num_cols + 1
        available_for_cells = available_width - border_overhead
        if available_for_cells < num_cols:
            # Too narrow to render a stable table. Fall back to raw markdown.
            raw = token.get("raw")
            fallback_lines = wrap_text_with_ansi(raw, available_width) if raw else []
            if next_token_type and next_token_type != "space":
                fallback_lines.append("")
            return fallback_lines

        # Calculate natural column widths (what each column needs without constraints)
        natural_widths: list[int] = []
        min_word_widths: list[int] = []
        for cell in header:
            header_text = self._render_inline_tokens(cell.get("tokens") or [], style_context)
            natural_widths.append(visible_width(header_text))
            min_word_widths.append(
                max(1, self._get_longest_word_width(header_text, MAX_UNBROKEN_WORD_WIDTH))
            )
        for row in rows:
            for i, cell in enumerate(row):
                cell_text = self._render_inline_tokens(cell.get("tokens") or [], style_context)
                if i >= len(natural_widths):
                    natural_widths.append(0)
                    min_word_widths.append(1)
                natural_widths[i] = max(natural_widths[i], visible_width(cell_text))
                min_word_widths[i] = max(
                    min_word_widths[i],
                    self._get_longest_word_width(cell_text, MAX_UNBROKEN_WORD_WIDTH),
                )

        min_column_widths = min_word_widths
        min_cells_width = sum(min_column_widths)

        if min_cells_width > available_for_cells:
            min_column_widths = [1] * num_cols
            remaining = available_for_cells - num_cols

            if remaining > 0:
                total_weight = sum(max(0, width - 1) for width in min_word_widths)
                growth = [
                    int((max(0, width - 1) / total_weight) * remaining) if total_weight > 0 else 0
                    for width in min_word_widths
                ]

                for i in range(num_cols):
                    min_column_widths[i] += growth[i] if i < len(growth) else 0

                leftover = remaining - sum(growth)
                i = 0
                while leftover > 0 and i < num_cols:
                    min_column_widths[i] += 1
                    leftover -= 1
                    i += 1

            min_cells_width = sum(min_column_widths)

        # Calculate column widths that fit within available width
        total_natural_width = sum(natural_widths) + border_overhead
        column_widths: list[int]

        if total_natural_width <= available_width:
            # Everything fits naturally
            column_widths = [
                max(width, min_column_widths[index]) for index, width in enumerate(natural_widths)
            ]
        else:
            # Need to shrink columns to fit
            total_grow_potential = sum(
                max(0, width - min_column_widths[index])
                for index, width in enumerate(natural_widths)
            )
            extra_width = max(0, available_for_cells - min_cells_width)
            column_widths = []
            for index, min_width in enumerate(min_column_widths):
                natural_width = natural_widths[index]
                min_width_delta = max(0, natural_width - min_width)
                grow = 0
                if total_grow_potential > 0:
                    grow = int((min_width_delta / total_grow_potential) * extra_width)
                column_widths.append(min_width + grow)

            # Adjust for rounding errors - distribute remaining space
            remaining = available_for_cells - sum(column_widths)
            while remaining > 0:
                grew = False
                for i in range(num_cols):
                    if remaining <= 0:
                        break
                    if column_widths[i] < natural_widths[i]:
                        column_widths[i] += 1
                        remaining -= 1
                        grew = True
                if not grew:
                    break

        # Render top border
        top_border_cells = ["─" * w for w in column_widths]
        lines.append(f"┌─{'─┬─'.join(top_border_cells)}─┐")

        # Render header with wrapping
        header_cell_lines = [
            self._wrap_cell_text(
                self._render_inline_tokens(cell.get("tokens") or [], style_context),
                column_widths[i],
            )
            for i, cell in enumerate(header)
        ]
        header_line_count = max(len(c) for c in header_cell_lines)

        for line_idx in range(header_line_count):
            row_parts: list[str] = []
            for col_idx, cell_lines in enumerate(header_cell_lines):
                text = cell_lines[line_idx] if line_idx < len(cell_lines) else ""
                padded = text + " " * max(0, column_widths[col_idx] - visible_width(text))
                row_parts.append(self._theme.bold(padded))
            lines.append(f"│ {' │ '.join(row_parts)} │")

        # Render separator
        separator_cells = ["─" * w for w in column_widths]
        separator_line = f"├─{'─┼─'.join(separator_cells)}─┤"
        lines.append(separator_line)

        # Render rows with wrapping
        for row_index, row in enumerate(rows):
            row_cell_lines = [
                self._wrap_cell_text(
                    self._render_inline_tokens(cell.get("tokens") or [], style_context),
                    column_widths[i],
                )
                for i, cell in enumerate(row)
            ]
            row_line_count = max(len(c) for c in row_cell_lines)

            for line_idx in range(row_line_count):
                row_parts = []
                for col_idx, cell_lines in enumerate(row_cell_lines):
                    text = cell_lines[line_idx] if line_idx < len(cell_lines) else ""
                    row_parts.append(
                        text + " " * max(0, column_widths[col_idx] - visible_width(text))
                    )
                lines.append(f"│ {' │ '.join(row_parts)} │")

            if row_index < len(rows) - 1:
                lines.append(separator_line)

        # Render bottom border
        bottom_border_cells = ["─" * w for w in column_widths]
        lines.append(f"└─{'─┴─'.join(bottom_border_cells)}─┘")

        if next_token_type and next_token_type != "space":
            lines.append("")  # Add spacing after table
        return lines

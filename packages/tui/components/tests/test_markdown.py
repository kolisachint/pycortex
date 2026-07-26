"""Markdown component tests, ported from hoocode's `test/markdown.test.ts`.

The screen-level contract lives in the parity corpus
(`component/markdown-*` in `packages/tui/testkit/goldens/scenarios.json`), which
diffs pycortex against frames captured from the real TS. These are the TS unit
tests that assert on structure rather than on a frame, plus the cache and
capability behaviour that has no TS counterpart.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from cortex.tui.components import DefaultTextStyle, Markdown, MarkdownTheme
from cortex.tui.images import (
    TerminalCapabilities,
    reset_capabilities_cache,
    set_capabilities,
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _no_hyperlink_capability() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """`markdown.ts` reads a process-global capability, so pin it per test.

    Without this the suite would render links differently depending on which
    terminal it runs in — and on whichever test last called `set_capabilities`.
    """
    set_capabilities(TerminalCapabilities(images=None, true_color=True, hyperlinks=False))
    yield
    reset_capabilities_cache()


def strip_ansi(line: str) -> str:
    return ANSI_RE.sub("", line)


def plain(lines: list[str]) -> list[str]:
    return [strip_ansi(line).rstrip() for line in lines]


def _chalk(*codes: str) -> Callable[[str], str]:
    """A chalk-style wrapper: `chalk.bold.cyan` is `_chalk("1;22", "36;39")`."""
    opens = "".join(f"\x1b[{code.split(';')[0]}m" for code in codes)
    closes = "".join(f"\x1b[{code.split(';')[1]}m" for code in reversed(codes))
    return lambda text: f"{opens}{text}{closes}"


def default_theme(**overrides: Any) -> MarkdownTheme:
    """`test/test-themes.ts`'s `defaultMarkdownTheme`, at chalk level 3."""
    theme = MarkdownTheme(
        heading=_chalk("1;22", "36;39"),
        link=_chalk("34;39"),
        link_url=_chalk("2;22"),
        code=_chalk("33;39"),
        code_block=_chalk("32;39"),
        code_block_border=_chalk("2;22"),
        quote=_chalk("3;23"),
        quote_border=_chalk("2;22"),
        hr=_chalk("2;22"),
        list_bullet=_chalk("36;39"),
        bold=_chalk("1;22"),
        italic=_chalk("3;23"),
        strikethrough=_chalk("9;29"),
        underline=_chalk("4;24"),
    )
    for key, value in overrides.items():
        setattr(theme, key, value)
    return theme


def md(text: str, padding_x: int = 0, padding_y: int = 0, **kwargs: Any) -> Markdown:
    return Markdown(text, padding_x, padding_y, default_theme(), kwargs.get("default_text_style"))


# --- Lists ------------------------------------------------------------------


def test_renders_simple_nested_list() -> None:
    lines = plain(md("- Item 1\n  - Nested 1.1\n  - Nested 1.2\n- Item 2").render(80))
    assert any("- Item 1" in line for line in lines)
    assert any("    - Nested 1.1" in line for line in lines)
    assert any("    - Nested 1.2" in line for line in lines)
    assert any("- Item 2" in line for line in lines)


def test_renders_deeply_nested_list() -> None:
    lines = plain(md("- Level 1\n  - Level 2\n    - Level 3\n      - Level 4").render(80))
    assert any("- Level 1" in line for line in lines)
    assert any("    - Level 2" in line for line in lines)
    assert any("        - Level 3" in line for line in lines)
    assert any("            - Level 4" in line for line in lines)


def test_renders_ordered_nested_list() -> None:
    source = "1. First\n   1. Nested first\n   2. Nested second\n2. Second"
    lines = plain(md(source).render(80))
    assert any("1. First" in line for line in lines)
    assert any("    1. Nested first" in line for line in lines)
    assert any("    2. Nested second" in line for line in lines)
    assert any("2. Second" in line for line in lines)


def test_maintains_numbering_when_code_blocks_are_not_indented() -> None:
    """LLM output: unindented code splits one list into three, `start` re-joins them."""
    source = (
        "1. First item\n\n```typescript\n// code block\n```\n\n"
        "2. Second item\n\n```typescript\n// another code block\n```\n\n3. Third item"
    )
    numbered = [
        line.strip() for line in plain(md(source).render(80)) if re.match(r"^\d+\.", line.strip())
    ]
    assert len(numbered) == 3, numbered
    assert numbered[0].startswith("1.")
    assert numbered[1].startswith("2.")
    assert numbered[2].startswith("3.")


def test_indents_wrapped_unordered_list_lines() -> None:
    assert plain(md("- alpha beta gamma delta epsilon").render(20)) == [
        "- alpha beta gamma",
        "  delta epsilon",
    ]


def test_indents_wrapped_ordered_list_lines() -> None:
    assert plain(md("1. alpha beta gamma delta epsilon").render(20)) == [
        "1. alpha beta gamma",
        "   delta epsilon",
    ]


def test_indents_wrapped_ordered_list_lines_with_multi_digit_markers() -> None:
    assert plain(md("10. alpha beta gamma delta epsilon").render(21)) == [
        "10. alpha beta gamma",
        "    delta epsilon",
    ]


def test_indents_wrapped_nested_list_lines() -> None:
    assert plain(md("- parent\n  - alpha beta gamma delta epsilon").render(24)) == [
        "- parent",
        "    - alpha beta gamma",
        "      delta epsilon",
    ]


def test_indents_wrapped_nested_list_lines_under_ordered_parents() -> None:
    assert plain(md("1. parent\n   - alpha beta gamma delta epsilon").render(24)) == [
        "1. parent",
        "    - alpha beta gamma",
        "      delta epsilon",
    ]


def test_renders_and_wraps_blockquotes_inside_list_items() -> None:
    assert plain(md("- > alpha beta gamma delta epsilon zeta").render(24)) == [
        "- │ alpha beta gamma",
        "  │ delta epsilon zeta",
    ]


def test_renders_and_wraps_code_blocks_inside_list_items() -> None:
    source = "- ```ts\n  alpha beta gamma delta epsilon zeta\n  ```"
    assert plain(md(source).render(24)) == [
        "- ```ts",
        "    alpha beta gamma",
        "  delta epsilon zeta",
        "  ```",
    ]


# --- Tables -----------------------------------------------------------------


def test_extremely_narrow_table_width_is_handled_gracefully() -> None:
    source = "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |"
    lines = md(source).render(15)
    assert lines
    for line in plain(lines):
        assert len(line) <= 15, line


def test_renders_table_when_it_fits_naturally() -> None:
    lines = plain(md("| A | B |\n| --- | --- |\n| 1 | 2 |").render(80))
    header = next((line for line in lines if "A" in line and "B" in line), None)
    assert header is not None
    assert "│" in header
    assert any("├" in line and "┼" in line for line in lines)
    assert any("1" in line and "2" in line for line in lines)


def test_padding_x_is_respected_when_calculating_table_width() -> None:
    source = "| Column One | Column Two |\n| --- | --- |\n| Data 1 | Data 2 |"
    lines = plain(Markdown(source, 2, 0, default_theme()).render(40))
    for line in lines:
        assert len(line) <= 40, line
    table_row = next(line for line in lines if "│" in line)
    assert table_row.startswith("  ")


def test_no_trailing_blank_line_when_table_is_the_last_block() -> None:
    lines = plain(md("| Name |\n| --- |\n| Alice |").render(80))
    assert lines[-1] != ""


# --- Pre-styled text (thinking traces) --------------------------------------


def thinking_style() -> DefaultTextStyle:
    return DefaultTextStyle(color=_chalk("90;39"), italic=True)


def test_preserves_gray_italic_styling_after_inline_code() -> None:
    component = Markdown(
        "This is thinking with `inline code` and more text after",
        1,
        0,
        default_theme(),
        thinking_style(),
    )
    output = "\n".join(component.render(80))
    assert "inline code" in output
    assert "\x1b[90m" in output
    assert "\x1b[3m" in output
    assert "\x1b[33m" in output


def test_preserves_gray_italic_styling_after_bold_text() -> None:
    component = Markdown(
        "This is thinking with **bold text** and more after",
        1,
        0,
        default_theme(),
        thinking_style(),
    )
    output = "\n".join(component.render(80))
    assert "bold text" in output
    assert "\x1b[90m" in output
    assert "\x1b[3m" in output
    assert "\x1b[1m" in output


def test_default_style_does_not_leak_past_the_last_token() -> None:
    """The TS drives this through a TUI and reads the cell after the component.

    Here the same guarantee is checked at the source: the line must not end with
    a dangling style prefix, or the padding (and every line after it) inherits it.
    """
    component = Markdown(
        "This is thinking with `inline code`", 1, 0, default_theme(), thinking_style()
    )
    for line in component.render(80):
        assert not line.endswith("\x1b[3m")
        assert not line.endswith("\x1b[90m")


# --- Spacing ----------------------------------------------------------------


def blank_lines_after(lines: list[str], index: int) -> int:
    after = lines[index + 1 :]
    return next((i for i, line in enumerate(after) if line != ""), len(after))


def test_one_blank_line_between_code_block_and_following_paragraph() -> None:
    source = 'hello world\n\n```js\nconst hello = "world";\n```\n\nagain, hello world'
    lines = plain(md(source).render(80))
    assert blank_lines_after(lines, lines.index("```")) == 1


@pytest.mark.parametrize(
    "source",
    [
        "hello this is text\n```\ncode block\n```\nmore text",
        "hello this is text\n\n```\ncode block\n```\n\nmore text",
    ],
)
def test_paragraph_and_code_block_spacing_normalizes_to_one_blank_line(source: str) -> None:
    assert plain(md(source).render(80)) == [
        "hello this is text",
        "",
        "```",
        "  code block",
        "```",
        "",
        "more text",
    ]


@pytest.mark.parametrize(
    "source",
    [
        "```js\nconst hello = 'world';\n```",
        "hello world\n\n```js\nconst hello = 'world';\n```",
    ],
)
def test_no_trailing_blank_line_when_code_block_is_the_last_block(source: str) -> None:
    assert plain(md(source).render(80))[-1] != ""


def test_one_blank_line_between_divider_and_following_paragraph() -> None:
    lines = plain(md("hello world\n\n---\n\nagain, hello world").render(80))
    divider = next(i for i, line in enumerate(lines) if "─" in line)
    assert blank_lines_after(lines, divider) == 1


def test_no_trailing_blank_line_when_divider_is_the_last_block() -> None:
    assert plain(md("---").render(80))[-1] != ""


def test_one_blank_line_between_heading_and_following_paragraph() -> None:
    lines = plain(md("# Hello\n\nThis is a paragraph").render(80))
    assert blank_lines_after(lines, 0) == 1


def test_no_trailing_blank_line_when_heading_is_the_last_block() -> None:
    assert plain(md("some text\n\n# Hello").render(80))[-1] != ""


def test_one_blank_line_between_blockquote_and_following_paragraph() -> None:
    lines = plain(md("hello world\n\n> quoted\n\nagain, hello world").render(80))
    quote = next(i for i, line in enumerate(lines) if line.startswith("│ "))
    assert blank_lines_after(lines, quote) == 1


def test_no_trailing_blank_line_when_blockquote_is_the_last_block() -> None:
    assert plain(md("hello world\n\n> quoted").render(80))[-1] != ""


# --- Blockquotes ------------------------------------------------------------


def test_lazy_continuation_blockquote_is_styled_consistently() -> None:
    component = Markdown(
        ">Foo\nbar", 0, 0, default_theme(), DefaultTextStyle(color=_chalk("35;39"))
    )
    lines = component.render(80)
    assert sum(1 for line in plain(lines) if line.startswith("│ ")) == 2
    foo = next(line for line in lines if "Foo" in line)
    bar = next(line for line in lines if "bar" in line)
    assert "\x1b[3m" in foo
    assert "\x1b[3m" in bar
    # The default message colour must not reach inside a blockquote.
    assert "\x1b[35m" not in foo
    assert "\x1b[35m" not in bar


def test_explicit_multiline_blockquote_is_styled_consistently() -> None:
    component = Markdown(
        ">Foo\n>bar", 0, 0, default_theme(), DefaultTextStyle(color=_chalk("36;39"))
    )
    lines = component.render(80)
    assert sum(1 for line in plain(lines) if line.startswith("│ ")) == 2
    assert all("\x1b[36m" not in line for line in lines)


def test_renders_list_content_inside_blockquotes() -> None:
    lines = plain(md("> 1. bla bla\n> - nested bullet").render(80))
    quoted = [line for line in lines if line.startswith("│ ")]
    assert any("1. bla bla" in line for line in quoted)
    assert any("- nested bullet" in line for line in quoted)


def test_long_blockquote_lines_wrap_with_a_border_on_each_line() -> None:
    long_text = (
        "This is a very long blockquote line that should wrap to multiple lines when rendered"
    )
    content = [line for line in plain(md(f"> {long_text}").render(30)) if line]
    assert len(content) > 1
    for line in content:
        assert line.startswith("│ ")
    joined = " ".join(content)
    assert "very long" in joined
    assert "blockquote" in joined
    assert "multiple" in joined


def test_inline_formatting_inside_blockquote_reapplies_quote_styling() -> None:
    lines = md("> Quote with **bold** and `code`").render(80)
    assert any(line.startswith("│ ") for line in plain(lines))
    output = "\n".join(lines)
    assert "\x1b[1m" in output
    assert "\x1b[33m" in output
    assert "\x1b[3m" in output


# --- Headings ---------------------------------------------------------------


def test_heading_styling_is_restored_after_inline_code() -> None:
    output = "\n".join(md("### Why `sourceInfo` should not be optional").render(80))
    code_end = output.index("\x1b[39m", output.index("sourceInfo"))
    assert "\x1b[36m" in output[code_end:], output


def test_h1_styling_is_restored_after_inline_code() -> None:
    output = "\n".join(md("# Why `sourceInfo` matters").render(80))
    code_end = output.index("\x1b[39m", output.index("sourceInfo"))
    assert "\x1b[4m" in output[code_end:], output


def test_h1_underline_does_not_leak_into_padding() -> None:
    """`# Head \\`code\\`` ends on an inline token; the heading prefix must not trail."""
    for line in Markdown("# Head `code`", 2, 1, default_theme()).render(20):
        assert not line.endswith("\x1b[4m")


def test_heading_prefix_is_shown_from_level_three() -> None:
    assert plain(md("## Two").render(80))[0] == "Two"
    assert plain(md("### Three").render(80))[0] == "### Three"


# --- Strikethrough ----------------------------------------------------------


def test_double_tilde_renders_as_strikethrough() -> None:
    lines = md("Use ~~strikethrough~~ here").render(80)
    output = "\n".join(lines)
    joined_plain = " ".join(plain(lines))
    assert "\x1b[9m" in output
    assert "strikethrough" in joined_plain
    assert "~~strikethrough~~" not in joined_plain


def test_single_tilde_stays_plain_text() -> None:
    lines = md("Use ~strikethrough~ literally").render(80)
    assert "~strikethrough~" in " ".join(plain(lines))
    assert "\x1b[9m" not in "\n".join(lines)


# --- Links ------------------------------------------------------------------


def test_autolinked_email_is_not_duplicated() -> None:
    joined = " ".join(plain(md("Contact user@example.com for help").render(80)))
    assert "user@example.com" in joined
    assert "mailto:" not in joined


def test_bare_url_is_not_duplicated() -> None:
    joined = " ".join(plain(md("Visit https://example.com for more").render(80)))
    assert joined.count("https://example.com") == 1


def test_url_is_shown_in_parentheses_without_hyperlink_support() -> None:
    joined = " ".join(plain(md("[click here](https://example.com)").render(80)))
    assert "click here" in joined
    assert "(https://example.com)" in joined


def test_mailto_url_is_shown_in_parentheses_without_hyperlink_support() -> None:
    joined = " ".join(plain(md("[Email me](mailto:test@example.com)").render(80)))
    assert "Email me" in joined
    assert "(mailto:test@example.com)" in joined


@pytest.fixture
def hyperlinks_supported() -> None:
    """Report the terminal as OSC 8 capable, as `terminal-image.ts` would.

    The screen this branch produces is pinned against the real TS by
    `component/markdown-link-osc8`, which now runs; these assert on the
    structure of the sequence rather than on the frame.
    """
    set_capabilities(TerminalCapabilities(images=None, true_color=True, hyperlinks=True))


@pytest.mark.usefixtures("hyperlinks_supported")
def test_emits_osc8_when_the_terminal_supports_hyperlinks() -> None:
    lines = md("[click here](https://example.com)").render(80)
    joined = "".join(lines)
    assert "\x1b]8;;https://example.com\x1b\\" in joined
    assert "\x1b]8;;\x1b\\" in joined
    visible = ANSI_RE.sub("", re.sub(r"\x1b\]8;;[^\x1b]*\x1b\\\\?", "", joined))
    assert "click here" in visible
    assert "(https://example.com)" not in visible


@pytest.mark.usefixtures("hyperlinks_supported")
def test_uses_osc8_for_mailto_links() -> None:
    joined = "".join(md("[Email me](mailto:test@example.com)").render(80))
    assert "\x1b]8;;mailto:test@example.com\x1b\\" in joined
    assert "\x1b]8;;\x1b\\" in joined


@pytest.mark.usefixtures("hyperlinks_supported")
def test_uses_osc8_for_bare_urls() -> None:
    joined = "".join(md("Visit https://example.com for more").render(80))
    assert "\x1b]8;;https://example.com\x1b\\" in joined
    visible = ANSI_RE.sub("", re.sub(r"\x1b\]8;;[^\x1b]*\x1b\\\\?", "", joined))
    assert "(https://example.com)" not in visible


def test_the_capability_is_read_per_render_not_cached_by_the_component() -> None:
    """`getCapabilities()` is called inside the link branch, every render.

    A component that resolved it once at construction would keep printing
    `text (url)` on a terminal that had since been identified.
    """
    component = md("[click here](https://example.com)")
    assert "(https://example.com)" in " ".join(plain(component.render(80)))
    set_capabilities(TerminalCapabilities(images=None, true_color=True, hyperlinks=True))
    component.invalidate()
    assert "\x1b]8;;https://example.com\x1b\\" in "".join(component.render(80))


# --- HTML -------------------------------------------------------------------


def test_html_like_tags_stay_visible() -> None:
    source = "This is text with <thinking>hidden content</thinking> that should be visible"
    joined = " ".join(plain(md(source).render(80)))
    assert "hidden content" in joined or "<thinking>" in joined


def test_html_inside_code_blocks_is_rendered() -> None:
    joined = "\n".join(plain(md("```html\n<div>Some HTML</div>\n```").render(80)))
    assert "<div>" in joined
    assert "</div>" in joined


# --- Caching and lifecycle (no TS counterpart) -------------------------------


def test_render_is_cached_per_width() -> None:
    component = md("# Title\n\nBody")
    first = component.render(40)
    assert component.render(40) is first


def test_line_cache_keeps_the_last_three_widths() -> None:
    component = md("# Title\n\nBody")
    first = component.render(10)
    component.render(20)
    component.render(30)
    component.render(40)  # evicts width 10
    assert component.render(20) is not None
    assert component.render(10) is not first


def test_set_text_drops_the_cache() -> None:
    component = md("first")
    before = component.render(40)
    component.set_text("second")
    after = component.render(40)
    assert after is not before
    assert "second" in " ".join(plain(after))


def test_invalidate_drops_the_cache() -> None:
    component = md("first")
    before = component.render(40)
    component.invalidate()
    assert component.render(40) is not before


def test_empty_text_renders_nothing() -> None:
    assert md("").render(40) == []
    assert md("   \n  \n").render(40) == []


def test_empty_text_is_cached_as_an_empty_frame() -> None:
    """JS truthiness bites here: `[]` is a cache *hit* in the TS, not a miss."""
    component = md("")
    first = component.render(40)
    assert component.render(40) is first


def test_padding_adds_blank_lines_and_margins() -> None:
    lines = Markdown("hello", 2, 1, default_theme()).render(20)
    assert lines[0] == " " * 20
    assert lines[-1] == " " * 20
    assert strip_ansi(lines[1]).startswith("  hello")
    assert len(strip_ansi(lines[1])) == 20

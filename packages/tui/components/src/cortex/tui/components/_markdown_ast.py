"""Markdown lexing that produces `marked`-shaped tokens.

`markdown.ts` is written directly against the token tree from the `marked` npm
package. Python has no `marked`, so this parses with **mistune** and normalises
its AST into the same shape. mistune was chosen by measuring both candidates
against real `marked` output: `markdown-it-py` emits a flat `_open`/`_close`
stream that would have to be reassembled into a tree, while mistune already
nests and carries every field the renderer reads.

The normalisation is verified token-for-token against ASTs captured from the
real `marked` (`packages/tui/testkit/goldens/marked-ast.json`) — see
`tests/test_markdown_ast.py`. Nothing here is inferred from reading the
TypeScript.

Only the fields `markdown.ts` consults are produced. `raw` is carried for
`html` tokens because that branch prints it and for `table` tokens because the
too-narrow fallback reprints the table's markdown source; elsewhere marked's
`raw` and source offsets are internal bookkeeping no renderer branch reads.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

import mistune
from mistune.block_parser import BlockParser
from mistune.plugins.table import parse_nptable, parse_table, table_in_list, table_in_quote

if TYPE_CHECKING:
    from re import Match

    from mistune.core import BlockState

__all__ = ["lex_markdown"]

# markdown.ts installs a StrictStrikethroughTokenizer to reject the loose forms
# stock GFM accepts (`~~ spaced ~~`). mistune's strikethrough plugin already
# rejects them identically — pinned by the `strike-strict-rejects-spaces`
# golden — so no override is needed here.

_BLOCK_TYPES = {
    "block_code": "code",
    "block_quote": "blockquote",
    "thematic_break": "hr",
    "block_html": "html",
    "blank_line": "space",
}

_INLINE_TYPES = {
    "emphasis": "em",
    "strong": "strong",
    "codespan": "codespan",
    "linebreak": "br",
    "softbreak": "text",
    "strikethrough": "del",
    "inline_html": "html",
}

# marked's heading, table and html rules consume their own trailing blank lines,
# so no `space` token follows one. Every other block leaves the blank line
# behind. All three were found by diffing against marked, not by reading it.
_CONSUMES_TRAILING_BLANK = frozenset({"heading", "table", "html"})


# A source ending in a blank line (optionally holding spaces or tabs).
_TRAILING_BLANK_RE = re.compile(r"\n[^\S\n]*\n[^\S\n]*$")

# Set per call in `lex_markdown`: mistune appends a newline to the source it
# parses, marked does not, and the difference shows up in the last block's `raw`.
_ENDS_WITH_NEWLINE = "cortex_source_ends_with_newline"


def _skip_blank_lines(src: str, pos: int) -> int:
    """Advance past the run of blank lines starting at `pos`."""
    while pos < len(src):
        line_end = src.find("\n", pos)
        stop = len(src) if line_end < 0 else line_end + 1
        if src[pos:stop].strip():
            return pos
        pos = stop
    return pos


def _record_raw(parse: Any, token_type: str) -> Any:
    """Wrap a block rule so its token keeps the markdown it was parsed from.

    marked puts the source of every block on `token.raw`, and `markdown.ts`
    reads it for two of them: a table too narrow to draw reprints its own
    markdown, and an html block prints its source. mistune's AST carries no
    source spans, but the rule knows them — `state.cursor` is where the block
    started and the return value is where it ended.

    Two adjustments make it marked's span rather than mistune's, both pinned by
    goldens: marked's table and html rules swallow the blank lines that follow
    the block (which is also why no `space` token comes after one), and mistune
    parses a source it has appended a final newline to, which marked never sees.
    """

    def rule(block: BlockParser, m: Match[str], state: BlockState) -> int | None:
        start = state.cursor
        end = cast("int | None", parse(block, m, state))
        if end is None or not state.tokens or state.tokens[-1].get("type") != token_type:
            return end
        stop = _skip_blank_lines(state.src, end)
        raw = state.src[start:stop]
        if stop >= len(state.src) and not state.env.get(_ENDS_WITH_NEWLINE, True):
            raw = raw.removesuffix("\n")
        state.tokens[-1]["raw"] = raw
        return end

    return rule


_PARSER = mistune.create_markdown(
    renderer=None,
    # `table_in_quote`/`table_in_list` reuse the same rules in nested contexts;
    # marked parses tables there too, so leaving them off would silently render
    # a quoted table as paragraphs.
    plugins=[
        "table",
        table_in_quote,
        table_in_list,
        "strikethrough",
        "task_lists",
        "url",
    ],
)
# Re-register the two table rules through the raw-recording wrapper. `register`
# with `pattern=None` keeps the plugin's own pattern and rule ordering; only the
# parse function changes, and the nested rule lists point at the same one.
_PARSER.block.register("table", None, _record_raw(parse_table, "table"))
_PARSER.block.register("nptable", None, _record_raw(parse_nptable, "table"))
_PARSER.block.register("raw_html", None, _record_raw(BlockParser.parse_raw_html, "block_html"))


def lex_markdown(text: str) -> list[dict[str, Any]]:
    """Lex `text` into `marked`-shaped block tokens."""
    if not text:
        return []
    state = _PARSER.block.state_cls()
    # Shared with every nested state, so a table inside a quote or a list item
    # sees it too.
    state.env[_ENDS_WITH_NEWLINE] = text.endswith("\n")
    # `parse(renderer=None)` always yields the AST, but mistune types the result
    # as `str | list` because a renderer would produce a string.
    ast = cast("list[dict[str, Any]]", _PARSER.parse(text, state)[0])
    tokens = _normalize_spaces([t for t in (_block(n) for n in ast) if t is not None])
    # mistune emits no blank line of its own after a list, so a document that
    # *ends* with one has nothing for `_normalize_spaces` to keep; marked still
    # reports the trailing blank, and the renderer prints it as a blank line.
    if tokens and tokens[-1]["type"] == "list" and _TRAILING_BLANK_RE.search(text):
        tokens.append({"type": "space"})
    return tokens


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    return node.get("children") or []


def _block(node: dict[str, Any]) -> dict[str, Any] | None:  # noqa: C901 - flat type switch
    kind = node.get("type", "")
    mapped = _BLOCK_TYPES.get(kind, kind)

    if mapped == "heading":
        return {
            "type": "heading",
            "depth": node.get("attrs", {}).get("level", 1),
            "tokens": _inlines(_children(node)),
        }

    if mapped == "code":
        info = (node.get("attrs") or {}).get("info") or ""
        # marked reports only the first word of the info string as `lang`, and
        # strips the trailing newline the fence keeps.
        return {
            "type": "code",
            "lang": info.split()[0] if info.split() else "",
            "text": (node.get("raw") or "").rstrip("\n"),
        }

    if mapped == "list":
        attrs = node.get("attrs") or {}
        ordered = bool(attrs.get("ordered"))
        start = attrs.get("start")
        return {
            "type": "list",
            "ordered": ordered,
            # marked reports a number for every ordered list (1 when the list
            # starts at 1) and an empty string for unordered ones.
            "start": (start if start is not None else 1) if ordered else "",
            "items": [_list_item(child) for child in _children(node)],
        }

    if mapped == "table":
        return _table(node)

    if mapped == "blockquote":
        inner = [t for t in (_block(c) for c in _children(node)) if t is not None]
        return {
            "type": "blockquote",
            "tokens": _normalize_spaces(inner, keep_trailing_space=False),
        }

    if mapped == "hr":
        return {"type": "hr"}

    if mapped == "html":
        # Recorded by `_record_raw`; the html branch prints it (trimmed).
        return {"type": "html", "raw": node.get("raw") or ""}

    if mapped == "space":
        return {"type": "space"}

    if mapped in ("paragraph", "block_text"):
        # `block_text` is mistune's tight-list-item paragraph. marked models the
        # same thing as a `text` token that still carries inline children.
        inlines = _inlines(_children(node))
        if mapped == "block_text":
            return {"type": "text", "text": _plain(inlines), "tokens": inlines}
        return {"type": "paragraph", "tokens": inlines}

    return {"type": mapped, "tokens": _inlines(_children(node))}


def _item_child(token: dict[str, Any]) -> dict[str, Any]:
    """A list item's own blocks, as marked models them.

    marked never puts a `paragraph` directly inside a `list_item`: even a loose
    item (blank line between items, or several paragraphs in one item) keeps its
    prose as `text` tokens carrying inline children. mistune produces
    `block_text` for tight items and `paragraph` for loose ones, so the loose
    case is folded in here. It matters: `renderToken` renders `text` through
    `renderInlineTokens` and `paragraph` through a branch that appends a blank
    line, so a loose list would grow spacing the TS does not have.
    """
    if token["type"] != "paragraph":
        return token
    return {"type": "text", "text": _plain(token["tokens"]), "tokens": token["tokens"]}


def _list_item(node: dict[str, Any]) -> dict[str, Any]:
    is_task = node.get("type") == "task_list_item"
    item: dict[str, Any] = {
        "type": "list_item",
        "task": is_task,
        "tokens": [_item_child(t) for t in (_block(c) for c in _children(node)) if t is not None],
    }
    if is_task:
        item["checked"] = bool((node.get("attrs") or {}).get("checked"))
    else:
        # marked omits `checked` entirely on non-task items; the goldens record
        # that as `undefined`, which is absent once serialised.
        item["checked"] = None
    return item


def _table(node: dict[str, Any]) -> dict[str, Any]:
    head_cells: list[dict[str, Any]] = []
    rows: list[list[dict[str, Any]]] = []
    align: list[str | None] = []

    for section in _children(node):
        if section.get("type") == "table_head":
            for cell in _children(section):
                align.append((cell.get("attrs") or {}).get("align"))
                head_cells.append({"tokens": _inlines(_children(cell))})
        elif section.get("type") == "table_body":
            for row in _children(section):
                rows.append([{"tokens": _inlines(_children(cell))} for cell in _children(row)])

    return {
        "type": "table",
        "align": align,
        "header": head_cells,
        "rows": rows,
        # Recorded by `_record_table_raw`; the narrow-table fallback prints it.
        "raw": node.get("raw", ""),
    }


def _inlines(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inline tokens, merged the way marked emits them.

    mistune splits on every soft break and leaves empty text tokens around
    emphasis; marked yields one text token per contiguous run, newlines
    included. Merging here keeps `renderInlineTokens` walking the same list.
    """
    out: list[dict[str, Any]] = []
    for node in nodes:
        token = _inline(node)
        if token is None:
            continue
        if token["type"] == "text":
            if not token["text"]:
                continue
            if out and out[-1]["type"] == "text" and "tokens" not in out[-1]:
                out[-1] = {"type": "text", "text": out[-1]["text"] + token["text"]}
                continue
        out.append(token)
    return out


def _inline(node: dict[str, Any]) -> dict[str, Any] | None:
    kind = node.get("type", "")
    mapped = _INLINE_TYPES.get(kind, kind)

    if kind == "text":
        return {"type": "text", "text": node.get("raw", "")}

    if kind == "softbreak":
        # marked keeps a soft break as a newline inside the surrounding text.
        return {"type": "text", "text": "\n"}

    if mapped == "br":
        return {"type": "br"}

    if mapped == "codespan":
        return {"type": "codespan", "text": node.get("raw", "")}

    if kind == "link":
        children = _inlines(_children(node))
        return {
            "type": "link",
            "href": (node.get("attrs") or {}).get("url", ""),
            "text": _plain(children),
            "tokens": children,
        }

    if mapped == "html":
        return {"type": "html", "raw": node.get("raw", "")}

    if mapped in ("strong", "em", "del"):
        return {"type": mapped, "tokens": _inlines(_children(node))}

    children = _inlines(_children(node))
    if children:
        return {"type": mapped, "tokens": children}
    return {"type": mapped, "text": node.get("raw", "")}


def _plain(tokens: list[dict[str, Any]]) -> str:
    """Flatten inline tokens to their text, as marked's `text` field does."""
    parts: list[str] = []
    for token in tokens:
        if "text" in token:
            parts.append(token["text"])
        elif "tokens" in token:
            parts.append(_plain(token["tokens"]))
    return "".join(parts)


def _normalize_spaces(
    tokens: list[dict[str, Any]], *, keep_trailing_space: bool = True
) -> list[dict[str, Any]]:
    """Make mistune's blank lines line up with marked's `space` tokens.

    `markdown.ts` keys its blank-line spacing off `nextToken.type === "space"`,
    so this is not cosmetic. Three adjustments, each pinned by a golden:

    - collapse a run of blank lines into a single `space`, as marked does;
    - drop the `space` after a heading or table, whose rules eat their own
      trailing blanks;
    - add the `space` after a `list` that mistune omits — `list` is the one
      block `markdown.ts` never lets add its own trailing blank, so without this
      the line simply disappears.

    A document that ends with a blank line ends with a `space` token in both
    lexers, and the renderer prints it as a trailing blank line, so it is kept.
    Inside a blockquote (`keep_trailing_space=False`) mistune emits one where
    marked does not — for `"> para\n>\n"` marked's inner source ends at the
    paragraph — but the blockquote branch pops trailing blank lines before
    drawing borders, so dropping it there keeps the screens identical.
    """
    out: list[dict[str, Any]] = []
    for token in tokens:
        if token["type"] == "space":
            previous = out[-1] if out else None
            if previous is not None and previous["type"] == "space":
                continue
            if previous is not None and previous["type"] in _CONSUMES_TRAILING_BLANK:
                continue
        elif out and out[-1]["type"] == "list":
            out.append({"type": "space"})
        out.append(token)
    if not keep_trailing_space:
        while len(out) > 1 and out[-1]["type"] == "space":
            out.pop()
    return out

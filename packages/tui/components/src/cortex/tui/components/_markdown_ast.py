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
`html` tokens because that branch prints it; elsewhere marked's `raw` and
source offsets are internal bookkeeping no renderer branch reads.
"""

from __future__ import annotations

from typing import Any, cast

import mistune

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

# marked's heading rule consumes its own trailing blank lines, so no `space`
# token follows one. Every other block leaves the blank line behind.
_CONSUMES_TRAILING_BLANK = frozenset({"heading"})


_PARSER = mistune.create_markdown(
    renderer=None,
    plugins=["table", "strikethrough", "task_lists", "url"],
)


def lex_markdown(text: str) -> list[dict[str, Any]]:
    """Lex `text` into `marked`-shaped block tokens."""
    if not text:
        return []
    # `create_markdown(renderer=None)` always yields the AST, but mistune types
    # the call as `str | list` because a renderer would produce a string.
    ast = cast("list[dict[str, Any]]", _PARSER(text))
    return _normalize_spaces([t for t in (_block(n) for n in ast) if t is not None])


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
        return {"type": "blockquote", "tokens": _normalize_spaces(inner)}

    if mapped == "hr":
        return {"type": "hr"}

    if mapped == "html":
        return {"type": "html", "raw": (node.get("raw") or "").rstrip("\n")}

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


def _list_item(node: dict[str, Any]) -> dict[str, Any]:
    is_task = node.get("type") == "task_list_item"
    item: dict[str, Any] = {
        "type": "list_item",
        "task": is_task,
        "tokens": [t for t in (_block(c) for c in _children(node)) if t is not None],
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

    return {"type": "table", "align": align, "header": head_cells, "rows": rows}


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


def _normalize_spaces(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make mistune's blank lines line up with marked's `space` tokens.

    `markdown.ts` keys its blank-line spacing off `nextToken.type === "space"`,
    so this is not cosmetic. Four adjustments, each pinned by a golden:

    - collapse a run of blank lines into a single `space`, as marked does;
    - drop the `space` after a heading, whose rule eats its own trailing blanks;
    - add the `space` after a `list` that mistune omits — `list` is the one
      block `markdown.ts` never lets add its own trailing blank, so without this
      the line simply disappears;
    - drop a trailing `space`, which marked only emits for an all-blank document.
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
    while len(out) > 1 and out[-1]["type"] == "space":
        out.pop()
    return out

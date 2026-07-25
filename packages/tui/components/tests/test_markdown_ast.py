"""Hold the mistune adapter to ASTs captured from the real `marked`.

Same discipline as the surface harness: the authority is the actual upstream
implementation, not a tree I decided looked right. `markdown.ts` branches on
`token.type`, `depth`, `lang`, `ordered`, `checked`, `align` and — critically —
on whether the *next* token is a `space`, so all of those are pinned.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from cortex.tui.components._markdown_ast import lex_markdown

# The testkit owns the goldens; reach them through the installed package so
# this keeps working regardless of where pytest is invoked from.
from cortex.tui.testkit._scene import GOLDENS_DIR as GOLDENS

CORPUS = json.loads((GOLDENS / "markdown-corpus.json").read_text())
MARKED = {s["id"]: s for s in json.loads((GOLDENS / "marked-ast.json").read_text())["scenarios"]}
SAMPLES = [(s["id"], s["source"]) for s in CORPUS["samples"]]


def strip_undefined(node: Any) -> Any:
    """Drop keys marked leaves `undefined` — they vanish through JSON anyway."""
    if isinstance(node, dict):
        return {k: strip_undefined(v) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [strip_undefined(v) for v in node]
    return node


@pytest.mark.parametrize(("sample_id", "source"), SAMPLES, ids=[s[0] for s in SAMPLES])
def test_matches_marked(sample_id: str, source: str) -> None:
    golden = MARKED.get(sample_id)
    assert golden is not None, (
        f"no marked AST for {sample_id} — run `uv run scripts/tui_goldens.py --refresh`"
    )
    # markdown.ts normalises tabs before lexing; the dumper does the same.
    actual = strip_undefined(lex_markdown(source.replace("\t", "   ")))
    expected = strip_undefined(golden["tokens"])
    assert actual == expected, (
        f"{sample_id} diverges from marked\n"
        f"  marked: {json.dumps(expected, ensure_ascii=False)}\n"
        f"  ours:   {json.dumps(actual, ensure_ascii=False)}"
    )


def test_block_type_sequences_match() -> None:
    """A coarser view, so a shape regression names itself before the deep diff."""
    problems: list[str] = []
    for sample_id, source in SAMPLES:
        want = [t["type"] for t in MARKED[sample_id]["tokens"]]
        got = [t["type"] for t in lex_markdown(source.replace("\t", "   "))]
        if want != got:
            problems.append(f"{sample_id}: marked={want} ours={got}")
    assert not problems, "\n".join(problems)


def test_every_corpus_sample_has_a_golden() -> None:
    assert {s[0] for s in SAMPLES} == set(MARKED)

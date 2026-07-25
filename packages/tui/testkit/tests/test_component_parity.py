"""Component parity: pycortex vs. the real hoocode TS, compared as screens.

Each scenario is rendered by both implementations and projected onto the
`Surface` validated in `test_surface_xterm.py`. Equal screens pass; different
screens fail with both screens printed. A scenario pycortex has not ported yet
is skipped **with the plan step that will close it** — never silently.
"""

from __future__ import annotations

from typing import Any

import pytest
from cortex.tui.testkit import Verdict, evaluate_component, load_corpus, load_golden
from cortex.tui.testkit._parity import expected_surface_snapshot
from cortex.tui.util import visible_width

CORPUS = load_corpus()
GOLDENS: dict[str, Any] = {s["id"]: s for s in load_golden("ts-components.json")["scenarios"]}
SCENARIOS = [(s["id"], s) for s in CORPUS.components]


@pytest.mark.parametrize(("scenario_id", "spec"), SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_component_matches_typescript(scenario_id: str, spec: dict[str, Any]) -> None:
    golden = GOLDENS.get(scenario_id)
    assert golden is not None, (
        f"no golden for {scenario_id} — run `uv run scripts/tui_goldens.py --refresh`"
    )

    result = evaluate_component(spec, golden)
    if result.verdict is Verdict.UNPORTED:
        pytest.skip(f"not ported yet — {result.detail}")
    assert result.verdict is Verdict.MATCH, (
        f"{scenario_id} diverges from hoocode TS\n{result.detail}"
    )


def test_every_golden_has_a_scenario() -> None:
    """Goldens and corpus must stay in step, or coverage silently shrinks."""
    corpus_ids = {s["id"] for s in CORPUS.components}
    stale = set(GOLDENS) - corpus_ids
    assert not stale, f"goldens exist for scenarios no longer in the corpus: {sorted(stale)}"


def test_reference_output_is_cache_stable() -> None:
    """The TS side memoizes renders; record that the goldens captured a stable frame."""
    unstable = [s["id"] for s in GOLDENS.values() if not s["stable"]]
    assert not unstable, f"TS render was not stable across two calls for: {unstable}"


@pytest.mark.parametrize(
    ("scenario_id",),
    [(s["id"],) for s in CORPUS.components],
    ids=[s["id"] for s in CORPUS.components],
)
def test_reference_screens_render_square(scenario_id: str) -> None:
    """Guards the snapshot renderer itself — a ragged box means bad diff output.

    Every framed row must be exactly as wide as the border, measured in display
    columns (a wide character is one character and two columns).
    """
    snapshot_text = expected_surface_snapshot(GOLDENS[scenario_id])
    framed = [line for line in snapshot_text.splitlines() if "│" in line]
    border = next(line for line in snapshot_text.splitlines() if "┌" in line)
    expected_width = visible_width(border)
    ragged = [line for line in framed if visible_width(line) != expected_width]
    assert not ragged, f"ragged snapshot for {scenario_id}:\n{snapshot_text}"

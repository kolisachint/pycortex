"""Renderer parity: the screen `cortex.tui.render` produces vs. hoocode's `tui.ts`.

Frames are cumulative — a differential renderer's second write only means
anything applied on top of its first — so each scenario replays every frame into
one long-lived `Surface` per side and compares after each.

Strict since step 1.5 re-ported `tui.ts`: any divergence from the TypeScript is
a failure, not a tracked gap. An `UNPORTED` verdict still skips — that is for
scenarios naming something genuinely absent, which the parity report tracks
against the step that will add it.
"""

from __future__ import annotations

from typing import Any

import pytest
from cortex.tui.testkit import Verdict, evaluate_renderer, load_corpus, load_golden

STRICT = True

CORPUS = load_corpus()
GOLDENS: dict[str, Any] = {s["id"]: s for s in load_golden("ts-renderer.json")["scenarios"]}
SCENARIOS = [(s["id"], s) for s in CORPUS.renderer]


@pytest.mark.parametrize(("scenario_id", "spec"), SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_renderer_matches_typescript(scenario_id: str, spec: dict[str, Any]) -> None:
    golden = GOLDENS.get(scenario_id)
    assert golden is not None, (
        f"no golden for {scenario_id} — run `uv run scripts/tui_goldens.py --refresh`"
    )

    result = evaluate_renderer(spec, golden)
    if result.verdict is Verdict.MATCH:
        return
    if STRICT:
        pytest.fail(f"{scenario_id} diverges from hoocode TS\n{result.detail}")
    pytest.skip(f"blocked by step {result.blocked_by}: {result.detail.splitlines()[0]}")


def test_goldens_cover_the_corpus() -> None:
    corpus_ids = {s["id"] for s in CORPUS.renderer}
    assert corpus_ids == set(GOLDENS), (
        f"corpus/golden mismatch: missing {sorted(corpus_ids - set(GOLDENS))}, "
        f"stale {sorted(set(GOLDENS) - corpus_ids)}"
    )


def test_reference_frames_are_non_empty() -> None:
    """A golden with no frames would make any port look correct."""
    empty = [s["id"] for s in GOLDENS.values() if not s["frames"]]
    assert not empty, f"captured no frames for: {empty}"


def test_first_frame_paints_the_content() -> None:
    """Sanity-check the goldens themselves: frame 0 must put text on screen."""
    from cortex.tui.testkit import Surface

    blank: list[str] = []
    for scenario in GOLDENS.values():
        frame = scenario["frames"][0]
        surface = Surface.render(frame["stream"], cols=frame["cols"], rows=frame["rows"])
        if not surface.text().strip():
            blank.append(scenario["id"])
    assert not blank, f"TS reference frame 0 was blank for: {blank}"

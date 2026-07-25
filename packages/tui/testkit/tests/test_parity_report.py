"""Keep the committed parity report honest.

`migrate_next.py --status` refuses to accept a ticked TUI step while
`docs/tui-parity-report.json` still lists unported scenarios — which only works
if the report reflects reality. This test fails the moment it drifts, so the
report cannot quietly become a stale file that green-lights a step.
"""

from __future__ import annotations

import json
from pathlib import Path

from cortex.tui.testkit import Verdict, evaluate_all, load_corpus, load_golden

REPO_ROOT = Path(__file__).resolve().parents[4]
REPORT = REPO_ROOT / "docs" / "tui-parity-report.json"
REFRESH = "uv run scripts/tui_parity.py --write"


def test_report_exists() -> None:
    assert REPORT.is_file(), f"{REPORT} is missing — run `{REFRESH}`"


def test_report_matches_a_fresh_evaluation() -> None:
    report = json.loads(REPORT.read_text())
    results = evaluate_all()

    expected_matched = sorted(r.id for r in results if r.verdict is Verdict.MATCH)
    expected_unported = sorted(
        (
            {"id": r.id, "kind": r.kind, "blocked_by": r.blocked_by}
            for r in results
            if r.verdict is not Verdict.MATCH
        ),
        key=lambda entry: entry["id"],
    )

    assert report["matched"] == expected_matched, f"report is stale — run `{REFRESH}`"
    assert report["unported"] == expected_unported, f"report is stale — run `{REFRESH}`"


def test_every_unported_entry_names_a_real_plan_step() -> None:
    """A gap with no owning step is a gap nobody will close."""
    plan = (REPO_ROOT / "docs" / "04-migration-plan.md").read_text()
    report = json.loads(REPORT.read_text())
    orphans = [
        entry["id"] for entry in report["unported"] if f"**{entry['blocked_by']} " not in plan
    ]
    assert not orphans, f"parity gaps blocked by a step that is not in the plan: {orphans}"


def test_corpus_and_goldens_are_in_step() -> None:
    corpus = load_corpus()
    pairs = (
        ("ts-components.json", corpus.components),
        ("ts-renderer.json", corpus.renderer),
        ("xterm-grids.json", corpus.ansi),
    )
    for name, specs in pairs:
        captured = {s["id"] for s in load_golden(name)["scenarios"]}
        expected = {s["id"] for s in specs}
        assert captured == expected, (
            f"{name} does not cover the corpus — run "
            f"`uv run scripts/tui_goldens.py --refresh`\n"
            f"  missing: {sorted(expected - captured)}\n"
            f"  stale:   {sorted(captured - expected)}"
        )

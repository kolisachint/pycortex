#!/usr/bin/env python3
"""Run the end-to-end TUI corpus and report what the product actually does.

  uv run scripts/tui_e2e.py                # human summary, grouped by step
  uv run scripts/tui_e2e.py --refresh      # also rewrite docs/tui-e2e-report.json
  uv run scripts/tui_e2e.py --detail <id>  # full traceback for one scenario
  uv run scripts/tui_e2e.py --step 7.3     # only scenarios owned by one step

`docs/tui-e2e-report.json` is what `migrate_next.py --status` reads, so a Phase 7
step cannot be ticked while the scenarios it owns are pending or failing.

The counterpart of `tui_parity.py`: that one asks "does this component render
like the TS component", this one asks "does the product behave like hoocode when
somebody runs it".
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT = REPO_ROOT / "docs" / "tui-e2e-report.json"


def _load():  # type: ignore[no-untyped-def]
    """Import the corpus from the source tree, without needing an editable install."""
    for leaf in ("code/e2e", "tui/testkit", "tui/components", "tui/render"):
        sys.path.insert(0, str(REPO_ROOT / "packages" / Path(leaf) / "src"))
    from cortex.code.e2e import SCENARIOS, run_scenario

    return SCENARIOS, run_scenario


def git_rev() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


GLYPH = {"passing": "✓", "failing": "✗", "pending": "·"}


def step_order(step_id: str) -> tuple[int, ...]:
    """Sort `7.10` after `7.2`, not before it."""
    try:
        return tuple(int(part) for part in step_id.split("."))
    except ValueError:
        return (999,)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="rewrite the report file")
    parser.add_argument("--detail", metavar="ID", help="print the full detail for one scenario")
    parser.add_argument("--step", metavar="ID", help="only scenarios owned by this step")
    args = parser.parse_args()

    scenarios, run_scenario = _load()
    if args.step:
        scenarios = [s for s in scenarios if s.blocked_by == args.step]
        if not scenarios:
            print(f"no scenarios owned by step {args.step}", file=sys.stderr)
            return 1

    if args.detail:
        matching = [s for s in scenarios if s.id == args.detail]
        if not matching:
            print(f"no such scenario: {args.detail}", file=sys.stderr)
            return 1
        result = run_scenario(matching[0])
        print(f"{result.id} [{result.blocked_by}] -> {result.status}")
        print(result.detail or "(passed)")
        return 0

    results = [run_scenario(s) for s in scenarios]
    titles = {s.id: s.title for s in scenarios}

    by_step: dict[str, list[object]] = {}
    for result in results:
        by_step.setdefault(result.blocked_by, []).append(result)

    passing = [r for r in results if r.status == "passing"]
    failing = [r for r in results if r.status == "failing"]

    print("End-to-end TUI corpus — what `pycortex` shows the user\n")
    for step in sorted(by_step, key=step_order):
        group = by_step[step]
        done = sum(1 for r in group if r.status == "passing")
        print(f"  step {step}  {done}/{len(group)}")
        for result in group:
            print(f"    {GLYPH[result.status]} {result.id:<34} {titles[result.id]}")
    print(f"\n  {len(passing)}/{len(results)} passing, {len(failing)} failing")
    print("  detail: uv run scripts/tui_e2e.py --detail <scenario-id>")

    if args.refresh:
        payload = {
            "generated_from": git_rev(),
            "note": (
                "Written by scripts/tui_e2e.py. Consumed by scripts/migrate_next.py --status. "
                "A Phase 7 step cannot be ticked while it still owns unmet scenarios."
            ),
            "passing": sorted(r.id for r in passing),
            "unmet": sorted(
                (
                    {
                        "id": r.id,
                        "status": r.status,
                        "blocked_by": r.blocked_by,
                        "title": titles[r.id],
                    }
                    for r in results
                    if r.status != "passing"
                ),
                key=lambda entry: entry["id"],
            ),
        }
        REPORT.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nwrote {REPORT.relative_to(REPO_ROOT)}")

    # Failing is a regression and should break a pipeline; pending is expected.
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())

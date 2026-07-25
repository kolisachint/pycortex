#!/usr/bin/env python3
"""Report how much of the TUI corpus pycortex reproduces, and write the status file.

  uv run scripts/tui_parity.py            # human summary
  uv run scripts/tui_parity.py --write    # also refresh docs/tui-parity-report.json
  uv run scripts/tui_parity.py --detail <id>   # full screen diff for one scenario

`docs/tui-parity-report.json` is what `migrate_next.py --status` reads, so a TUI
step cannot be ticked while its scenarios still diverge.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT = REPO_ROOT / "docs" / "tui-parity-report.json"


def _load():  # type: ignore[no-untyped-def]
    sys.path[:0] = [
        str(REPO_ROOT / "packages" / "tui" / "testkit" / "src"),
        str(REPO_ROOT / "packages" / "tui" / "components" / "src"),
        str(REPO_ROOT / "packages" / "tui" / "render" / "src"),
        str(REPO_ROOT / "packages" / "tui" / "util" / "src"),
        str(REPO_ROOT / "packages" / "tui" / "editing" / "src"),
    ]
    from cortex.tui.testkit import Verdict, evaluate_all

    return Verdict, evaluate_all


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="refresh the report file")
    parser.add_argument("--detail", metavar="ID", help="print the full diff for one scenario")
    args = parser.parse_args()

    verdict_cls, evaluate_all = _load()
    results = evaluate_all()

    if args.detail:
        matching = [r for r in results if r.id == args.detail]
        if not matching:
            print(f"no such scenario: {args.detail}", file=sys.stderr)
            return 1
        result = matching[0]
        print(f"{result.id} [{result.kind}] -> {result.verdict.value}")
        print(result.detail or "(no differences)")
        return 0

    by_kind: dict[str, list[object]] = {}
    for result in results:
        by_kind.setdefault(result.kind, []).append(result)

    print("TUI parity — pycortex vs. hoocode TS, compared as rendered screens\n")
    for kind in sorted(by_kind):
        group = by_kind[kind]
        matched = [r for r in group if r.verdict is verdict_cls.MATCH]  # type: ignore[attr-defined]
        mismatched = [r for r in group if r.verdict is verdict_cls.MISMATCH]  # type: ignore[attr-defined]
        unported = [r for r in group if r.verdict is verdict_cls.UNPORTED]  # type: ignore[attr-defined]
        print(
            f"  {kind:<10} {len(matched):>2}/{len(group)} match"
            f"   {len(mismatched)} mismatch   {len(unported)} unported"
        )
        for result in mismatched + unported:
            first = (result.detail or "").splitlines()  # type: ignore[attr-defined]
            reason = first[0] if first else ""
            print(f"      ✗ {result.id:<40} [{result.blocked_by}] {reason}")  # type: ignore[attr-defined]
    print("\n  detail: uv run scripts/tui_parity.py --detail <scenario-id>")

    if args.write:
        payload = {
            "generated_from": git_rev(),
            "note": (
                "Written by scripts/tui_parity.py. Consumed by scripts/migrate_next.py --status."
            ),
            "matched": sorted(r.id for r in results if r.verdict is verdict_cls.MATCH),  # type: ignore[attr-defined]
            "unported": sorted(
                (
                    {"id": r.id, "kind": r.kind, "blocked_by": r.blocked_by}  # type: ignore[attr-defined]
                    for r in results
                    if r.verdict is not verdict_cls.MATCH  # type: ignore[attr-defined]
                ),
                key=lambda entry: entry["id"],
            ),
        }
        REPORT.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nwrote {REPORT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

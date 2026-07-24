#!/usr/bin/env python3
"""Migration driver: reads docs/04-migration-plan.md, reports/executes the next step.

Usage:
  uv run scripts/migrate_next.py            # show next unchecked step + its spec
  uv run scripts/migrate_next.py --start    # print full working brief for the step
  uv run scripts/migrate_next.py --done 1.3 # verify gates, check the box, commit
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN = REPO_ROOT / "docs" / "04-migration-plan.md"
SRC_REPO_URL = "https://github.com/kolisachint/hoocode"

STEP_RE = re.compile(r"^- \[( |x)\] \*\*(\d+\.\d+) ([^*]+)\*\*")

# Phases 2 and 3 may run in parallel with earlier phases (per plan legend).
PARALLEL_PHASES = {1, 2}


@dataclass
class Step:
    done: bool
    id: str
    title: str
    body: str  # full markdown of the step (all continuation lines)
    line_no: int  # 0-based index of the `- [ ]` line

    @property
    def phase(self) -> int:
        return int(self.id.split(".")[0])


def parse_plan(text: str) -> list[Step]:
    lines = text.splitlines()
    steps: list[Step] = []
    current: Step | None = None
    for i, line in enumerate(lines):
        m = STEP_RE.match(line)
        if m:
            current = Step(
                done=m.group(1) == "x",
                id=m.group(2),
                title=m.group(3).strip(),
                body=line,
                line_no=i,
            )
            steps.append(current)
        elif current is not None and line.startswith("      "):
            current.body += "\n" + line
        else:
            current = None
    return steps


def source_repo() -> Path:
    env = os.environ.get("CORTEX_MIGRATION_SRC")
    if env:
        return Path(env).expanduser()
    local = Path.home() / "github" / "hoocode"
    if local.is_dir():
        return local
    cache = Path.home() / ".cache" / "cortex-migration" / "hoocode"
    if not cache.is_dir():
        cache.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", SRC_REPO_URL, str(cache)], check=True)
    return cache


def next_steps(steps: list[Step]) -> list[Step]:
    """First unchecked step; plus the first unchecked of a parallel phase, if any."""
    pending = [s for s in steps if not s.done]
    if not pending:
        return []
    first = pending[0]
    offered = [first]
    if first.phase in PARALLEL_PHASES:
        for s in pending:
            if s.phase != first.phase and s.phase in PARALLEL_PHASES:
                offered.append(s)
                break
    return offered


def gates(packages: list[str] | None = None) -> bool:
    pytest_targets = [f"packages/{p}" for p in packages] if packages else None
    checks: list[list[str]] = [
        ["uv", "run", "pytest", *pytest_targets] if pytest_targets else ["uv", "run", "pytest"],
        ["uv", "run", "ruff", "check", "."],
        ["uv", "run", "ruff", "format", "--check", "."],
    ]
    targets = [f"packages/{p}" for p in packages] if packages else ["packages"]
    checks.append(["uv", "run", "pyright", *targets])
    for cmd in checks:
        print(f"$ {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=REPO_ROOT)
        if result.returncode == 5 and cmd[2] == "pytest":
            print("  (no tests collected — OK during bootstrap)")
            continue
        if result.returncode != 0:
            print(f"GATE FAILED: {' '.join(cmd)}", file=sys.stderr)
            return False
    return True


def cmd_show(steps: list[Step], start: bool) -> int:
    offered = next_steps(steps)
    if not offered:
        print("All steps complete. 🎉")
        return 0
    for s in offered:
        print(f"Next: {s.id} {s.title}")
    if start:
        src = source_repo()
        s = offered[0]
        print()
        print(f"Source repo: {src}")
        print(f"Step spec:\n{s.body}")
        print()
        print(
            "Gates: uv run pytest && uv run ruff check . && "
            "uv run ruff format --check . && uv run pyright packages"
        )
        print(f"When green: uv run scripts/migrate_next.py --done {s.id}")
    return 0


def extract_packages_from_step(step: Step) -> list[str] | None:
    """Extract package names from step body's gate specification.

    Looks for patterns like `pytest packages/ai/util` and returns ['ai/util'].
    """
    match = re.search(r"pytest packages/([^\s`]+)", step.body)
    if match:
        return [match.group(1)]
    return None


def cmd_done(steps: list[Step], step_id: str) -> int:
    matching = [s for s in steps if s.id == step_id]
    if not matching:
        print(f"No step {step_id} in plan", file=sys.stderr)
        return 1
    step = matching[0]
    if step.done:
        print(f"Step {step_id} is already checked", file=sys.stderr)
        return 1
    offered_ids = {s.id for s in next_steps(steps)}
    if step_id not in offered_ids:
        print(
            f"Step {step_id} is not next (next: {', '.join(sorted(offered_ids))})",
            file=sys.stderr,
        )
        return 1
    packages = extract_packages_from_step(step)
    if not gates(packages):
        print("Refusing to mark step done with a dirty gate.", file=sys.stderr)
        return 1

    text = PLAN.read_text()
    lines = text.splitlines(keepends=True)
    lines[step.line_no] = lines[step.line_no].replace("- [ ]", "- [x]", 1)
    PLAN.write_text("".join(lines))

    subprocess.run(["git", "add", "-A"], cwd=REPO_ROOT, check=True)
    msg = f"migrate: {step.id} {step.title}"
    subprocess.run(["git", "commit", "-m", msg], cwd=REPO_ROOT, check=True)
    print(f"✓ plan updated, committed: {msg!r}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="print full working brief")
    parser.add_argument("--done", metavar="ID", help="verify gates, check box, commit")
    args = parser.parse_args()

    steps = parse_plan(PLAN.read_text())
    if args.done:
        return cmd_done(steps, args.done)
    return cmd_show(steps, args.start)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Migration driver: reads docs/04-migration-plan.md, reports/executes the next step.

Usage:
  uv run scripts/migrate_next.py            # show next unchecked step + its spec
  uv run scripts/migrate_next.py --start    # print full working brief for the step
  uv run scripts/migrate_next.py --status   # phase progress + invariant audit
  uv run scripts/migrate_next.py --done 1.3 # verify gates, check the box, commit

`--status` is the authoritative status view: checkboxes alone lie (a box can be
ticked while the leaf is empty), so it re-derives progress from the plan and then
audits every ticked step against the tree.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN = REPO_ROOT / "docs" / "04-migration-plan.md"
PACKAGES = REPO_ROOT / "packages"
PARITY_REPORT = REPO_ROOT / "docs" / "tui-parity-report.json"
E2E_REPORT = REPO_ROOT / "docs" / "tui-e2e-report.json"
SRC_REPO_URL = "https://github.com/kolisachint/hoocode"

STEP_RE = re.compile(r"^- \[( |x)\] \*\*(\d+\.\d+) ([^*]+)\*\*")
PHASE_RE = re.compile(r"^## (Phase [^\n]+)")
# `packages/tui/render/src/...` or `pytest packages/ai/util` -> ("tui", "render")
LEAF_RE = re.compile(r"packages/([a-z]+)/([a-z0-9_-]+)")
# A step body may declare what proves it done, e.g. `verify: leaf-populated`.
VERIFY_RE = re.compile(r"verify:\s*([a-z0-9_,\- ]+)", re.IGNORECASE)
# `cortex.tui`, or the umbrella steps' bare "tui umbrella" phrasing.
GROUP_RE = re.compile(r"cortex\.(tui|ai|agent|code)\b|\b(tui|ai|agent|code) umbrella\b")

# Phases 2 and 3 may run in parallel with earlier phases (per plan legend).
PARALLEL_PHASES = {1, 2}

# Directory names under packages/<group>/ that are not leaves.
NON_LEAF_DIRS = {"_meta"}


@dataclass
class Step:
    done: bool
    id: str
    title: str
    body: str  # full markdown of the step (all continuation lines)
    line_no: int  # 0-based index of the `- [ ]` line
    phase_title: str = ""

    @property
    def phase(self) -> int:
        return int(self.id.split(".")[0])


def parse_plan(text: str) -> list[Step]:
    lines = text.splitlines()
    steps: list[Step] = []
    current: Step | None = None
    phase_title = ""
    for i, line in enumerate(lines):
        phase_match = PHASE_RE.match(line)
        if phase_match:
            phase_title = phase_match.group(1).strip()
        m = STEP_RE.match(line)
        if m:
            current = Step(
                done=m.group(1) == "x",
                id=m.group(2),
                title=m.group(3).strip(),
                body=line,
                line_no=i,
                phase_title=phase_title,
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
    """Run the gates for a step.

    pytest stays targeted (whole-repo collection is broken by duplicate `tests`
    basenames), but **pyright always runs over all of `packages`**. Steps
    routinely add prerequisite modules outside their own leaf — 2.8 landed
    `ai/util/tool_constraints.py` while its gate only type-checked
    `ai/provider-openai`, and two errors sat in `main` as a result.
    """
    # Always build targeted pytest commands to avoid whole-repo collection failures
    if packages:
        pytest_targets = [f"packages/{p}" for p in packages]
    else:
        # Discover all leaf packages for targeted execution
        pytest_targets = []
        for group in PACKAGES.iterdir():
            if group.is_dir():
                for leaf in group.iterdir():
                    if leaf.is_dir() and (leaf / "pyproject.toml").is_file():
                        pytest_targets.append(f"packages/{group.name}/{leaf.name}")
    # Each package must be tested separately to avoid namespace conflicts
    checks: list[list[str]] = [["uv", "run", "pytest", t] for t in pytest_targets] + [
        ["uv", "run", "ruff", "check", "."],
        ["uv", "run", "ruff", "format", "--check", "."],
        ["uv", "run", "pyright", "packages"],
    ]
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


def leaf_dirs() -> list[Path]:
    if not PACKAGES.is_dir():
        return []
    return sorted(
        leaf
        for group in PACKAGES.iterdir()
        if group.is_dir()
        for leaf in group.iterdir()
        if leaf.is_dir() and (leaf / "pyproject.toml").is_file()
    )


def leaf_cortex_table(leaf: Path) -> dict[str, object]:
    data = tomllib.loads((leaf / "pyproject.toml").read_text())
    tool = data.get("tool", {})
    cortex = tool.get("cortex", {}) if isinstance(tool, dict) else {}
    return cortex if isinstance(cortex, dict) else {}


def leaf_publishes(leaf: Path) -> bool:
    return bool(leaf_cortex_table(leaf).get("publish", False))


def leaf_never_publishes(leaf: Path) -> bool:
    """Leaves that ship to nobody by design, e.g. `tui/testkit` (test infra).

    `publish = false` means "not yet"; an umbrella step is entitled to demand it
    be flipped. `never_publish = true` means "not ever", so the group-published
    audit skips the leaf instead of demanding the impossible.
    """
    return bool(leaf_cortex_table(leaf).get("never_publish", False))


def leaf_is_populated(leaf: Path) -> bool:
    """A leaf counts as ported once it ships module code and at least one test."""
    src = leaf / "src"
    has_code = any(p.stat().st_size > 0 for p in src.rglob("*.py")) if src.is_dir() else False
    has_tests = any((leaf / "tests").glob("test_*.py")) if (leaf / "tests").is_dir() else False
    return has_code and has_tests


def step_leaves(step: Step) -> list[Path]:
    """Leaf directories a step's body points at, deduped and existing only."""
    seen: dict[str, Path] = {}
    for group, leaf in LEAF_RE.findall(step.body):
        if leaf in NON_LEAF_DIRS:
            continue
        path = PACKAGES / group / leaf
        if path.is_dir() and (path / "pyproject.toml").is_file():
            seen[f"{group}/{leaf}"] = path
    return [seen[k] for k in sorted(seen)]


def step_groups(step: Step) -> set[str]:
    """Package groups (`tui`, `ai`, …) a step is about — from its body, else its phase."""
    for text in (f"{step.title}\n{step.body}", step.phase_title):
        groups = {a or b for a, b in GROUP_RE.findall(text)}
        groups = {g for g in groups if (PACKAGES / g).is_dir()}
        if groups:
            return groups
    return set()


def step_verifiers(step: Step) -> set[str]:
    """Explicit `verify:` tokens in the step body, else inferred defaults."""
    match = VERIFY_RE.search(step.body)
    if match:
        return {tok.strip() for tok in match.group(1).split(",") if tok.strip()}
    verifiers = {"leaf-populated"}
    if "publish=true" in step.body or "publishable" in step.title.lower():
        verifiers.add("group-published")
    # Phase 7 is the integration phase, and it exists because `leaf-populated`
    # is satisfiable by a stub — 5.2 shipped one and was ticked green. Its steps
    # are held to what the screen shows and to the absence of stub markers.
    if step.phase == 7:
        verifiers |= {"e2e-scenarios", "no-stubs"}
    return verifiers


# Phrases that mark code as deliberately unfinished. `leaf-populated` cannot see
# these — a file full of them is still "module code" — which is exactly how a
# stubbed interactive mode passed the audit.
STUB_MARKERS = (
    "not yet implemented",
    "not implemented yet",
    "simplified stub",
    "is a simplified implementation",
    "NotImplementedError",
)


def stub_markers_in(leaf: Path) -> list[str]:
    """Stub markers found under a leaf's `src`, as `file:line: marker` strings."""
    src = leaf / "src"
    if not src.is_dir():
        return []
    found: list[str] = []
    for path in sorted(src.rglob("*.py")):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            lowered = line.lower()
            for marker in STUB_MARKERS:
                if marker.lower() in lowered:
                    rel = path.relative_to(REPO_ROOT)
                    found.append(f"{rel}:{lineno}: {marker}")
    return found


def e2e_gaps() -> list[tuple[str, str]]:
    """End-to-end scenarios the product does not yet satisfy, as (owning step, message).

    Mirrors `parity_gaps`, one layer up: that one guards "this component renders
    like the TS one", this one guards "the product does the thing when run".
    """
    if not E2E_REPORT.is_file():
        return []
    try:
        report = json.loads(E2E_REPORT.read_text())
    except json.JSONDecodeError:
        return [("?", f"{E2E_REPORT.name}: not valid JSON")]
    by_step: dict[str, list[str]] = {}
    for entry in report.get("unmet", []):
        by_step.setdefault(entry.get("blocked_by", "?"), []).append(entry.get("id", "?"))
    return [
        (
            step_id,
            f"step {step_id}: {len(ids)} end-to-end scenario(s) not met "
            f"({', '.join(sorted(ids)[:3])}{'…' if len(ids) > 3 else ''}) — "
            f"see `uv run scripts/tui_e2e.py --step {step_id}`",
        )
        for step_id, ids in sorted(by_step.items())
    ]


def audit(steps: list[Step]) -> list[str]:
    """Check every ticked step against the tree. Returns human-readable problems.

    A checkbox is a claim; this is the evidence. Anything reported here means the
    plan says done but the repo disagrees.
    """
    problems: list[str] = []
    for step in steps:
        if not step.done:
            continue
        verifiers = step_verifiers(step)
        leaves = step_leaves(step)
        if "leaf-populated" in verifiers:
            empty = [f"{p.parent.name}/{p.name}" for p in leaves if not leaf_is_populated(p)]
            if empty:
                problems.append(
                    f"{step.id} {step.title}: checked, but no code+tests in {', '.join(empty)}"
                )
        if "no-stubs" in verifiers:
            for leaf in leaves:
                markers = stub_markers_in(leaf)
                if markers:
                    shown = "; ".join(markers[:3])
                    more = f" (+{len(markers) - 3} more)" if len(markers) > 3 else ""
                    problems.append(
                        f"{step.id} {step.title}: checked, but stub markers remain — {shown}{more}"
                    )
        if "group-published" in verifiers:
            groups = {p.parent.name for p in leaves} or step_groups(step)
            unpublished: list[str] = []
            for group in sorted(groups):
                for leaf in leaf_dirs():
                    if leaf.parent.name != group or leaf_never_publishes(leaf):
                        continue
                    if not leaf_publishes(leaf):
                        unpublished.append(f"{group}/{leaf.name}")
            if unpublished:
                problems.append(
                    f"{step.id} {step.title}: checked, but publish=false on "
                    f"{', '.join(unpublished)}"
                )
    # A parity gap is owned by the step that closes it. It only contradicts a
    # checkbox when *that* step is the one ticked — building the harness (1.9)
    # must not be blocked by the renderer gaps (1.5) it exists to expose.
    ticked = {s.id for s in steps if s.done}
    problems.extend(msg for step_id, msg in parity_gaps() if step_id in ticked)
    # Same ownership rule for the end-to-end corpus: an unmet scenario only
    # contradicts a checkbox when the step that owes it is the one ticked.
    # Building the harness (7.1) must not be blocked by the shell (7.2) it exists
    # to test.
    e2e_owed = {
        step_id
        for step_id, _ in e2e_gaps()
        if any(s.id == step_id and "e2e-scenarios" in step_verifiers(s) for s in steps)
    }
    problems.extend(msg for step_id, msg in e2e_gaps() if step_id in ticked and step_id in e2e_owed)
    return problems


def parity_gaps() -> list[tuple[str, str]]:
    """TUI scenarios that still diverge, as (owning step id, message)."""
    if not PARITY_REPORT.is_file():
        return []
    try:
        report = json.loads(PARITY_REPORT.read_text())
    except json.JSONDecodeError:
        return [("?", f"{PARITY_REPORT.name}: not valid JSON")]
    by_step: dict[str, list[str]] = {}
    for entry in report.get("unported", []):
        by_step.setdefault(entry.get("blocked_by", "?"), []).append(entry.get("id", "?"))
    return [
        (
            step_id,
            f"step {step_id}: {len(ids)} tui scenario(s) still diverge from the TS "
            f"({', '.join(sorted(ids)[:3])}{'…' if len(ids) > 3 else ''})",
        )
        for step_id, ids in sorted(by_step.items())
    ]


def cmd_status(steps: list[Step], as_json: bool, strict: bool) -> int:
    phases: dict[str, list[Step]] = {}
    for step in steps:
        phases.setdefault(step.phase_title or "(unphased)", []).append(step)
    problems = audit(steps)
    offered = next_steps(steps)

    if as_json:
        print(
            json.dumps(
                {
                    "phases": [
                        {
                            "title": title,
                            "done": sum(1 for s in group if s.done),
                            "total": len(group),
                            "steps": [
                                {"id": s.id, "title": s.title, "done": s.done} for s in group
                            ],
                        }
                        for title, group in phases.items()
                    ],
                    "next": [{"id": s.id, "title": s.title} for s in offered],
                    "problems": problems,
                },
                indent=2,
            )
        )
        return 1 if (strict and problems) else 0

    print(f"Migration status — {PLAN.relative_to(REPO_ROOT)}\n")
    width = max(len(t) for t in phases) if phases else 0
    for title, group in phases.items():
        done = sum(1 for s in group if s.done)
        total = len(group)
        filled = round(10 * done / total) if total else 0
        bar = "█" * filled + "·" * (10 - filled)
        print(f"  {title:<{width}}  {done:>2}/{total:<2} {bar}")
    total_done = sum(1 for s in steps if s.done)
    print(f"\n  {'TOTAL':<{width}}  {total_done:>2}/{len(steps):<2}")

    print()
    if offered:
        for s in offered:
            print(f"Next: {s.id} {s.title}")
    else:
        print("All steps complete. 🎉")

    print()
    if problems:
        print(f"Audit — {len(problems)} problem(s): a ticked box the tree does not back up")
        for problem in problems:
            print(f"  ✗ {problem}")
    else:
        print("Audit — clean: every ticked step is backed by code + tests on disk.")

    ticked = {s.id for s in steps if s.done}
    open_gaps = [msg for step_id, msg in parity_gaps() if step_id not in ticked]
    if open_gaps:
        print("\nKnown gaps (tracked, not yet claimed done)")
        for gap in open_gaps:
            print(f"  · {gap}")
    return 1 if (strict and problems) else 0


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
    For publishable/umbrella steps without explicit targets, discovers leaves
    in the mentioned group.
    """
    match = re.search(r"pytest packages/([^\s`]+)", step.body)
    if match:
        return [match.group(1)]
    # For umbrella/publishable steps, discover leaves in the group
    if "publishable" in step.title.lower():
        # Extract group from title like "agent umbrella publishable"
        group_match = re.search(r"(\w+) umbrella", step.title)
        if group_match:
            group_name = group_match.group(1)
            group_dir = PACKAGES / group_name
            if group_dir.is_dir():
                leaves = []
                for leaf in sorted(group_dir.iterdir()):
                    if leaf.is_dir() and (leaf / "pyproject.toml").is_file():
                        leaves.append(f"{group_name}/{leaf.name}")
                return leaves if leaves else None
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

    # Gates only prove the code that exists is clean — an empty leaf passes them
    # trivially. Re-run this step's own verifiers so the checkbox cannot outrun
    # the tree (this is how 1.5/1.7/1.8/2.10 were ticked while still unported).
    step.done = True
    unmet = audit([step])
    step.done = False
    if unmet:
        print("Refusing to check the box — the tree does not back this step:", file=sys.stderr)
        for problem in unmet:
            print(f"  ✗ {problem}", file=sys.stderr)
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
    parser.add_argument("--status", action="store_true", help="phase progress + audit")
    parser.add_argument("--json", action="store_true", help="machine-readable --status")
    parser.add_argument("--strict", action="store_true", help="exit 1 if the audit finds problems")
    parser.add_argument("--done", metavar="ID", help="verify gates, check box, commit")
    args = parser.parse_args()

    steps = parse_plan(PLAN.read_text())
    if args.done:
        return cmd_done(steps, args.done)
    if args.status or args.json:
        return cmd_status(steps, args.json, args.strict)
    return cmd_show(steps, args.start)


if __name__ == "__main__":
    sys.exit(main())

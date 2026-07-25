#!/usr/bin/env python3
"""Regenerate the TUI parity goldens from the real hoocode TS implementation.

  uv run scripts/tui_goldens.py --refresh   # recapture goldens (needs bun + TS source)
  uv run scripts/tui_goldens.py --check     # goldens present and match the corpus?

The goldens are committed, so day-to-day `pytest packages/tui/testkit` needs
neither bun nor the TS checkout. Refresh only when the corpus grows or the
upstream TS changes.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTKIT = REPO_ROOT / "packages" / "tui" / "testkit"
GOLDENS = TESTKIT / "goldens"
REFERENCE = TESTKIT / "reference"
CORPUS = GOLDENS / "scenarios.json"
# Populated by --refresh: a copy of the TS sources with node_modules alongside,
# so bun can resolve `marked`/`get-east-asian-width` without ever writing into
# the source checkout.
STAGE = REFERENCE / ".hoocode-src"

OUTPUTS = (
    "ts-components.json",
    "ts-renderer.json",
    "xterm-grids.json",
    "marked-ast.json",
    "ts-autocomplete.json",
)

sys.path.insert(0, str(REPO_ROOT / "scripts"))


def source_tui_src() -> Path:
    from migrate_next import source_repo

    src = source_repo() / "packages" / "tui" / "src"
    if not src.is_dir():
        raise SystemExit(f"hoocode tui sources not found at {src}")
    return src


def run(cmd: list[str], cwd: Path) -> None:
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}")


def refresh() -> int:
    if shutil.which("bun") is None:
        raise SystemExit("bun is required to refresh goldens (https://bun.sh)")

    src = source_tui_src()
    if STAGE.exists():
        shutil.rmtree(STAGE)
    # Copy rather than import in place: bun resolves node_modules by walking up
    # from the importing file, and the source checkout must stay untouched.
    shutil.copytree(src, STAGE)
    print(f"staged TS sources: {src} -> {STAGE}")

    run(["bun", "install", "--frozen-lockfile"], cwd=REFERENCE) if (
        REFERENCE / "bun.lock"
    ).exists() else run(["bun", "install"], cwd=REFERENCE)
    run(["bun", "reference/dump.ts", str(STAGE), str(GOLDENS)], cwd=TESTKIT)
    run(["bun", "reference/xterm_dump.ts", str(GOLDENS)], cwd=TESTKIT)
    run(["bun", "reference/markdown_ast_dump.ts", str(STAGE), str(GOLDENS)], cwd=TESTKIT)
    run(["bun", "reference/autocomplete_dump.ts", str(STAGE), str(GOLDENS)], cwd=TESTKIT)

    shutil.rmtree(STAGE)
    print("goldens refreshed")
    return 0


def check() -> int:
    corpus = json.loads(CORPUS.read_text())
    problems: list[str] = []
    for name in OUTPUTS:
        path = GOLDENS / name
        if not path.is_file():
            problems.append(f"missing golden: {name}")
    if problems:
        for problem in problems:
            print(f"  ✗ {problem}")
        return 1

    markdown_corpus = json.loads((GOLDENS / "markdown-corpus.json").read_text())
    autocomplete_corpus = json.loads((GOLDENS / "autocomplete-corpus.json").read_text())
    pairs = (
        ("ts-components.json", corpus.get("components", [])),
        ("ts-renderer.json", corpus.get("renderer", [])),
        ("xterm-grids.json", corpus.get("ansi", [])),
        ("marked-ast.json", markdown_corpus.get("samples", [])),
        ("ts-autocomplete.json", autocomplete_corpus.get("scenarios", [])),
    )
    for name, specs in pairs:
        golden = json.loads((GOLDENS / name).read_text())
        captured = {s["id"] for s in golden["scenarios"]}
        expected = {s["id"] for s in specs}
        missing = expected - captured
        extra = captured - expected
        if missing:
            problems.append(f"{name}: no golden for {sorted(missing)}")
        if extra:
            problems.append(f"{name}: stale golden for {sorted(extra)}")

    if problems:
        print("Goldens are out of date — run: uv run scripts/tui_goldens.py --refresh")
        for problem in problems:
            print(f"  ✗ {problem}")
        return 1
    print("goldens cover every scenario in the corpus")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="recapture from the TS source")
    parser.add_argument("--check", action="store_true", help="verify goldens cover the corpus")
    args = parser.parse_args()
    if args.refresh:
        return refresh()
    return check()


if __name__ == "__main__":
    sys.exit(main())

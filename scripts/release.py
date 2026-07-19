#!/usr/bin/env python3
"""Local release driver: bump + commit + tag + push. CI publishes.

Usage: uv run scripts/release.py <patch|minor|major>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def run(*cmd: str) -> str:
    return subprocess.run(cmd, cwd=REPO_ROOT, check=True, capture_output=True, text=True).stdout


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("patch", "minor", "major"):
        print("Usage: uv run scripts/release.py <patch|minor|major>", file=sys.stderr)
        return 1

    if run("git", "status", "--porcelain").strip():
        print("Working tree is dirty; commit or stash first.", file=sys.stderr)
        return 1
    if run("git", "branch", "--show-current").strip() != "main":
        print("Releases are cut from main only.", file=sys.stderr)
        return 1

    out = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "bump_versions.py"), sys.argv[1]],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    version = out.strip().splitlines()[-1]
    tag = f"v{version}"

    run("git", "add", "-A")
    run("git", "commit", "-m", f"Release {tag}")
    run("git", "tag", tag)
    run("git", "push", "origin", "main", tag)
    print(f"Pushed {tag}; CI will build and publish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

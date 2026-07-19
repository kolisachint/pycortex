#!/usr/bin/env python3
"""Lockstep version bump for every workspace package.

Rewrites `version` in each packages/*/pyproject.toml and any sibling dependency
pins (e.g. `cortexcode-ai>=X,<Y`), then refreshes uv.lock.

Usage: uv run scripts/bump_versions.py <patch|minor|major>
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES_DIR = REPO_ROOT / "packages"


def bump(version: str, kind: str) -> str:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-.+)?", version)
    if not m:
        raise ValueError(f"Cannot parse version: {version}")
    major, minor, patch = (int(g) for g in m.groups())
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def workspace_pyprojects() -> list[Path]:
    return sorted(
        p / "pyproject.toml" for p in PACKAGES_DIR.iterdir() if (p / "pyproject.toml").is_file()
    )


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("patch", "minor", "major"):
        print("Usage: uv run scripts/bump_versions.py <patch|minor|major>", file=sys.stderr)
        return 1
    kind = sys.argv[1]

    pyprojects = workspace_pyprojects()
    if not pyprojects:
        print("No packages found", file=sys.stderr)
        return 1

    # Collect current versions and dist names.
    names: dict[Path, str] = {}
    versions: set[str] = set()
    for pp in pyprojects:
        data = tomllib.loads(pp.read_text())
        names[pp] = data["project"]["name"]
        versions.add(data["project"]["version"])
    if len(versions) != 1:
        print(f"Packages are not in lockstep: {sorted(versions)}", file=sys.stderr)
        return 1

    old = versions.pop()
    new = bump(old, kind)

    for pp in pyprojects:
        text = pp.read_text()
        # Bump the package's own version.
        text, n = re.subn(
            rf'^version = "{re.escape(old)}"$', f'version = "{new}"', text, count=1, flags=re.M
        )
        if n != 1:
            print(f"Could not bump version in {pp}", file=sys.stderr)
            return 1
        # Re-pin sibling dependency ranges (only strings that carry a specifier,
        # so the package's own `name = "..."` line is left alone).
        for sibling in names.values():
            text = re.sub(
                rf'"{re.escape(sibling)}\s*[><=~!][^"]*"',
                f'"{sibling}>={new},<{bump(new, "minor")}"',
                text,
            )
        pp.write_text(text)
        print(f"{names[pp]}: {old} -> {new}")

    subprocess.run(["uv", "lock"], cwd=REPO_ROOT, check=True)
    print(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build and publish every package marked `[tool.cortex] publish = true`.

Skips versions that already exist on PyPI (idempotent re-runs). CI-only:
expects PYPI_TOKEN in the environment.

Usage: uv run scripts/publish_packages.py [--dry-run]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES_DIR = REPO_ROOT / "packages"


def on_pypi(name: str, version: str) -> bool:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url) as resp:
            json.load(resp)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    token = os.environ.get("PYPI_TOKEN", "")
    if not token and not dry_run:
        print("PYPI_TOKEN not set", file=sys.stderr)
        return 1

    published = 0
    for pyproject in sorted(PACKAGES_DIR.rglob("pyproject.toml")):
        data = tomllib.loads(pyproject.read_text())
        name = data["project"]["name"]
        version = data["project"]["version"]
        if not data.get("tool", {}).get("cortex", {}).get("publish", False):
            print(f"skip  {name} (publish = false)")
            continue
        if on_pypi(name, version):
            print(f"skip  {name} {version} (already on PyPI)")
            continue
        print(f"build {name} {version}")
        if dry_run:
            published += 1
            continue
        dist = REPO_ROOT / "dist" / name
        subprocess.run(
            ["uv", "build", "--package", name, "--out-dir", str(dist)],
            cwd=REPO_ROOT,
            check=True,
        )
        subprocess.run(
            ["uv", "publish", "--token", token, str(dist / "*")],
            cwd=REPO_ROOT,
            check=True,
        )
        published += 1
    print(f"published {published} package(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

---
name: release
description: Flip a package to publishable and cut a lockstep release via CI.
---

# release

When a phase's "publishable" step is reached (1.13, 2.11, 3.9, 5.7):

## Checklist (doc 03 §5)

1. All phase steps for the package are checked in docs/04-migration-plan.md.
2. Every ported TS test file has a Python counterpart.
3. `uv run pyright packages/<pkg>` passes strict.
4. The package README documents its public API (it renders on PyPI).

## Then

1. Flip `[tool.cortex] publish = false → true` in the package's `pyproject.toml`.
2. Trigger `release.yml` (workflow dispatch, choose bump level) **or** apply a
   `pypi:patch` / `pypi:minor` / `pypi:major` label to the PR (merge-release.yml).
3. Verify the version on PyPI and the GitHub release notes.

## Rules

- Never `uv publish` locally — publishing is CI-only via `PYPI_TOKEN`.
- Versions are lockstep across all four packages; never hand-edit one version.

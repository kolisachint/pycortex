# Release Pipeline — CI, Versioning, PyPI

**Status:** DRAFT
Mirrors hoocode's npm pipeline (`ci.yml`, `release.yml`, `merge-release.yml`,
`bump-versions.mjs`, `release.mjs`) using the **`PYPI_TOKEN`** already stored in GitHub
repo secrets.

## 1. Versioning

- **Lockstep** across all four packages (like hoocode 0.4.x), starting at `0.1.0`.
- `scripts/bump_versions.py {patch|minor|major}` rewrites `version` in every
  `packages/*/pyproject.toml` **and** the sibling dependency pins
  (`cortexcode-ai>=X,<X+1`-style ranges), then updates `uv.lock`.
- A package is only published once it reaches **"publishable"** status in the migration
  plan (T0/T1 first). Unpublishable packages are skipped by the publish script via a
  `[tool.cortex] publish = false` marker in their `pyproject.toml`.

## 2. Workflows

### `ci.yml` — on push/PR to main

```
jobs:
  check:   uv sync --all-packages → ruff check . → ruff format --check . → pyright
  test:    matrix [tui, ai, agent, coding-agent] → uv run pytest packages/<p>
  build:   uv build --all-packages  (sdist+wheel smoke test)
```

### `release.yml` — manual dispatch (input: patch|minor|major)

```
1. uv run scripts/bump_versions.py <level>
2. commit "Release vX.Y.Z", tag vX.Y.Z, push
3. uv build for each package with publish=true
4. uv publish --token ${{ secrets.PYPI_TOKEN }}   # per package, skip already-published
5. gh release create vX.Y.Z --generate-notes
```

### `merge-release.yml` — on PR merge

Auto-release when the PR carries a label `pypi:patch` / `pypi:minor` / `pypi:major`
(direct port of hoocode's `npm:*` labels). Invokes the same steps as `release.yml`.

## 3. PyPI specifics

- **Token:** `PYPI_TOKEN` secret → `uv publish --token`. Later, optionally migrate to
  PyPI **Trusted Publishing** (OIDC, no token) — noted as post-migration enhancement.
- **Name reservation:** the very first migration step publishes `0.0.1` placeholder
  sdists for all four distribution names to reserve them (or verifies availability and
  fails loudly → naming fallback in doc 02).
- **Idempotent publish:** the publish step checks PyPI for the exact version first
  (`uv publish` fails on dupes); re-runs of a release job must be safe.

## 4. Local commands

| Command | Action |
| --- | --- |
| `uv run scripts/bump_versions.py patch` | lockstep bump |
| `uv run scripts/release.py patch` | bump + tag + push (CI publishes) |
| `uv build --package cortexcode-ai` | build one package |
| `uv publish` | never run locally — CI only |

## 5. Definition of "stable → publish"

A package flips `publish = false → true` when:

1. All Phase steps for it in [04-migration-plan.md](04-migration-plan.md) are checked.
2. Test coverage of ported modules ≥ the TS originals' test surface (each ported TS test
   file has a Python counterpart).
3. pyright strict passes (T0/T1 packages).
4. Its public API is documented in the package README (rendered on PyPI).

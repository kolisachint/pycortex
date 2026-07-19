# PyCortex Migration — Overview

**Status:** DRAFT — pending joint review
**Source:** [github.com/kolisachint/hoocode](https://github.com/kolisachint/hoocode)
(TypeScript monorepo, 828 commits; local clone at `~/github/hoocode`, branch `main`)
**Target:** this repo (`pycortex`) — an ultra-modular Python monorepo

## Goal

Port hoocode (a deterministic terminal coding agent) to Python as a set of small,
independently versioned, independently testable packages, with stable components
published automatically to PyPI. **Phase 1 is migration only** — feature parity of the
core, no enhancements. Enhancements come after the port is green.

## Principles

1. **Ultra-modular.** Each package has one responsibility, minimal deps, its own tests,
   its own `pyproject.toml`. Leaves publish first.
2. **Stability-ordered.** Port in order of *lowest churn first* (measured from hoocode git
   history — see [01-source-analysis.md](01-source-analysis.md)). Stable code lands on
   PyPI early; volatile code stays in-repo until it settles.
3. **Never broken.** Every migration step ends with the full test suite green and the
   workspace importable. Steps are small, atomic, and independently revertable.
4. **Testable by construction.** Every ported module ships with tests translated from (or
   inspired by) the TS originals, plus a fake/faux provider layer so nothing needs real
   API keys to test.
5. **Executable plan.** The migration plan
   ([04-migration-plan.md](04-migration-plan.md)) is a machine-readable checklist. A
   `migrate-next` command/skill reads it, executes the next unchecked step, runs the
   gates, and checks it off. Humans and agents share the same source of truth.
6. **Automated releases.** GitHub Actions + `PYPI_TOKEN` (already in repo secrets)
   publish tagged packages. No manual uploads.

## Document map

| Doc | Contents |
| --- | --- |
| [00-migration-overview.md](00-migration-overview.md) | This file — goals, principles, index |
| [01-source-analysis.md](01-source-analysis.md) | hoocode inventory, dependency graph, churn analysis, stability tiers |
| [02-target-architecture.md](02-target-architecture.md) | Python monorepo layout, package map, TS→Py dependency mapping |
| [03-release-pipeline.md](03-release-pipeline.md) | CI, versioning, PyPI publishing with `PYPI_TOKEN` |
| [04-migration-plan.md](04-migration-plan.md) | The executable, checkbox-driven migration plan |
| [05-skills-and-commands.md](05-skills-and-commands.md) | Skills + the `migrate-next` command that drives the plan |

## Open questions for review

1. **PyPI naming.** `pycortex` is **taken on PyPI** (a neuroimaging package). Proposed
   distribution names: `cortex-tui`, `cortex-ai`, `cortex-agent-core`, `cortex-code`
   (CLI) under import namespace `cortex.*` — but `cortex` import name may collide too.
   Alternatives in [02-target-architecture.md](02-target-architecture.md) §Naming.
2. **TUI scope.** Port the differential-rendering TUI faithfully, or build interactive
   mode on an existing Python TUI (e.g. prompt_toolkit/Textual) to cut ~11K LOC of port
   work? Recommendation: faithful port of a *minimal* subset (see plan Phase 2).
3. **Which providers first.** Recommendation: anthropic + openai + a `faux` test provider
   in Phase 3; the long tail later.

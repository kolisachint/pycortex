# AGENTS.md — pycortex

Orientation for coding agents. **Read this first, then don't re-derive what's here.**

pycortex is a mechanical Python port of hoocode (TS). One step per session, feature
parity only, no enhancements.

This file holds only what does *not* change per step. **Never hand-write status here**
— it goes stale within a session or two. Ask the driver instead:

```
uv run scripts/migrate_next.py --status    # phase progress, next step, audit
uv run scripts/migrate_next.py --start     # working brief for the next step
```

## Start every migration session

1. `uv run scripts/migrate_next.py --start` — emits the next step brief.
2. Read `.hoocode/MIGRATION_NOTES.md` — living doc: conventions, type mappings,
   gotchas, per-step log. **Update it when you finish a step.**
3. Do the work (see skills in `.hoocode/skills/`).
4. Gates (all green) — the driver runs these **targeted at the step's packages**:
   `uv run pytest packages/<group>/<leaf> && uv run ruff check . && uv run ruff format --check . && uv run pyright packages`
5. `uv run scripts/migrate_next.py --done <id>` — re-runs gates **and the audit**,
   checks the box, commits `migrate: <id> <title>`.

## Status is machine-derived, and audited

A checked box is a claim; `--status` checks it against the tree and prints what
doesn't hold up:

- the step's leaf actually has module code **and** a `tests/test_*.py`;
- a step promising `publish = true` really flipped it on every leaf in the group;
- TUI steps have no unported scenarios left in `docs/tui-parity-report.json`.

`--done` runs that same audit and **refuses to tick a box the tree can't back up**.
Four boxes (1.5, 1.7, 1.8, 2.10) were ticked before this existed and had to be
reverted — if `--status` reports a problem, fix the tree or the plan, don't
hand-edit the checkbox.

## Source of truth (don't rescan every time)

| What | Where |
| --- | --- |
| Checklist / plan | `docs/04-migration-plan.md` (driven by `scripts/migrate_next.py`) |
| Live progress + audit | `uv run scripts/migrate_next.py --status` |
| Architecture + package map | `docs/02-target-architecture.md` |
| Conventions, gotchas, step log | `.hoocode/MIGRATION_NOTES.md` |
| TS source (never edit) | see below — **not** a fixed path |

**Resolving the TS source.** `scripts/migrate_next.py` resolves it in this order, and
so should you:

1. `$CORTEX_MIGRATION_SRC`, if set;
2. `~/github/hoocode`, if it exists (the author's laptop);
3. otherwise a shallow clone cached at `~/.cache/cortex-migration/hoocode`, made
   automatically from <https://github.com/kolisachint/hoocode>.

On a fresh container none of it exists until the driver clones it, so run
`--start` before going looking for files.

## Repo layout

- Each leaf = its own package: `packages/<group>/<leaf>/`
  - code: `src/cortex/<group>/<...>/` (namespace package, no top-level `__init__`)
  - tests: `<leaf>/tests/test_*.py`
  - `pyproject.toml` with `[tool.uv.sources.*] workspace = true` for internal deps
- Directory names may contain hyphens (`provider-google`); **module paths may not** —
  the code inside goes to `src/cortex/ai/providers/google/`, never
  `src/cortex/ai/provider-google/`. Several placeholder leaves shipped the broken
  form and had to be deleted.
- `uv run pytest` (whole repo) fails to collect (duplicate `tests` basenames) —
  this is pre-existing. Gates run **targeted** pytest per package.
- `uv sync --all-packages` (not plain `uv sync`) — anything less collapses the venv
  to a couple of editables and breaks namespace-package merging.

## TUI work is verified against a surface, not by reading the diff

For every leaf except the tui ones, "the Python reads like the TS" is decent
evidence. For TUI rendering it is not: the contract is the grid of cells the user
sees, and a plausible-looking refactor can produce a completely different screen.

`packages/tui/testkit/` (`cortex.tui.testkit`, never published) is the authority:

- `Surface` — a cell grid plus an ANSI interpreter, cross-validated against
  `@xterm/headless` so it is a real terminal model, not a convenient one;
- `packages/tui/testkit/goldens/` — frames captured from the **real hoocode TS**
  components and renderer;
- parity tests render the same scenario in Python and diff surfaces.

So: port a tui module, add its scenario to the corpus, and let the diff tell you
whether you got it right. Regenerate goldens with
`uv run scripts/tui_goldens.py --refresh` (needs `bun` and the TS source).

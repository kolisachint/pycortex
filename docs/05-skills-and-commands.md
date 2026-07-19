# Skills & Commands — Driving the Migration

**Status:** DRAFT

The migration is executed by agents (and humans) through a small set of **skills** stored
in `.hoocode/skills/` of this repo, plus one script. The design goal: every migration
session is fast, repeatable, safe, and leaves the repo green.

## 1. The `migrate-next` command

`scripts/migrate_next.py` — also exposed as the skill `migrate-next`.

```
uv run scripts/migrate_next.py            # show next unchecked step + its spec
uv run scripts/migrate_next.py --start    # print full working brief for the step
uv run scripts/migrate_next.py --done 1.3 # verify gates, check the box, commit
```

Behavior:

1. **Parse** `docs/04-migration-plan.md`, find the first `- [ ]` step (respecting phase
   ordering; parallel phases both offered).
2. **Brief**: emit the step's source files (paths inside the hoocode source repo),
   target paths, the TS test files to port, and the gate commands.
   The source repo is https://github.com/kolisachint/hoocode; the driver uses
   `CORTEX_MIGRATION_SRC` if set, else `~/github/hoocode`, else clones from GitHub into
   a cache dir — so the migration works on any machine (including CI), not just one with
   the local clone.
3. **Gate** (`--done`): run `uv run pytest` on the whole workspace, `ruff check`,
   `ruff format --check`, and `pyright` on touched packages. Only if all pass:
   flip `[ ]` → `[x]`, and commit `migrate: <id> <title>` including the plan edit.
4. **Refuse** to mark a step done with a dirty gate — this is the "never broken"
   principle enforced mechanically.

## 2. Skills (`.hoocode/skills/*.md`)

Each skill is a markdown file with frontmatter (name, description) — the format hoocode
already loads. They make agent sessions faster by encoding the repeated procedures once.

### `migrate-next.md`
Run the driver, take the emitted brief, execute the step end-to-end (port code → port
tests → gates → `--done`). Constraints restated in the skill: ≤1 step per session,
no scope creep, no enhancement, match plan's target paths exactly.

### `port-ts-module.md`
The mechanical TS→Python translation procedure:
- read the TS module *and its tests first*;
- map types per doc 02 §4 (typebox→pydantic, Promise→async, EventEmitter→callbacks,
  discriminated unions→pydantic tagged unions / `Literal` fields);
- preserve public names in snake_case; keep file-level structure 1:1 so future hoocode
  diffs are easy to re-apply;
- translate tests before implementation when feasible (port-tests-first);
- forbidden: adding features, "improving" APIs, reformatting semantics.

### `port-ts-tests.md`
bun-test → pytest translation table (describe→class/module, test→def test_,
expect(x).toBe→assert, mock timers→pytest-asyncio patterns, xterm snapshot→ANSI golden
files under `tests/goldens/`).

### `sync-from-upstream.md`
hoocode keeps moving while we migrate. Procedure: `git fetch` the source repo
(github.com/kolisachint/hoocode), then for a step already ported run
`git -C $SRC log --oneline <since>.. -- <source files>` to list upstream changes to its
sources, and apply the deltas. Keeps ports from drifting stale.

### `release.md`
When a phase's "publishable" step is reached: checklist from doc 03 §5, then trigger
`release.yml` (or apply the `pypi:patch` label on the PR).

## 3. Safety rails (all skills inherit)

- **Never** edit files in the hoocode source repo (read-only source; local clone or
  GitHub checkout alike). Upstream fixes go through PRs to
  github.com/kolisachint/hoocode, never through the migration.
- **Never** `uv publish` locally; publishing is CI-only via `PYPI_TOKEN`.
- One migration step = one commit; plan-file edit rides in the same commit.
- Any gate failure → fix before proceeding; if the step is misdesigned, edit the plan in
  a *separate* commit prefixed `plan:` and flag for human review.
- New runtime dependencies require a note in doc 02 §4 in the same PR.

## 4. Typical session

```
$ uv run scripts/migrate_next.py --start
Next: 2.6 faux provider
Sources:  ~/github/hoocode/packages/ai/src/providers/faux.ts
          ~/github/hoocode/packages/ai/test/faux.test.ts
Targets:  packages/ai/src/cortex/ai/providers/faux.py
          packages/ai/tests/test_faux.py
Gates:    uv run pytest && ruff check . && pyright packages/ai

… agent ports tests, then code, iterates until green …

$ uv run scripts/migrate_next.py --done 2.6
✓ pytest 214 passed  ✓ ruff  ✓ pyright
✓ plan updated, committed: "migrate: 2.6 faux provider"
```

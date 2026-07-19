---
name: migrate-next
description: Execute the next unchecked step of the pycortex migration plan end-to-end.
---

# migrate-next

Run the driver, take the emitted brief, execute the step end-to-end.

## Procedure

1. `uv run scripts/migrate_next.py --start` — read the brief (step spec, source repo
   location, gates).
2. Read the TS source files **and their tests** in the source repo first.
3. Port tests before implementation when feasible (see `port-ts-tests`), then port the
   module (see `port-ts-module`).
4. Iterate until all gates are green:
   `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyright packages`
5. `uv run scripts/migrate_next.py --done <id>` — re-runs gates, flips the checkbox,
   commits `migrate: <id> <title>`.

## Constraints

- **≤ 1 step per session.** Stop after `--done` succeeds.
- **No scope creep, no enhancements** — feature parity only.
- Match the plan's target paths exactly; if the step is misdesigned, edit the plan in a
  *separate* commit prefixed `plan:` and flag for human review.
- Never edit files in the hoocode source repo.

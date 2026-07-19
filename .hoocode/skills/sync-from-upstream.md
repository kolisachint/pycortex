---
name: sync-from-upstream
description: Re-apply upstream hoocode changes to modules that were already ported.
---

# sync-from-upstream

hoocode keeps moving while we migrate. Keep ports from drifting stale.

## Procedure

1. Resolve the source repo (`$CORTEX_MIGRATION_SRC`, else `~/github/hoocode`, else the
   clone cache) and `git -C $SRC fetch`.
2. For a step already ported, list upstream changes to its source files since the port:
   `git -C $SRC log --oneline <since>.. -- <source files>`
   (`<since>` = the upstream commit recorded/known at port time).
3. Read each upstream diff; apply the semantic delta to the Python port (same mapping
   rules as `port-ts-module`). Port any new/changed tests too.
4. Run the full gates; commit as `sync: <step-id> <short upstream summary>`.

## Rules

- Read-only source: never edit the hoocode repo. Upstream fixes go through PRs to
  github.com/kolisachint/hoocode.
- One sync commit per step-id; do not mix syncs with new migration steps.

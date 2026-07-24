# AGENTS.md — pycortex

Orientation for coding agents. **Read this first, then don't re-derive what's here.**

pycortex is a mechanical Python port of hoocode (TS). One step per session, feature
parity only, no enhancements.

## Start every migration session

1. `uv run scripts/migrate_next.py --start` — emits the next step brief.
2. Read `.hoocode/MIGRATION_NOTES.md` — living doc: conventions, type mappings,
   gotchas, per-step log. **Update it when you finish a step.**
3. Do the work (see skills in `.hoocode/skills/`).
4. Gates (all green):
   `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyright packages`
5. `uv run scripts/migrate_next.py --done <id>` — re-runs gates, checks the box,
   commits `migrate: <id> <title>`.

## Source of truth (don't rescan every time)

| What | Where |
| --- | --- |
| Checklist / plan | `docs/04-migration-plan.md` (driven by `scripts/migrate_next.py`) |
| Architecture + package map | `docs/02-target-architecture.md` |
| Conventions, gotchas, step log | `.hoocode/MIGRATION_NOTES.md` |
| TS source (never edit) | `/Users/sachinkoli/github/hoocode` |

## Repo layout

- Each leaf = its own package: `packages/<group>/<leaf>/`
  - code: `src/cortex/<group>/<...>/` (namespace package, no top-level `__init__`)
  - tests: `<leaf>/tests/test_*.py`
  - `pyproject.toml` with `[tool.uv.sources.*] workspace = true` for internal deps
- `uv run pytest` (whole repo) fails to collect (duplicate `tests` basenames) —
  this is pre-existing. Gates run **targeted** pytest per package.

## In progress: step 2.7 provider-anthropic

`anthropic.ts` imports **5 helper modules that are not yet ported**. These are the
real prerequisites for 2.7 and are shared with the openai/google providers, so they
land as their own units (decision recorded here so it isn't re-litigated):

| TS module | Target | Note |
| --- | --- | --- |
| `utils/sanitize-unicode.ts` (`sanitizeSurrogates`) | `cortex.ai.util` (`sanitize_unicode.py`) | 25 lines; util leaf |
| `providers/cache-retention.ts` (`resolveCacheRetention`) | **new leaf** `cortexcode-ai-provider-common` → `cortex.ai.providers._common` | shared |
| `providers/simple-options.ts` (`buildBaseOptions`, `adjustMaxTokensForThinking`) | `cortex.ai.providers._common` | shared |
| `providers/transform-messages.ts` (`transformMessages`) | `cortex.ai.providers._common` | shared, 220 lines |
| `providers/github-copilot-headers.ts` (`buildCopilotDynamicHeaders`, `hasCopilotVisionInput`) | `cortex.ai.providers._common` | shared |

Decision: the 4 provider-shared helpers go in a **new shared leaf
`cortexcode-ai-provider-common` (`cortex.ai.providers._common`)** — every
`provider-*` depends on it. This is a plan/arch change: add a row to
`docs/02-target-architecture.md` §3.2 and a prerequisite step to
`docs/04-migration-plan.md` in a **separate commit prefixed `plan:`** before
implementing 2.7.

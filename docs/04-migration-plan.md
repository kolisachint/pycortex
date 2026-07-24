# Migration Plan — Executable Checklist

**Status:** DRAFT — iteratively updated during restructure to ultramodular leaves.

This file is the **single source of truth** for migration progress. The
`migrate-next` command (see [05-skills-and-commands.md](05-skills-and-commands.md))
finds the first unchecked step, executes it, runs the gates, and checks the box in the
same commit.

**Source repo:** https://github.com/kolisachint/hoocode — resolved via
`CORTEX_MIGRATION_SRC` (else `~/github/hoocode`, else clone cache). Source paths are
relative to that repo root.

**Step contract:**

- Completable in one focused session (port one leaf's code + tests, or one plumbing
  task).
- Names source files in hoocode and target files here.
- Ends with gates green for the affected leaf(s):
  `uv run pytest packages/<leaf>` + `ruff check` + `ruff format --check` +
  `uv run pyright packages/<leaf>`.
- Committed atomically as `migrate: <step-id> <title>`.

Legend: `[ ]` pending · `[x]` done. Leaves with no cross-leaf deps may run in
parallel; the driver lists the first eligible parallel option.

---

## Phase 0 — Workspace bootstrap

- [x] **0.1 Root workspace** — root `pyproject.toml` (uv workspace, ruff, pyright,
      pytest config), `.gitignore`, `.python-version` (3.11), placeholders under
      `packages/{tui,ai,agent,code}`.
- [x] **0.2 CI** — `.github/workflows/ci.yml` per doc 03; green run.
- [x] **0.3 Name check + reservation** — reserved on PyPI:
  - lib umbrellas: `cortexcode-tui`, `cortexcode-ai`, `cortexcode-agent-core`
  - CLI umbrella: `cortexcode-cli` (`cortexcode` was taken)
  - first leaves will be added under these umbrellas.
- [x] **0.4 Release plumbing** — `scripts/bump_versions.py`, `scripts/release.py`,
      `scripts/publish_packages.py`, `.github/workflows/release.yml`,
      `.github/workflows/merge-release.yml`.
- [x] **0.5 Migration driver** — `scripts/migrate_next.py` + `.hoocode/skills/` per
      doc 05. Gate: driver correctly reports next step.

## Phase 0.5 — Ultramodular restructure

- [x] **0.6 Restructure into leaves** — split existing `packages/{tui,ai,agent,code}`
      placeholders into `_meta/` umbrellas and initial leaf directories per doc 02 §3,
      update `pyproject.toml` workspace members to `packages/*/*`, rewrite CI matrix,
      bump/publish scripts, and `migrate_next.py` for many packages. Gate: `uv sync`,
      `uv build --all-packages`, CI green.

---

## Phase 1 — `cortex.tui` leaves (no cross-leaf deps)

Parallelizable across these leaves; dependencies flow upward from util.

- [x] **1.1 util** — `packages/tui/src/utils.ts` → `packages/tui/util/src/cortex/tui/util.py`
      (ANSI width, grapheme handling, truncate/wrap) + tests. Gate: `pytest packages/tui/util`.
- [x] **1.2 fuzzy** — `packages/tui/src/fuzzy.ts` → `packages/tui/fuzzy/src/cortex/tui/fuzzy.py`
      + tests. Gate: `pytest packages/tui/fuzzy`.
- [x] **1.3 keys** — `packages/tui/src/{keys,keybindings}.ts` →
      `packages/tui/keys/src/cortex/tui/keys.py` + tests. Gate: `pytest packages/tui/keys`.
- [x] **1.4 terminal** — `packages/tui/src/{terminal,stdin-buffer}.ts` →
      `packages/tui/terminal/src/cortex/tui/terminal.py` + tests. Gate: `pytest packages/tui/terminal`.
- [x] **1.5 render** — `packages/tui/src/tui.ts` → `packages/tui/render/src/cortex/tui/render.py`
      (differential renderer) + ANSI snapshot tests. Gate: `pytest packages/tui/render`.
- [x] **1.6 editing** — `packages/tui/src/{editor-component,kill-ring,undo-stack}.ts` →
      `packages/tui/editing/src/cortex/tui/editing/` + tests. Gate: `pytest packages/tui/editing`.
- [x] **1.7 components** — `packages/tui/src/components/*.ts` →
      `packages/tui/components/src/cortex/tui/components/` + snapshot tests. Gate:
      `pytest packages/tui/components`.
- [x] **1.8 tui umbrella publishable** — leaf READMEs, pyright strict on every tui leaf,
      flip `_meta/publish = true` for all T0 leaves, run `uv build --all-packages`.
      Gate: green release dry-run (`publish_packages.py --dry-run`).

---

## Phase 2 — `cortex.ai` leaves

Parallelizable except where leaf deps require ordering (types/util/models → stream →
providers; faux first).

- [x] **2.1 types** — `packages/ai/src/types.ts` →
      `packages/ai/types/src/cortex/ai/types.py` (pydantic models) + tests. Gate:
      `pytest packages/ai/types`.
- [x] **2.2 util** — `packages/ai/src/utils/*` → `packages/ai/util/src/cortex/ai/util/`
      (json repair, validation, overflow, hash, headers) + tests. Gate:
      `pytest packages/ai/util`.
- [x] **2.3 models** — `packages/ai/src/{api-registry,models.generated,image-models,models}.ts` →
      `packages/ai/models/src/cortex/ai/models/` + tests. Gate:
      `pytest packages/ai/models`.
- [x] **2.4 env-api-keys** — `packages/ai/src/env-api-keys.ts` →
      `packages/ai/env/src/cortex/ai/env.py` + tests. Gate: `pytest packages/ai/env`.
- [x] **2.5 stream** — `packages/ai/src/stream.ts` →
      `packages/ai/stream/src/cortex/ai/stream.py` + tests against faux. Gate:
      `pytest packages/ai/stream`.
- [x] **2.6 provider-faux** — `packages/ai/src/providers/faux.ts` →
      `packages/ai/provider-faux/src/cortex/ai/providers/faux.py` + tests. Blocks all
      downstream testing — prioritize. Gate: `pytest packages/ai/provider-faux`.
- [ ] **2.11 sanitize-unicode** — `packages/ai/src/utils/sanitize-unicode.ts` →
      `packages/ai/util/src/cortex/ai/util/sanitize_unicode.py` (`sanitize_surrogates`) +
      tests. Shared by anthropic/openai/google providers. Gate: `pytest packages/ai/util`.
- [ ] **2.12 provider-common** — shared provider helpers →
      new leaf `cortexcode-ai-provider-common` (`packages/ai/provider-common/`,
      `cortex.ai.providers._common`). Ports `providers/cache-retention.ts`
      (`resolve_cache_retention`), `providers/simple-options.ts` (`build_base_options`,
      `adjust_max_tokens_for_thinking`), `providers/transform-messages.ts`
      (`transform_messages`), `providers/github-copilot-headers.ts`
      (`build_copilot_dynamic_headers`, `has_copilot_vision_input`) + tests. Every
      `provider-*` depends on it. Gate: `pytest packages/ai/provider-common`.
- [ ] **2.7 provider-anthropic** — `packages/ai/src/providers/anthropic.ts` →
      `packages/ai/provider-anthropic/src/cortex/ai/providers/anthropic.py` + fixture
      tests. Depends on 2.11 + 2.12. Gate: `pytest packages/ai/provider-anthropic`.
- [ ] **2.8 provider-openai** — `packages/ai/src/providers/openai-*.ts` →
      `packages/ai/provider-openai/src/cortex/ai/providers/openai/` + tests. Gate:
      `pytest packages/ai/provider-openai`.
- [ ] **2.9 provider-google** — `packages/ai/src/providers/google*.ts` →
      `packages/ai/provider-google/src/cortex/ai/providers/google/` + tests. Gate:
      `pytest packages/ai/provider-google`.
- [x] **2.10 ai umbrella publishable** — leaf READMEs, strict types, flip T0/T1 leaves to
      `publish=true`. Gate: dry-run clean.

---

## Phase 3 — `cortex.agent` leaves

- [ ] **3.1 types** — `packages/agent/src/types.ts` →
      `packages/agent/types/src/cortex/agent/types.py` + tests. Gate:
      `pytest packages/agent/types`.
- [ ] **3.2 loop** — `packages/agent/src/agent-loop.ts` →
      `packages/agent/loop/src/cortex/agent/loop.py` + tests. Gate:
      `pytest packages/agent/loop`.
- [ ] **3.3 agent** — `packages/agent/src/agent.ts` →
      `packages/agent/agent/src/cortex/agent/agent.py` + tests. Gate:
      `pytest packages/agent/agent`.
- [ ] **3.4 harness** — `packages/agent/src/harness/{messages,system-prompt,prompt-templates,skills,types,agent-harness}.ts` →
      `packages/agent/harness/src/cortex/agent/harness/` + tests. Gate:
      `pytest packages/agent/harness`.
- [ ] **3.5 session** — `packages/agent/src/harness/session/*` + `execution-env*` →
      `packages/agent/session/src/cortex/agent/session/` + tests. Gate:
      `pytest packages/agent/session`.
- [ ] **3.6 compaction** — `packages/agent/src/harness/compaction/*` →
      `packages/agent/compaction/src/cortex/agent/compaction/` + tests. Gate:
      `pytest packages/agent/compaction`.
- [ ] **3.7 tools** — `packages/agent/src/tools/default-tools.ts` →
      `packages/agent/tools/src/cortex/agent/tools/` + tests. Gate:
      `pytest packages/agent/tools`.
- [ ] **3.8 mcp** — `packages/agent/src/tools/mcp-*.ts` →
      `packages/agent/mcp/src/cortex/agent/mcp.py` + tests. Gate:
      `pytest packages/agent/mcp`.
- [ ] **3.9 agent umbrella publishable** — READMEs, strict, flip T0/T1 leaves to
      `publish=true`. Gate: dry-run clean.

---

## Phase 4 — `cortex.code` core (T2)

Coarser leaves until the code stabilizes.

- [ ] **4.1 config** — `packages/coding-agent/src/config.ts`, `core/settings-*` →
      `packages/code/config/src/cortex/code/config/` + tests.
- [ ] **4.2 tools** — `packages/coding-agent/src/core/tools/{read,bash,edit,write,grep,find,ls}.ts`
      → `packages/code/tools/src/cortex/code/tools/` + tests.
- [ ] **4.3 session** — `packages/coding-agent/src/core/agent-session*.ts`,
      `session-manager.ts` → `packages/code/session/src/cortex/code/session/` + tests.
- [ ] **4.4 prompts** — `packages/coding-agent/src/core/{system-prompt,mode-prompts,prompt-templates}.ts`
      → `packages/code/prompts/src/cortex/code/prompts/` + tests.
- [ ] **4.5 print** — `packages/coding-agent/src/modes/print-mode.ts` →
      `packages/code/print/src/cortex/code/print.py` + e2e test.
- [ ] **4.6 main** — `packages/coding-agent/src/main.ts`, `cli/args.ts` →
      `packages/code/main/src/cortex/code/main.py` + entry point; first runnable CLI.

## Phase 5 — `cortex.code` full (T3)

- [ ] **5.1 rpc** — `packages/coding-agent/src/modes/rpc-mode.ts` →
      `packages/code/rpc/src/cortex/code/rpc.py` + protocol tests.
- [ ] **5.2 interactive** — `packages/coding-agent/src/modes/interactive/**` →
      `packages/code/interactive/src/cortex/code/interactive/` + e2e pty tests.
- [ ] **5.3 resources** — `packages/coding-agent/src/core/{skills,resource-loader}.ts` →
      `packages/code/resources/src/cortex/code/resources/`.
- [ ] **5.4 subagents** — `packages/coding-agent/src/core/subagent*.ts`,
      `core/tools/subagent.ts` → `packages/code/subagents/src/cortex/code/subagents/`.
- [ ] **5.5 extensions** — port semantics of `packages/coding-agent/src/core/extensions/**`
      into a Python plugin API via importlib; document redesign.
- [ ] **5.6 cli umbrella publishable** — flip T2 leaves `publish=true`, release first
      public `cortexcode-cli` with `cortex` command.

## Phase 6 — Cutover

- [ ] **6.1 parity checklist** — run hoocode and cortex side-by-side on scripted print
      scenarios; diff transcripts.
- [ ] **6.2 docs port** — design docs rewritten for Python leaves.
- [ ] **6.3 release 0.1.0 umbrella train** — lockstep umbrella bumps + all published
      leaves via `release.yml`.

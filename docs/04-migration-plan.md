# Migration Plan — Executable Checklist

**Status:** DRAFT — do not execute until reviewed.

This file is the **single source of truth** for migration progress. It is designed to be
machine-driven: the `migrate-next` command (see
[05-skills-and-commands.md](05-skills-and-commands.md)) finds the first unchecked step,
executes it, runs the gates, and checks the box in the same commit.

**Source repo:** https://github.com/kolisachint/hoocode — the driver resolves it via a
local clone (default `~/github/hoocode`, override with `CORTEX_MIGRATION_SRC`); if
absent it clones from GitHub. Source paths below are relative to that repo root.

**Step contract — every step must:**

- be completable in one focused session (≤ ~500 LOC ported);
- name its source files in the hoocode repo and target files here;
- end with all gates green: `uv run pytest` (whole workspace) + `ruff check` + `pyright`
  on touched packages;
- be committed atomically as `migrate: <step-id> <title>`.

Legend: `[ ]` pending · `[x]` done. Steps within a phase are ordered; phases 2 and 3 may
run in parallel (no cross-deps).

---

## Phase 0 — Workspace bootstrap

- [x] **0.1 Root workspace** — root `pyproject.toml` (uv workspace, ruff, pyright,
      pytest config), `.gitignore`, `.python-version` (3.11), empty
      `packages/{tui,ai,agent,coding-agent}` members with placeholder `pyproject.toml`
      (`publish=false`), `uv sync` green. Gate: `uv run pytest` (collects 0), `ruff`, CI-less.
- [ ] **0.2 CI** — `.github/workflows/ci.yml` per doc 03. Gate: green run on a PR.
- [ ] **0.3 Name check + reservation** — verify `cortexcode-*` availability on PyPI;
      publish `0.0.1` placeholders using `PYPI_TOKEN` (manual dispatch of a one-off
      workflow) or record fallback names in doc 02. Gate: names secured.
- [ ] **0.4 Release plumbing** — `scripts/bump_versions.py`, `scripts/release.py`,
      `release.yml`, `merge-release.yml`. Gate: `release.yml` dry-run (publish step
      `--dry-run`) green.
- [ ] **0.5 Migration driver** — `scripts/migrate_next.py` + `.hoocode/skills/` per
      doc 05. Gate: driver correctly reports "next step = 1.1".

## Phase 1 — cortex.tui foundations (T0, source: packages/tui)

- [ ] **1.1 utils + fuzzy** — `utils.ts`, `fuzzy.ts` → `utils.py`, `fuzzy.py` + ports of
      their tests (text width via `wcwidth`-equivalent logic ported from
      `get-east-asian-width` usage).
- [ ] **1.2 ansi helper** — replace chalk usage → `ansi.py` (style/color codes).
- [ ] **1.3 keys** — `keys.ts` → `keys.py` (key parsing incl. Kitty protocol) + tests.
- [ ] **1.4 keybindings** — `keybindings.ts` → `keybindings.py` + tests.
- [ ] **1.5 terminal** — `terminal.ts` → `terminal.py` (raw mode via termios, SIGWINCH,
      write batching). Tests via pseudo-tty (`pty` stdlib).
- [ ] **1.6 stdin buffer** — `stdin-buffer.ts` → `stdin_buffer.py` (asyncio) + tests.
- [ ] **1.7 renderer core** — `tui.ts` differential renderer → `tui.py`. Golden ANSI
      snapshot tests.
- [ ] **1.8 basic components** — `text`, `spacer`, `box`, `loader` → `components/` + snapshot tests.
- [ ] **1.9 input + autocomplete** — `input.ts`, `autocomplete.ts` + tests.
- [ ] **1.10 select-list** — `select-list.ts` (uses fuzzy) + tests.
- [ ] **1.11 editor** — `editor.ts`, `editor-component.ts`, `kill-ring.ts`,
      `undo-stack.ts` + tests. (Largest single step; may split on execution.)
- [ ] **1.12 markdown component** — `markdown.ts` via mistune + snapshot tests.
- [ ] **1.13 tui publishable** — README, pyright strict, coverage check → flip
      `publish=true`. Gate: `uv build --package cortexcode-tui` clean.

## Phase 2 — cortex.ai (T0/T1, source: packages/ai) — parallel with Phase 1

- [ ] **2.1 types** — `types.ts` → `types.py` (pydantic v2 models for messages, tools,
      stream events, usage/cost) + tests.
- [ ] **2.2 utils** — `utils/` (partial-json parsing, validation, overflow) + tests.
- [ ] **2.3 registry + models** — `api-registry.ts`, `models.ts`,
      `models.generated.ts` → `registry.py`, `models.py`, `models_generated.py`, plus
      `scripts/generate_models.py` (reuses hoocode's source data). Tests.
- [ ] **2.4 env api keys** — `env-api-keys.ts` → `env_api_keys.py` + tests.
- [ ] **2.5 stream core** — `stream.ts` → `stream.py` (async iterator event stream,
      `stream()` / `complete()`) + tests against faux.
- [ ] **2.6 faux provider** — `providers/faux.ts` → `providers/faux.py` (scriptable test
      provider) + tests. **Blocks all downstream testing — prioritize.**
- [ ] **2.7 anthropic provider** — `providers/anthropic.ts` → httpx-based
      `providers/anthropic.py` + recorded-fixture tests.
- [ ] **2.8 openai providers** — `openai-completions.ts`, `openai-responses.ts` + tests.
- [ ] **2.9 google provider** — `providers/google.ts` + tests.
- [ ] **2.10 oauth (minimal)** — `oauth.ts` for anthropic/openai subscription flows +
      tests. (Defer other providers.)
- [ ] **2.11 ai publishable** — README, strict types, flip `publish=true`.

## Phase 3 — cortex.agent (T1, source: packages/agent) — after 2.6

- [ ] **3.1 types** — `types.ts` → `types.py` (AgentState, AgentMessage, AgentTool,
      events) + tests.
- [ ] **3.2 agent loop** — `agent-loop.ts` → `loop.py` (turn loop, tool dispatch,
      background tools) + tests on faux provider.
- [ ] **3.3 Agent class** — `agent.ts` → `agent.py` (event emitter, state, steering) + tests.
- [ ] **3.4 harness: messages + system prompt** — `harness/messages.ts`,
      `system-prompt.ts` → `harness/` + tests.
- [ ] **3.5 harness: sessions** — `harness/session/{jsonl,memory,shared,session}.ts` →
      `harness/session/` + tests (memory repo first).
- [ ] **3.6 harness: compaction** — `harness/compaction/` (token-aware pruning, branch
      summarization) + tests.
- [ ] **3.7 default tools + skills loader** — `tools/default-tools.ts`,
      `harness/skills.ts` + tests.
- [ ] **3.8 MCP tools** — `tools/mcp-tools.ts` etc. via official `mcp` SDK + tests
      against a local stdio test server.
- [ ] **3.9 agent-core publishable** — README, strict types, flip `publish=true`.

## Phase 4 — cortex.code core (T2, source: packages/coding-agent) — after 3.x

- [ ] **4.1 config + settings** — `config.ts`, `core/settings-{types,defaults,manager}.ts`
      → `config.py`, `settings/` + tests. Config dir: `~/.cortex/`, project `.cortex/`.
- [ ] **4.2 tools: read/ls** — `core/tools/{read,ls}.ts` + truncation logic + tests.
- [ ] **4.3 tools: grep/find** — `core/tools/{grep,find}.ts` (ripgrep subprocess w/
      pure-python fallback, pathspec gitignore) + tests.
- [ ] **4.4 tools: bash** — `core/bash-executor.ts`, `core/tools/bash.ts` (streaming,
      timeout, truncation) + tests.
- [ ] **4.5 tools: edit/write** — `core/tools/{edit,write}.ts` (exact-match multi-edit
      semantics, diff rendering via difflib) + tests — **port the TS edit test suite
      fully; this is the highest-risk correctness surface.**
- [ ] **4.6 permission gate** — permission/approval flow (Yes/No/Always) from
      `core/` + tests.
- [ ] **4.7 session layer** — `core/agent-session*.ts`, `session-manager.ts` (on top of
      3.5) + tests.
- [ ] **4.8 system prompt + modes** — `core/{system-prompt,mode-prompts,prompt-templates}.ts`
      (ask/plan/build/debug) + tests.
- [ ] **4.9 resources + skills** — `core/{resource-loader,skills}.ts` (.cortex/skills,
      AGENTS.md context files) + tests.
- [ ] **4.10 print mode + CLI entry** — `main.ts`, `cli/args.ts`, `modes/print-mode.ts`
      → `main.py`, `cli/`, `modes/print.py` + e2e test (`cortex -p "…" ` on faux).
      **First runnable milestone.**
- [ ] **4.11 model registry/resolver** — `core/{model-registry,model-resolver}.ts`,
      `auth-storage.ts` + tests.

## Phase 5 — cortex.code full (T3) — after 4.x

- [ ] **5.1 rpc mode** — `modes/rpc-mode.ts` + protocol tests.
- [ ] **5.2 interactive mode shell** — `modes/interactive/interactive-mode.ts` minimal:
      editor input, streaming render, permission prompts. e2e pty test.
- [ ] **5.3 interactive components** — assistant/user message, tool-execution, footer,
      selectors + snapshot tests.
- [ ] **5.4 subagents** — `core/subagent*.ts`, `core/tools/subagent.ts`, task-store,
      task-panel + tests.
- [ ] **5.5 extensions (Python plugin API)** — `core/extensions/` semantics via
      importlib entry points + tests. (API redesign documented in its own doc before
      implementation.)
- [ ] **5.6 hoo-core built-in extension** — `extensions/core/hoo-core.ts` port (modes,
      default skills) + tests.
- [ ] **5.7 coding-agent publishable** — README, e2e suite, flip `publish=true` →
      first `cortexcode` release with CLI `cortex`.

## Phase 6 — Cutover

- [ ] **6.1 parity checklist** — run hoocode and cortex side-by-side on a scripted
      scenario set (print mode); diff transcripts; document accepted gaps.
- [ ] **6.2 docs port** — bring over relevant design docs (`package-map`, `product`,
      `loop-and-plugin-system`) rewritten for Python.
- [ ] **6.3 release 0.1.0** — lockstep 0.1.0 of all four packages via `release.yml`.

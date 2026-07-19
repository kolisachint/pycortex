# Target Architecture — pycortex

**Status:** DRAFT

## 1. Naming

`pycortex` is **taken on PyPI** (neuroimaging). Proposal — one shared import namespace,
distinct distribution names:

| hoocode package | PyPI distribution | Import | Tier |
| --- | --- | --- | --- |
| tui | `cortexcode-tui` | `cortex.tui` | T0 |
| ai | `cortexcode-ai` | `cortex.ai` | T0/T1 |
| agent | `cortexcode-agent-core` | `cortex.agent` | T1 |
| coding-agent | `cortexcode-cli` (CLI: `cortex`) | `cortex.code` | T2/T3 |

`cortex.*` uses PEP 420 namespace packages so each distribution owns one subpackage.
**Resolved 2026-07-19:** `cortexcode` was squatted on PyPI (an unrelated code-indexing
package), so the CLI distribution is **`cortexcode-cli`**. The three lib names were
free and were reserved as-is at 0.0.1.

## 2. Repository layout (uv workspace)

```
pycortex/
├── pyproject.toml            # workspace root: uv, ruff, pyright, pytest config
├── uv.lock
├── packages/
│   ├── tui/
│   │   ├── pyproject.toml    # name=cortexcode-tui, own version, own deps
│   │   ├── src/cortex/tui/   # namespace package (no __init__.py at cortex/)
│   │   └── tests/
│   ├── ai/
│   │   ├── pyproject.toml
│   │   ├── src/cortex/ai/
│   │   └── tests/
│   ├── agent/
│   │   ├── pyproject.toml
│   │   ├── src/cortex/agent/
│   │   └── tests/
│   └── coding-agent/
│       ├── pyproject.toml    # entry point: cortex = cortex.code.main:main
│       ├── src/cortex/code/
│       └── tests/
├── scripts/
│   ├── bump_versions.py      # lockstep bump (port of bump-versions.mjs)
│   ├── release.py            # bump → tag → push (CI does the publish)
│   └── migrate_next.py       # executes next step of 04-migration-plan.md
├── docs/                     # these design docs + ported design notes
├── .hoocode/skills/          # migration skills (05-skills-and-commands.md)
└── .github/workflows/        # ci.yml, release.yml, merge-release.yml
```

Rationale:

- **uv workspaces** mirror npm workspaces: one lockfile, editable path deps between
  members, per-package publishing.
- **src layout** ensures tests run against installed/importable code, not the cwd.
- **Lockstep versions** initially (like hoocode's 0.4.x for all four). Revisit
  independent versioning after Phase 1.

## 3. Package designs (module mapping)

### 3.1 `cortex.tui` (from packages/tui — ~11.4K LOC TS)

| TS | Py module | Notes |
| --- | --- | --- |
| `tui.ts` | `tui.py` | container + differential renderer; the hard part |
| `terminal.ts` | `terminal.py` | `termios`/`tty` raw mode, resize via `signal.SIGWINCH` |
| `keys.ts`, `keybindings.ts` | `keys.py`, `keybindings.py` | Kitty protocol parsing ports directly |
| `components/*` | `components/*.py` | text, input, editor, markdown, select_list, loader, image, box, spacer |
| `stdin-buffer.ts` | `stdin_buffer.py` | asyncio reader |
| `fuzzy.ts`, `utils.ts`, `kill-ring.ts`, `undo-stack.ts` | same names | pure logic, mechanical port |
| chalk usage | `ansi.py` | tiny internal ANSI helper |

Zero runtime deps beyond stdlib + mistune. Phase 2 ports the *minimal viable subset*
(renderer, terminal, keys, text/input/select components); editor/image/markdown follow.

### 3.2 `cortex.ai` (from packages/ai — ~27.7K LOC TS)

| TS | Py module |
| --- | --- |
| `types.ts` | `types.py` (pydantic models: `Model`, `Context`, `Tool`, stream events) |
| `stream.ts` | `stream.py` (async generators for streaming) |
| `api-registry.ts`, `models.generated.ts` | `registry.py`, `models_generated.py` (+ `scripts/generate_models.py`) |
| `providers/*.ts` | `providers/{anthropic,openai_responses,openai_completions,google,faux}.py` — Phase 3 ports these five; the long tail (bedrock, mistral, vertex, azure…) is post-migration |
| `env-api-keys.ts` | `env_api_keys.py` |
| `oauth.ts` | `oauth.py` (Phase 3b — needed for subscription auth only) |
| `images*.ts` | deferred post-migration |
| `utils/*` | `utils/` (json repair/partial parse via `partial-json` equivalents; port logic directly) |

Deps: `httpx`, `pydantic`. The **`faux` provider is ported first** — it powers all
downstream tests without keys.

### 3.3 `cortex.agent` (from packages/agent — ~9.5K LOC TS)

| TS | Py module |
| --- | --- |
| `agent.ts` | `agent.py` (event-emitting Agent class) |
| `agent-loop.ts` | `loop.py` (turn loop, tool dispatch, background tools) |
| `types.ts` | `types.py` |
| `harness/` | `harness/` (session jsonl/memory repos, compaction, messages, system-prompt, skills) |
| `tools/default-tools.ts` | `tools/defaults.py` |
| `tools/mcp-*.ts` | `tools/mcp.py` (official `mcp` SDK) |
| `proxy.ts` | deferred |

Deps: `cortexcode-ai`, `mcp`, `pyyaml`, `pathspec`.

### 3.4 `cortex.code` (from packages/coding-agent — ~71.5K LOC TS)

Ported in slices, volatile-last:

| Slice | TS area | Py area |
| --- | --- | --- |
| A. config + settings | `config.ts`, `core/settings-*` | `config.py`, `settings/` |
| B. core tools | `core/tools/{read,bash,edit,write,grep,find,ls}.ts` | `tools/` |
| C. session | `core/agent-session*.ts`, `session-manager.ts` | `session/` |
| D. system prompt + modes | `core/{system-prompt,mode-prompts,prompt-templates}.ts` | `prompts/` |
| E. print mode + CLI | `main.ts`, `cli/`, `modes/print-mode.ts` | `main.py`, `cli/`, `modes/print.py` |
| F. rpc mode | `modes/rpc-mode.ts` | `modes/rpc.py` |
| G. skills + resources | `core/{skills,resource-loader}.ts` | `resources/` |
| H. interactive mode | `modes/interactive/**` | `modes/interactive/` |
| I. subagents | `core/subagent*.ts`, `core/tools/subagent.ts` | `subagents/` |
| J. extensions | `core/extensions/**` | `extensions/` (Python-module plugins via importlib; **API redesigned for Python — port semantics, not the jiti mechanism**) |

Deps: siblings + `pyyaml`, `pathspec`, `Pillow` (later slices).

Not ported in Phase 1 (explicit non-goals): web-ui, mini-lit, voice transcribe, HTML
export, clipboard-native FFI, image tooling beyond read-tool image support, the
extension marketplace.

## 4. Toolchain

| Concern | Tool | Config |
| --- | --- | --- |
| Package/workspace | **uv** | root `pyproject.toml` `[tool.uv.workspace]` |
| Lint + format | **ruff** | root config, inherited |
| Types | **pyright** (strict on T0/T1 packages, basic on T2 initially) | root |
| Tests | **pytest** + pytest-asyncio | per-package `tests/` |
| Python floor | **3.11** (asyncio.TaskGroup, tomllib, ExceptionGroup) | |
| Build backend | **hatchling** | per-package |

## 5. Testing strategy

- Port TS test files alongside each module (hoocode has per-package `test/` dirs).
- The `faux` provider (ai) + in-memory session repo (agent) make the whole stack testable
  offline — same trick hoocode uses.
- Golden-output tests for the TUI renderer: feed component trees, snapshot ANSI output
  (hoocode uses xterm-based tests; we snapshot instead).
- Every migration step's gate: `uv run pytest` green across the workspace + `ruff check`
  + `pyright` clean on touched packages.

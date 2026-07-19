# Target Architecture — pycortex

**Status:** DRAFT

## 1. Naming & the ultramodular model

`pycortex` is **taken on PyPI** (neuroimaging). We use **one shared import namespace**
(`cortex.*`, PEP 420) but explode each hoocode package into **many independently
versioned leaf distributions**, grouped by *co-change*: files that change together live
in one leaf; files that evolve independently live in separate leaves. The four names
already reserved on PyPI (`cortexcode-tui`, `cortexcode-ai`, `cortexcode-agent-core`,
`cortexcode-cli`) become **umbrella meta-packages** — they carry no code, only pinned
dependencies on their leaves, so `pip install cortexcode-tui` still installs the whole
TUI and `import cortex.tui` still works.

**Principle (ultramodular):** a leaf is the smallest unit with a single reason to
change. Churn stays contained — a fix to the renderer never forces a version bump of the
key-parser. Each leaf: one responsibility, its own `pyproject.toml`, own version, own
tests, minimal deps. Stable leaves publish first; volatile leaves stay `publish=false`.

### Distribution map

| Umbrella (reserved) | Import namespace | Leaf distributions |
| --- | --- | --- |
| `cortexcode-tui` | `cortex.tui.*` | `cortexcode-tui-util`, `-keys`, `-terminal`, `-render`, `-editing`, `-components` |
| `cortexcode-ai` | `cortex.ai.*` | `cortexcode-ai-types`, `-util`, `-models`, `-stream`, `-provider-faux`, `-provider-anthropic`, `-provider-openai`, `-provider-google`, `-oauth`, `-images` |
| `cortexcode-agent-core` | `cortex.agent.*` | `cortexcode-agent-types`, `-loop`, `-harness`, `-session`, `-compaction`, `-tools`, `-mcp` |
| `cortexcode-cli` | `cortex.code.*` | sliced later (phases 4–5); T2/T3 stays in-repo longer, published as fewer, coarser leaves |

Each leaf owns a distinct subpackage under its namespace (e.g. `cortex.tui.util`,
`cortex.tui.render`) so there is never a file-ownership collision across distributions.
**Resolved 2026-07-19:** `cortexcode` was squatted on PyPI, so the CLI umbrella is
**`cortexcode-cli`**; the three lib umbrellas were free and reserved at 0.0.1 alongside
their first leaves.

## 2. Repository layout (uv workspace)

Every leaf **and** every umbrella is a workspace member under `packages/`. Leaves are
grouped in a directory per umbrella for humans; uv flattens them via `packages/*/*`.

```
pycortex/
├── pyproject.toml            # workspace root: members = packages/*, packages/*/*
├── uv.lock
├── packages/
│   ├── tui/
│   │   ├── _meta/            # umbrella: name=cortexcode-tui, deps on leaves, NO code
│   │   │   └── pyproject.toml
│   │   ├── util/            # leaf: name=cortexcode-tui-util
│   │   │   ├── pyproject.toml
│   │   │   ├── src/cortex/tui/util/   # PEP 420 namespace, owns this subpackage only
│   │   │   └── tests/
│   │   ├── keys/           # cortexcode-tui-keys  -> cortex.tui.keys
│   │   ├── terminal/       # cortexcode-tui-terminal -> cortex.tui.terminal
│   │   ├── render/         # cortexcode-tui-render   -> cortex.tui.render
│   │   ├── editing/        # cortexcode-tui-editing  -> cortex.tui.editing
│   │   └── components/     # cortexcode-tui-components -> cortex.tui.components
│   ├── ai/   (_meta + leaves: types, util, models, stream, provider-*, oauth, images)
│   ├── agent/(_meta + leaves: types, loop, harness, session, compaction, tools, mcp)
│   └── code/ (_meta + coarser leaves, sliced in phases 4–5)
├── scripts/
│   ├── bump_versions.py      # per-leaf independent bump + sibling re-pin
│   ├── release.py            # bump → tag → push (CI publishes changed leaves)
│   ├── publish_packages.py   # publish every publish=true leaf whose version is new
│   └── migrate_next.py       # executes next step of 04-migration-plan.md
├── docs/                     # these design docs + ported design notes
├── .hoocode/skills/          # migration skills (05-skills-and-commands.md)
└── .github/workflows/        # ci.yml, release.yml, merge-release.yml
```

Rationale:

- **uv workspaces** give one lockfile, editable path deps between leaves, per-leaf
  publishing. Leaves depend on sibling leaves by distribution name; uv resolves them
  from the workspace locally and from PyPI once published.
- **src layout** ensures tests run against installed/importable code, not the cwd.
- **PEP 420 namespaces**: no `__init__.py` at `cortex/` or `cortex/tui/`; each leaf
  ships only its own deepest subpackage (`cortex/tui/util/__init__.py`), so two
  distributions never write the same file.
- **Per-leaf versioning.** Leaves version independently (a leaf bumps only when its own
  code changes); each umbrella re-pins to its leaves' new versions and bumps too. This
  is the whole point of ultramodular: contained churn, minimal release blast radius.

## 3. Ultramodular leaf map

A "leaf" is the smallest unit that can version, test, and publish independently.
Umbrellas group leaves by import namespace and install path.

### 3.1 `cortex.tui` umbrella (`cortexcode-tui`)

| Leaf dist | Import | Owns (from TS) | Stability |
| --- | --- | --- | --- |
| `cortexcode-tui-util` | `cortex.tui.util` | `utils.ts` (text width, ANSI wrap/truncate, graphemes) | T0 |
| `cortexcode-tui-fuzzy` | `cortex.tui.fuzzy` | `fuzzy.ts` | T0 |
| `cortexcode-tui-keys` | `cortex.tui.keys` | `keys.ts`, `keybindings.ts` | T0 |
| `cortexcode-tui-terminal` | `cortex.tui.terminal` | `terminal.ts`, `stdin-buffer.ts` | T0 |
| `cortexcode-tui-render` | `cortex.tui.render` | `tui.ts` (differential renderer) | T0 |
| `cortexcode-tui-editing` | `cortex.tui.editing` | `editor-component.ts`, `kill-ring.ts`, `undo-stack.ts` | T0 |
| `cortexcode-tui-components` | `cortex.tui.components` | `components/*.ts` (text, box, spacer, loader, input, select-list, autocomplete, editor, markdown, image) | T0 |
| `cortexcode-tui-images` | `cortex.tui.images` | `terminal-image.ts` | T1 (post-core) |

All leaves depend only on lower / same-tier tui leaves (components → editing → render
→ keys → terminal → util). Fuzzy is a shared leaf used by components.

### 3.2 `cortex.ai` umbrella (`cortexcode-ai`)

| Leaf dist | Import | Owns (from TS) | Stability |
| --- | --- | --- | --- |
| `cortexcode-ai-types` | `cortex.ai.types` | `types.ts` (pydantic models, events, tools) | T0 |
| `cortexcode-ai-util` | `cortex.ai.util` | `utils/*` (json repair, validation, overflow, diagnostics, hash, headers) | T0 |
| `cortexcode-ai-models` | `cortex.ai.models` | `api-registry.ts`, `models.generated.ts`, `image-models*.ts` | T0/T1 |
| `cortexcode-ai-stream` | `cortex.ai.stream` | `stream.ts` | T0 |
| `cortexcode-ai-provider-faux` | `cortex.ai.providers.faux` | `providers/faux.ts` | T0 — **port first** |
| `cortexcode-ai-provider-anthropic` | `cortex.ai.providers.anthropic` | `providers/anthropic.ts` | T1 |
| `cortexcode-ai-provider-openai` | `cortex.ai.providers.openai` | `providers/openai-completions.ts`, `providers/openai-responses.ts`, `providers/openai-responses-shared.ts`, `providers/openai-codex-responses.ts` | T1 |
| `cortexcode-ai-provider-google` | `cortex.ai.providers.google` | `providers/google.ts`, `providers/google-shared.ts`, `providers/google-vertex.ts` | T1 |
| `cortexcode-ai-provider-azure` | `cortex.ai.providers.azure` | `providers/azure-openai-responses.ts` | T2 (long tail) |
| `cortexcode-ai-oauth` | `cortex.ai.oauth` | `oauth.ts`, `utils/oauth/*` | T2 |
| `cortexcode-ai-images` | `cortex.ai.images` | `images*.ts`, `providers/images/*` | T2 |

Leaf deps:

- `types` has no leaf deps.
- `util` depends on `types`.
- `models` depends on `types`.
- `stream` depends on `types`, `models`, `util`, `env-api-keys` (add a tiny `cortexcode-ai-env` leaf if it turns out to need no ai types; otherwise it sits in `stream`).
- every `provider-*` depends on `stream`, `types`, `util`, `models`.
- `faux` has the fewest deps and is ported before any provider that consumes it.

All ai leaves use `httpx` + `pydantic`.

### 3.3 `cortex.agent` umbrella (`cortexcode-agent-core`)

| Leaf dist | Import | Owns (from TS) | Stability |
| --- | --- | --- | --- |
| `cortexcode-agent-types` | `cortex.agent.types` | `types.ts` | T0 |
| `cortexcode-agent-loop` | `cortex.agent.loop` | `agent-loop.ts` | T0 |
| `cortexcode-agent` | `cortex.agent.agent` | `agent.ts` (the Agent class) | T0 |
| `cortexcode-agent-harness` | `cortex.agent.harness` | `harness/{messages,system-prompt,prompt-templates,skills,types,agent-harness}` | T1 |
| `cortexcode-agent-session` | `cortex.agent.session` | `harness/session/*`, `harness/execution-env*` | T1 |
| `cortexcode-agent-compaction` | `cortex.agent.compaction` | `harness/compaction/*` | T1 |
| `cortexcode-agent-tools` | `cortex.agent.tools` | `tools/default-tools.ts` | T1 |
| `cortexcode-agent-mcp` | `cortex.agent.mcp` | `tools/mcp-*.ts` | T2 |

Leaf deps: `loop`/`agent`/`tools` depend on `types`; `harness` depends on `agent`,
`loop`, `types`; `session` depends on `harness`; `compaction` depends on `session`;
`mcp` depends on `tools` and `session`.

Runtime deps include `cortexcode-ai`, `mcp`, `pyyaml`, `pathspec`.

### 3.4 `cortex.code` umbrella (`cortexcode-cli`)

The coding-agent source is too volatile to split finely during migration. We slice it
into coarser leaves, each published only when stable:

| Leaf dist | Import | Owns (from TS) | Stability |
| --- | --- | --- | --- |
| `cortexcode-cli-config` | `cortex.code.config` | `config.ts`, `core/settings-*` | T2 |
| `cortexcode-cli-tools` | `cortex.code.tools` | `core/tools/{read,bash,edit,write,grep,find,ls}` | T2 |
| `cortexcode-cli-session` | `cortex.code.session` | `core/agent-session*.ts`, `session-manager.ts` | T2 |
| `cortexcode-cli-prompts` | `cortex.code.prompts` | `core/{system-prompt,mode-prompts,prompt-templates}` | T2 |
| `cortexcode-cli-print` | `cortex.code.print` | `modes/print-mode.ts` | T2 |
| `cortexcode-cli-rpc` | `cortex.code.rpc` | `modes/rpc-mode.ts` | T3 |
| `cortexcode-cli-resources` | `cortex.code.resources` | `core/{skills,resource-loader}` | T3 |
| `cortexcode-cli-interactive` | `cortex.code.interactive` | `modes/interactive/**` | T3 |
| `cortexcode-cli-subagents` | `cortex.code.subagents` | `core/subagent*.ts`, `core/tools/subagent.ts` | T3 |
| `cortexcode-cli-extensions` | `cortex.code.extensions` | `core/extensions/**` — port semantics only, Python plugin API redesigned | T3 |
| `cortexcode-cli-main` | `cortex.code.main` | `main.ts`, `cli/args.ts` | T2 |

The umbrella `cortexcode-cli` also provides the console script `cortex =
cortex.code.main:main`. CLI deps: sibling agent/ai/tui leaves + `pyyaml`, `pathspec`,
`Pillow` (later slices).

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

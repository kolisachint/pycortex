# Source Analysis — hoocode

**Status:** DRAFT
Source repo: **https://github.com/kolisachint/hoocode** (public; clone if no local copy:
`git clone https://github.com/kolisachint/hoocode ~/github/hoocode`).
Data gathered from the local clone at `~/github/hoocode` on 2026-07-19 (828 commits,
branch `main`; churn windows noted per table).

## 1. Package inventory

npm workspaces, four library packages, lockstep-versioned at **0.4.146**:

| Package | npm name | LOC (src, .ts) | Responsibility |
| --- | --- | ---: | --- |
| `packages/tui` | `@kolisachint/hoocode-tui` | ~11,400 | Terminal UI library: differential renderer, components (editor, select-list, markdown, image), keybindings |
| `packages/ai` | `@kolisachint/hoocode-ai` | ~27,700 | Unified LLM API: providers, streaming, tool-calling, model discovery, OAuth, images |
| `packages/agent` | `@kolisachint/hoocode-agent-core` | ~9,500 | Provider-agnostic agent loop, tool execution, state, sessions, compaction, MCP |
| `packages/coding-agent` | `@kolisachint/hoocode-agent` | ~71,500 | The `hoocode` CLI: tools, permission gates, modes, extensions, subagents, config |

### Dependency graph (build order = leaves first)

```
tui   (no internal deps)
ai    (no internal deps)
agent          -> ai
coding-agent   -> agent, ai, tui
```

## 2. Churn analysis (git history)

Commits touching each package's `src/` in the last 3 months:

| Package | Commits (3 mo) | Interpretation |
| --- | ---: | --- |
| tui | 9 | **Stable** — API settled |
| agent | 16 | **Stable-ish** — loop settled, harness evolves slowly |
| ai | 19 | **Stable-ish** — mostly generated model lists + provider tweaks |
| coding-agent | 283 | **Hot** — active feature development |

Hottest files (change count, ~6 mo), all inside `coding-agent`:

| File | Changes | Area |
| --- | ---: | --- |
| `modes/interactive/interactive-mode.ts` | 56 | interactive UI |
| `core/tools/subagent.ts` | 51 | subagent Task tool |
| `main.ts` | 50 | CLI entry |
| `cli/args.ts` | 45 | CLI args |
| `core/subagent-pool.ts` | 42 | subagent orchestration |
| `extensions/core/hoo-core.ts` | 34 | built-in extension |
| `core/agent-session.ts` | 33 | session lifecycle |
| `modes/interactive/components/task-panel.ts` | 31 | task panel UI |
| `core/settings-manager.ts` | 30 | settings |
| `core/sdk.ts` | 27 | programmatic SDK |

Churn by area within `coding-agent/src` (~6 mo):
`core/` 811 · `modes/` 227 · `extensions/` 61 · `cli/` 50 · `utils/` 36.

**In `ai`, churn is concentrated in `models.generated.ts` (12) and provider files —
i.e. data, not architecture.** In `agent`, churn is in `harness/` (31), the part shared
with the CLI.

## 3. Stability tiers → migration order

| Tier | Contents | PyPI posture |
| --- | --- | --- |
| **T0 — frozen** | tui core (renderer, components, keys), ai types/stream core, agent loop + types | Publish early, semver from day one |
| **T1 — settling** | ai providers (anthropic, openai, google), agent harness (sessions, compaction), MCP tools | Publish after tests port; expect patch releases |
| **T2 — volatile** | coding-agent `core/` (tools, session, settings, extensions), `cli/` | In-repo only until API stabilizes; publish as one CLI package |
| **T3 — hot / UI** | interactive mode, subagent pool, task panel, hoo-core extension | Port last; highest rework risk; keep behind the CLI package boundary |

**Consequence:** migration order is `tui → ai → agent-core → coding-agent(core tools →
print mode → rpc → interactive/subagents)`. This is *also* the dependency order, so the
two constraints agree.

## 4. External dependency mapping (TS → Python)

| TS dep | Used by | Python equivalent |
| --- | --- | --- |
| typebox (JSON-schema types) | ai, agent | **pydantic v2** |
| chalk | tui, coding-agent | ANSI helpers in `cortex-tui` (tiny; no rich dependency) |
| marked (markdown) | tui | **mistune** (or minimal own renderer to match TS output) |
| @anthropic-ai/sdk / openai / @google/genai | ai | **httpx** raw clients (matches hoocode's thin-provider style) or official SDKs — decide per provider |
| @modelcontextprotocol/sdk | agent | **mcp** (official Python SDK) |
| ignore (gitignore) | agent, coding-agent | **pathspec** |
| glob | coding-agent | stdlib `pathlib.Path.glob` + **pathspec** |
| diff | coding-agent | stdlib `difflib` |
| uuid, yaml | agent | stdlib `uuid`, **pyyaml** |
| jiti (dynamic TS import) | coding-agent extensions | `importlib` (extensions become Python modules) |
| photon (WASM image) | coding-agent | **Pillow** |
| koffi (FFI, optional) | tui | not needed (use `termios`/`tty` stdlib) |
| Bun/npm workspaces | root | **uv workspaces** |
| Biome + tsgo | root | **ruff** (lint+format) + **pyright** or **mypy** |
| bun test | all | **pytest** |

## 5. Existing release machinery (to replicate)

- `scripts/bump-versions.mjs` + `sync-versions.js` — lockstep version bump.
- `scripts/release.mjs` — bump → publish → tag.
- `.github/workflows/ci.yml` — check + per-package test matrix + build.
- `.github/workflows/release.yml` — manual dispatch publish.
- `.github/workflows/merge-release.yml` — auto-release on PR labels
  (`npm:patch|minor|major`).

Python equivalents specified in [03-release-pipeline.md](03-release-pipeline.md).

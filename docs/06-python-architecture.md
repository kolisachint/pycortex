# Python Architecture — cortex.* Leaves

This document describes the Python-side architecture of pycortex, the mechanical port of hoocode (TypeScript). It covers how the TS source maps to Python packages, key type/transport differences, and conventions used across all leaves.

## Package Layout

Each leaf is a standalone package with its own `pyproject.toml`, source tree, and tests:

```
packages/<group>/<leaf>/
├── pyproject.toml          # hatchling build, publish flag
├── README.md
├── src/cortex/<group>/<...>/
│   ├── __init__.py         # public API
│   ├── types.py            # dataclasses / Pydantic models
│   └── ...
└── tests/
    └── test_*.py
```

### Namespace packages

All leaves share the `cortex.*` namespace (PEP 420, no top-level `__init__.py`):

- `cortex.ai.types` — AI types shared across ai, agent, and code leaves
- `cortex.agent.types` — Agent loop types
- `cortex.tui.*` — TUI leaves
- `cortex.code.*` — Code leaves (config, tools, session, prompts, print, main, rpc, interactive, resources, subagents, extensions)

### Source of truth

- **TS source**: `~/github/hoocode` (or `~/.cache/cortex-migration/hoocode` shallow clone)
- **Migration plan**: `docs/04-migration-plan.md`
- **Conventions log**: `.hoocode/MIGRATION_NOTES.md`
- **TUI parity**: `packages/tui/testkit/` with goldens from the real TS renderer

## Type Mapping — TS → Python

| TypeScript | Python | Notes |
|---|---|---|
| `interface Foo {}` | `@dataclass class Foo` | Mutable data containers |
| `type Foo = "a" \| "b"` | `Literal["a", "b"]` | Literal unions |
| `type Foo = Bar \| Baz` | `Bar \| Baz` | Union types (PEP 604) |
| `Foo \| undefined` | `Foo \| None` | Optional |
| `Promise<T>` | `Awaitable[T]` or native `async` | Async support |
| `readonly T[]` | `Sequence[T]` | Immutable sequences |
| `Record<K, V>` | `dict[K, V]` | Standard mapping |
| `Map<K, V>` | `dict[K, V]` | Python dicts preserve insertion order |
| `Set<T>` | `set[T]` | Standard set |
| `string` | `str` | — |
| `number` | `int \| float` | — |
| `boolean` | `bool` | — |
| `any` | `Any` | Avoid where possible |
| `unknown` | `Any` | — |
| `never` | `NoReturn` | — |

### Pydantic vs dataclasses

- **Published leaves** (T0/T1): Use `pydantic.BaseModel` for serialization boundaries (AI types, agent types).
- **Internal leaves** (T2/T3): Use `@dataclass` for simplicity. Pydantic is only used when serialization is required.

## Async Model

TypeScript uses Promises everywhere. Python uses:

- **`async def`** for coroutines (event loops, I/O)
- **`Awaitable[T]`** for callable-that-returns-coroutine
- **`asyncio.run()`** as the entry point
- **`asyncio.gather()`** for parallel execution

Key difference: Python requires explicit `await` at each call site; TS resolves Promises implicitly via the event loop.

## Error Handling

| TypeScript | Python |
|---|---|
| `throw new Error(msg)` | `raise RuntimeError(msg)` or domain-specific |
| `try/catch` | `try/except` |
| `Error.message` | `str(exception)` |
| Custom error classes | Subclass `Exception` |

Convention: leaf-specific errors inherit from a base exception defined in the leaf's types module.

## Configuration

Settings are loaded from JSON files (mirroring TS):

- `~/.config/cortex/settings.json` — global settings
- `.cortex/settings.json` — project settings
- `CORTEX_CONFIG_*` environment variables

The config leaf (`cortex.code.config`) handles loading, merging, and typed access.

## Tool System

Each tool is a standalone function:

```python
async def read_file(tool_call_id: str, args: ReadInput) -> AgentToolResult: ...
```

Tools are bundled into groups (coding, read-only, etc.) via `create_coding_tools()` in `cortex.code.tools`.

## Session Management

Sessions are append-only JSONL trees (identical to TS):

- Each message is a JSON line
- Branching creates a new leaf in the tree
- Compaction summarises old context
- Skill blocks are parsed from user messages

## Print Mode

The print mode reads from stdin or files and outputs to stdout in text or JSON format. It does NOT use the TUI layer — it's a pure data pipeline.

## Interactive Mode (stub)

The interactive mode is the full TUI experience. In Python, this would use `cortex.tui` for rendering. Currently a stub — the TUI layer handles all rendering.

## RPC Mode

The RPC mode receives JSON commands on stdin and outputs JSON events on stdout. Used for embedding the agent in other applications.

## Subagents

Subagents are child processes spawned to handle delegated tasks. The pool manages concurrency, token budgets, and depth limits.

## Extension System

Extensions in Python use `importlib` for loading. The API is redesigned as a Python plugin system:

- Extensions are Python modules with `activate()` / `deactivate()` hooks
- They receive an `ExtensionContext` with access to the agent
- They can register tools, commands, and event handlers

## Testing Conventions

- **TUI tests**: Use `cortex.tui.testkit` with `Surface` and golden frames
- **Unit tests**: Standard `pytest` with `pytest-asyncio` for async
- **Gates**: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyright packages`
- **Type checking**: `pyright` strict mode (with four rules disabled repo-wide)

## Build & Release

- **Build system**: `hatchling`
- **Package manager**: `uv` with workspace support
- **Versioning**: Lockstep across all four umbrellas
- **Publishing**: CI-only via `release.yml` + `PYPI_TOKEN`
- **Console script**: `cortex = cortex.code.main:main`

## Migration History

This port was done leaf-by-leaf following `docs/04-migration-plan.md`. Each step:

1. Read the TS source
2. Mechanical translate to Python (see `.hoocode/skills/port-ts-module.md`)
3. Port the test file (see `.hoocode/skills/port-ts-tests.md`)
4. Run gates
5. Commit as `migrate: <id> <title>`

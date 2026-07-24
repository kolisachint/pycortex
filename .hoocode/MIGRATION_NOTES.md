# Migration Notes (pycortex ← hoocode)

Living doc. Update after every step. Speeds up future migrations by recording
conventions, gotchas, and the mechanical mappings that recur.

## Workflow (orchestrator mode)
- `uv run scripts/migrate_next.py --start` → emits next step brief.
- Orchestrator: read source + deps, split code-gen + unit tests to sonnet subagents
  (`general-purpose`), then REVIEW + run integration/gates yourself.
- Gates (all must be green):
  `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyright packages`
- `uv run scripts/migrate_next.py --done <id>` — re-runs gates, flips checkbox,
  commits `migrate: <id> <title>`. ≤1 step per session.

## Repo layout
- Source (TS, never edit): `/Users/sachinkoli/github/hoocode`
- Target (Python): `/Users/sachinkoli/github/pycortex`
- Each leaf = its own package under `packages/<group>/<leaf>/`
  - code: `src/cortex/<group>/<...>/`  (namespace package, no top-level __init__)
  - tests: `<leaf>/tests/test_*.py`
  - `pyproject.toml` with `[tool.uv.sources.*] workspace = true` for internal deps
- Plan: `docs/04-migration-plan.md`; arch + package map: `docs/02-target-architecture.md`

## Package/module mapping (ai group) — IMPORTANT
TS `packages/ai/src/` is split across MULTIPLE python packages:
- `types.ts`                → `cortexcode-ai-types`  → `cortex.ai.types`
- `api-registry.ts`         → `cortexcode-ai-models` → `cortex.ai.models` (api_registry.py)
- `stream.ts`, `utils/event-stream.ts` → `cortexcode-ai-stream` → `cortex.ai.stream`
- `env.ts`                  → `cortex.ai.env`
- `providers/faux.ts`       → `cortexcode-ai-provider-faux` → `cortex.ai.providers.faux`
- So a TS import `../api-registry.js` maps to `from cortex.ai.models import ...`
  and `../utils/event-stream.js` → `from cortex.ai.stream import ...`.

## Type mappings (see docs/02 §4)
- typebox / interface → pydantic v2 model (`from __future__ import annotations`)
- `Promise<T>` → `async def -> T`
- discriminated union w/ `type` field → pydantic models + `Literal` tag; events use `.type`
- `undefined`/`null` → `None`
- camelCase public names → snake_case (`getTokens`→`get_tokens`, `setResponses`→`set_responses`)
- Keep file structure 1:1 with TS for diffability.

## Known building blocks (already ported)
- `create_assistant_message_event_stream()` → `AssistantMessageEventStream`
  (in `cortex.ai.stream`). Producer: `.push(event)`, `.end(result)`. Consumer:
  `async for`, `await s.result()`. Complete events: type in ("done","error").
- Event models in `cortex.ai.types`: StartEvent, TextStartEvent, TextDeltaEvent,
  TextEndEvent, ThinkingStartEvent/Delta/End, ToolCallStartEvent/Delta/End,
  DoneEvent(reason, message), ErrorEvent(reason, error). Access tag via `.type`.
- `register_api_provider(ApiProvider(api, stream, stream_simple), source_id)`,
  `unregister_api_providers(source_id)`, `clear_api_providers()` in `cortex.ai.models`.
  ApiProvider wraps stream fns; wrapper validates `model.api == api`.
- Content types: TextContent(type="text", text), ThinkingContent(type="thinking",
  thinking), ToolCall(type="toolCall", id, name, arguments), ImageContent(type="image",
  mimeType, data). Messages: UserMessage, AssistantMessage(role,content,api,provider,
  model,usage,stopReason,errorMessage?,responseId?,timestamp), ToolResultMessage
  (toolName, content). Usage(input,output,cacheRead,cacheWrite,totalTokens,cost).

## Test conventions
- pytest, `asyncio_mode=auto` (async def tests need no decorator).
- One TS test file → one `test_*.py`. Keep names/order aligned.
- No real API keys; use faux provider / fixtures.
- Reset global registries between tests (`clear_api_providers()` in a fixture).

## Gotchas
- `structuredClone` → deep copy (pydantic `model_copy(deep=True)`).
- `Date.now()` → `time.time()`*1000 or int(time.time()*1000) (ms). Check usage.
- `queueMicrotask` → `asyncio.ensure_future`/`create_task` or `await asyncio.sleep(0)`.
- `Math.random()` streaming chunk sizes → keep behavior; tests must not assume exact splits.
- `JSON.stringify` → `json.dumps` (mind separators/key order for token estimates).

## Orchestration playbook (worked well for 2.6)
- Do infra yourself FIRST (skeleton dirs, pyproject deps) — deterministic, avoids
  subagent guesswork. Then dispatch code-gen + tests to sonnet in parallel.
- CRITICAL: give both subagents the SAME public API contract (exact signatures) or
  they diverge. In 2.6 the module used options-objects while tests used kwargs —
  had to reconcile. Decide kwargs-vs-object up front and state it in BOTH prompts.
- Subagents may "timeout" but still have written their files — always check disk
  (`find <pkg> -type f`) before re-dispatching.
- Orchestrator owns: reconciling API mismatches, pyright strict cleanup, integration.
- Sonnet subagents should NOT be trusted to run full gates; they lint/format their
  own files only. You run pytest+ruff+pyright and the cross-package integration.

## Repo gotchas (confirmed)
- `uv run pytest` (whole repo) FAILS to collect: every leaf has `tests/__init__.py`
  with no unique package name → `ModuleNotFoundError: No module named 'tests.test_x'`
  (duplicate basename collision). This is PRE-EXISTING. The migrate gate runs
  TARGETED pytest per package (`pytest packages/ai/<leaf>`) which works. For
  integration, run each package's pytest separately in a loop.
- pyright is `strict`. Common fixes: `@overload` for get_model() (no-arg → Model,
  arg → Model|None); `# pyright: ignore[reportPrivateUsage]` on the IMPORTED NAME
  line (not the paren) when tests import `_helpers`; `# pyright: ignore[reportUnusedFunction]`
  on autouse fixtures; `cast(T, x)` for callable-step results typed `object`.
- Registered stream-fn options convention: type as `dict[str, Any] | None` (matches
  stream.py + test_stream.py). Faux reads options via a `_get_opt(options, key)`
  helper that accepts BOTH dicts and objects — but tests should pass dicts to stay
  pyright-clean, e.g. `complete(model, ctx, {"session_id": "s1", "cache_retention": "short"})`.
- Abort signal: no AbortSignal type in Python port. Represent as any object with a
  bool `.aborted`; faux checks `getattr(signal, "aborted", False)`. StreamOptions has
  NO on_response field → the TS `options.onResponse?.()` call is OMITTED (no-op).

## Step log
- 2.12 provider-common — DONE. New shared leaf `cortexcode-ai-provider-common`
  (`cortex.ai.providers._common`). Ported 4 helpers from `providers/*.ts`:
  `cache_retention.py` (`resolve_cache_retention`), `simple_options.py`
  (`build_base_options`, `clamp_reasoning`, `adjust_max_tokens_for_thinking`),
  `transform_messages.py` (`transform_messages`), `github_copilot_headers.py`
  (`infer_copilot_initiator`, `has_copilot_vision_input`, `build_copilot_dynamic_headers`).
  31 tests (transform-messages ported from
  transform-messages-copilot-openai-to-anthropic.test.ts; cache/simple-options/copilot
  are new unit tests since the TS cache-retention.test.ts is e2e/API-gated).
  TWO PREREQUISITES resolved in this step (both required for faithful port, not
  enhancements):
  1. NAMESPACE: `cortex.ai.providers` was a REGULAR package because faux shipped
     `providers/__init__.py` (a barrel re-exporting faux). That pins the namespace
     `__path__` to faux's dir only, so a second wheel (provider-common) can't add
     `_common` under it. FIX: deleted faux's `providers/__init__.py` → PEP 420
     namespace package. TS has no providers barrel either; all imports already use
     `cortex.ai.providers.faux` directly, so nothing broke. RULE: never ship
     `providers/__init__.py` from any provider leaf — keep `cortex.ai.providers` a
     namespace. Each provider is a submodule (`.faux`, `._common`, later `.anthropic`).
  2. TYPES GAP: pycortex `StreamOptions` was a partial stub missing 8 fields that
     `build_base_options` copies (on_payload, on_response, headers, timeout_ms,
     max_retries, max_retry_delay_ms, metadata, constrain_tool_calls) — added them in
     TS field order. `SimpleStreamOptions` was a WRONG stub (model/api/provider/base_url,
     unused anywhere) → replaced with faithful `class SimpleStreamOptions(StreamOptions)`
     + reasoning/thinking_budgets/thinking_display. Exported `ThinkingBudgets` from
     types `__init__`. Callbacks (onPayload/onResponse/signal) typed `Any | None`.
  `adjust_max_tokens_for_thinking` returns a dict with SNAKE keys
  `{"max_tokens", "thinking_budget"}` (not TS camel). Deps: only `cortexcode-ai-types`
  (the 4 helpers import nothing else), narrower than arch doc §128's list — fine.
  VENV GOTCHA: `uv pip install -e <one pkg>` / plain `uv sync` collapsed the venv to a
  couple editables and broke faux's namespace merge. Use `uv sync --all-packages` to
  restore all workspace editables so `cortex.ai.providers.__path__` lists BOTH dirs.

- 2.11 sanitize-unicode — DONE. utils/sanitize-unicode.ts (`sanitizeSurrogates`) →
  `cortex.ai.util.sanitize_unicode` (`sanitize_surrogates`) + 7 tests. No TS test file
  existed. KEY INSIGHT: Python str is a sequence of Unicode code points (not UTF-16
  units), so valid emoji/BMP+ chars are single code points OUTSIDE the surrogate range
  and lone surrogates are single code points in 0xD800-0xDFFF. So the TS regex
  `[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]` collapses to
  simply stripping any 0xD800-0xDFFF code point. Re-exported from util `__init__`.

- 2.6 provider-faux — DONE. providers/faux.ts (455) + faux-provider.test.ts (597) →
  `cortex.ai.providers.faux` (+ `providers/__init__.py` re-export) + 22 tests, all green.
  Skeleton dir was `src/cortex/ai/provider-faux/` (hyphen, invalid module) → moved to
  `src/cortex/ai/providers/faux.py`. Added `cortexcode-ai-models` dep (faux needs
  api-registry). register_faux_provider + faux_assistant_message use KWARGS (not
  options objects).
</content>

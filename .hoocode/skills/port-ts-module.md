---
name: port-ts-module
description: Mechanical TS→Python translation procedure for porting a hoocode module.
---

# port-ts-module

## Procedure

1. Read the TS module **and its tests first** — tests define the contract.
2. Map types per docs/02-target-architecture.md §4:
   - typebox → pydantic v2 models
   - `Promise<T>` → `async def … -> T`
   - EventEmitter → callback lists / simple observer helpers
   - discriminated unions → pydantic tagged unions / `Literal` type fields
   - `undefined`/`null` → `None` (collapse unless the distinction is semantic)
3. Preserve public names in snake_case (`getTokens` → `get_tokens`).
4. Keep file-level structure 1:1 with the TS original so future hoocode diffs are easy
   to re-apply. One TS file → one Python module at the plan's target path.
5. Translate tests before implementation when feasible (port-tests-first).

## Forbidden

- Adding features or configuration the TS original does not have.
- "Improving" APIs, renaming concepts, merging/splitting files.
- Reformatting semantics (e.g. changing error types, reordering events).
- New runtime dependencies without a note in doc 02 §4 in the same PR.

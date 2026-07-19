---
name: port-ts-tests
description: bun-test → pytest translation table for porting hoocode test files.
---

# port-ts-tests

## Translation table

| bun-test | pytest |
| --- | --- |
| `describe("X", …)` | module or `class TestX:` grouping |
| `test("does y", …)` | `def test_does_y():` |
| `test(…, async () => …)` | `async def test_…():` (asyncio_mode=auto) |
| `expect(x).toBe(y)` / `toEqual` | `assert x == y` |
| `expect(x).toBeTruthy()` | `assert x` |
| `expect(fn).toThrow()` | `with pytest.raises(…):` |
| `beforeEach` / `afterEach` | fixtures (`@pytest.fixture`) |
| mock timers | `pytest-asyncio` + injected clock/sleep patterns |
| xterm snapshot assertions | ANSI golden files under `tests/goldens/` |

## Rules

- One TS test file → one Python test file (`foo.test.ts` → `test_foo.py`) in the
  package's `tests/` dir.
- Keep test names and ordering aligned with the original for diffability.
- Golden files: byte-exact ANSI output committed under `tests/goldens/`; regenerate
  only deliberately (assert-first, never auto-overwrite in CI).
- No real API keys — anything network-shaped runs against the faux provider or
  recorded fixtures.

# cortexcode-cli-e2e

End-to-end harness for the `pycortex` TUI. **Never published.**

The app-level counterpart of [`tui/testkit`](../../tui/testkit). `testkit` proves a
*component* renders like the TypeScript one; this proves the *product* behaves
like hoocode when a person runs it — boots the real interactive mode against a
fake terminal, presses keys, and reads the resulting cell grid.

## Why it exists

Step 5.2 (`interactive`) was ticked green with a 151-line stub that prints
`Interactive mode started` and returns. The audit accepted it because the only
question it asked was "does this leaf have module code and a test file", and a
stub answers yes to both.

A scenario asks a question a stub cannot fake: *does the screen show the thing.*

## Using it

```python
from cortex.code.e2e import AppHarness, boot_app

with boot_app(columns=100, rows=30) as h:
    h.type("hello")
    h.key("enter")
    h.assert_shows("hello")
    print(h.snapshot())  # bordered cell grid, for failure reports
```

- `screen()` — the visible grid.
- `transcript()` — scrollback + visible grid; what you want for chat assertions,
  since by the third turn the first turn is off-screen.
- `key(name)` — named keys from `_keys.KEY_SEQUENCES`, each verified to
  round-trip through the app's own `parse_key`.

## The corpus

`_scenarios.py` holds the checklist: one entry per thing a user can verify by
looking at the app. Each names the Phase 7 step that delivers it.

```
uv run scripts/tui_e2e.py            # run the corpus, print a summary
uv run scripts/tui_e2e.py --refresh  # …and rewrite docs/tui-e2e-report.json
```

`scripts/migrate_next.py` reads that report and refuses to tick a Phase 7 box
while scenarios attributed to that step are pending or failing.

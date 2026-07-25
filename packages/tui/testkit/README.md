# cortexcode-tui-testkit

**Test infrastructure. Never published, never imported by shipped code.**

The authoritative rendering surface the TUI port is checked against.

For every other leaf in pycortex, "the Python reads like the TypeScript" is
reasonable evidence that the port is right. For the TUI it is not. `tui.ts`
emits a stream of cursor moves, erases and text; two implementations can emit
very different streams and land on the same screen, or emit near-identical
streams and land on different ones. The observable contract is the grid of cells
the user ends up looking at — so that is what gets compared.

## What is in here

| Piece | Role |
| --- | --- |
| `Surface` | A cell grid plus an ANSI interpreter. Turns a write stream into a screen. |
| `snapshot()` / `diff_surfaces()` | Readable failures: two screens side by side with the differing cells named. |
| `CaptureTerminal` | Records what a renderer writes. Interprets nothing. |
| `goldens/scenarios.json` | The corpus. Read by **both** the TS dumper and the Python tests, so they cannot drift. |
| `goldens/ts-*.json` | Frames captured from the real hoocode TS components and renderer. |
| `goldens/xterm-grids.json` | The same ANSI streams as seen by `@xterm/headless`. |
| `reference/*.ts` | The capture scripts. Run under `bun`. |

## The authority chain

1. `@xterm/headless` is a production terminal emulator, so it defines correct
   ANSI interpretation. `test_surface_xterm.py` holds `Surface` to it cell for
   cell — the surface is a *validated* terminal model, not a convenient one.
2. The real hoocode TS implementation defines correct rendering. `reference/dump.ts`
   imports it directly (never a reimplementation) and records what it produces.
3. The parity tests render the same scenario with pycortex, project both through
   the validated `Surface`, and diff.

The middle step is what makes this worth building: a bug in the surface cannot
hide a real divergence, because both sides go through the same interpreter and
only cancel out when the two implementations genuinely agree.

## Working with it

```bash
uv run pytest packages/tui/testkit          # parity + surface tests
uv run scripts/tui_parity.py                # coverage report
uv run scripts/tui_goldens.py --check       # goldens cover the corpus?
uv run scripts/tui_goldens.py --refresh     # recapture (needs bun + the TS source)
```

Porting a TUI module: add its scenarios to `goldens/scenarios.json`, refresh the
goldens, then make the diff go away. A scenario pycortex cannot run yet reports
as `UNPORTED` against the plan step that will close it, rather than quietly
passing against a stand-in.

# cortexcode-tui-render

Differential renderer for `cortex.tui`. Port of hoocode's `packages/tui/src/tui.ts`.

- `Container` — memoized component tree; returns the same list object when a
  subtree is unchanged, which is what lets the root diff by identity.
- `TUI` — the root: patch-tracked flatten, overlay stack and compositing,
  `CURSOR_MARKER` extraction for IME positioning, kitty image bookkeeping,
  synchronized output, and viewport/scroll accounting.

```python
from cortex.tui.render import TUI

tui = TUI(terminal)
tui.add_child(component)
tui.start()  # frames are coalesced on the running event loop
tui.request_render()  # never paints synchronously
tui.render_now()  # explicit flush when there is no event loop
```

## Verification

This leaf is not verified by reading it against the TypeScript. Every frame it
produces is replayed through the terminal model in `packages/tui/testkit` and
diffed, cell by cell, against the same frame from the real hoocode
implementation:

```bash
uv run scripts/tui_parity.py           # 39 renderer scenarios
uv run pytest packages/tui/render      # the parts a screen cannot show
```

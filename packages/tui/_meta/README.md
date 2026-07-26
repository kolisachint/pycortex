# cortexcode-tui

Umbrella meta-package for the `cortex.tui.*` namespace. It carries **no code** —
installing it pulls in every published leaf, pinned to the release it was cut with.

```bash
pip install cortexcode-tui        # the whole TUI
pip install cortexcode-tui-keys   # or just the key parser
```

```python
from cortex.tui.components import Box, Editor, Text
from cortex.tui.render import TUI
from cortex.tui.terminal import ProcessTerminal
```

## Leaves

| Distribution | Import | Owns |
| --- | --- | --- |
| `cortexcode-tui-util` | `cortex.tui.util` | ANSI-aware width, wrap, truncate, grapheme segmentation |
| `cortexcode-tui-fuzzy` | `cortex.tui.fuzzy` | fuzzy match + filter |
| `cortexcode-tui-keys` | `cortex.tui.keys` | key parsing (legacy + kitty), keybinding registry |
| `cortexcode-tui-terminal` | `cortex.tui.terminal` | terminal control, stdin buffering |
| `cortexcode-tui-images` | `cortex.tui.images` | kitty/iTerm2 image protocols, capability detection |
| `cortexcode-tui-render` | `cortex.tui.render` | the differential renderer (`TUI`, `Container`) |
| `cortexcode-tui-editing` | `cortex.tui.editing` | kill ring, undo stack, editor interface |
| `cortexcode-tui-components` | `cortex.tui.components` | text, box, input, lists, editor, markdown, … |

Dependencies flow one way: components → editing → render → keys → util, with
fuzzy and images as shared leaves. Each leaf versions independently, so a fix to
the renderer does not force a release of the key parser.

`cortexcode-tui-testkit` is deliberately **not** in that list: it is the parity
harness the other leaves are tested against, and is never published.

A mechanical Python port of [hoocode](https://github.com/kolisachint/hoocode)'s
`packages/tui`. Feature parity, no enhancements.

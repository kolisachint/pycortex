# cortexcode-tui-components

The widget set for `cortex.tui`. Port of hoocode's `packages/tui/src/components/*.ts`
and `autocomplete.ts`.

```python
from cortex.tui.components import Box, Editor, Input, Markdown, SelectList, Text

Text("hello", padding_x=1, padding_y=0)  # styled line
TruncatedText(long_line)  # …with an ellipsis at the width limit
Spacer(1)

box = Box(padding_x=1, padding_y=0)  # framing and padding
box.add_child(Text("inside"))

Loader(tui, cyan, dim, "Working...")  # animated indicator
CancellableLoader(tui, cyan, dim, "Working...")  # …that aborts on cancel

Input()  # single line, full Emacs motion set
Editor(tui, theme, EditorOptions(...))  # multi-line, undo, kill ring, autocomplete
SelectList(items, max_visible, theme)  # filterable list
SettingsList(settings, max_visible, theme, on_change, on_cancel)
Markdown(text, padding_x, padding_y, theme)  # marked-compatible AST → styled lines
```

Colours are always injected, never assumed: a component takes the styling
functions it needs (`theme`, `*_color_fn`) so the host application owns the
palette — the same arrangement as the TS.

`autocomplete.py` provides the completion plumbing the editor drives:
`CombinedAutocompleteProvider` merges providers, `SlashCommand` describes a
command entry, and results arrive as `AutocompleteSuggestions` /
`CompletionResult`.

`_markdown_ast.py` is the reason `Markdown` can be a mechanical port at all:
`markdown.ts` is written against `marked`'s token tree, so this module normalises
[mistune](https://mistune.lepture.com)'s AST into that shape — renaming node
types, rebuilding `List.items` and table `header`/`rows`/`align`, and
synthesising the `space` tokens mistune omits but the renderer keys its
blank-line spacing off. It is verified token-for-token against AST goldens
captured from real `marked`.

## Verification

These are not verified by reading them against the TypeScript. Each component is
rendered through the terminal model in `packages/tui/testkit` and diffed, cell by
cell, against the same frame from the real hoocode implementation:

```bash
uv run scripts/tui_parity.py            # scenario coverage
uv run pytest packages/tui/components
```

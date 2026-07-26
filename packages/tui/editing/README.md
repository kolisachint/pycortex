# cortexcode-tui-editing

Editing primitives shared by the input and editor components. Port of hoocode's
`packages/tui/src/{editor-component,kill-ring,undo-stack}.ts`.

```python
from cortex.tui.editing import KillRing, UndoStack

ring = KillRing()
ring.push("word ", prepend=False)  # kill forward
ring.push("the ", prepend=True, accumulate=True)  # consecutive kills coalesce
ring.peek()  # "the word " — what a yank inserts
ring.rotate()  # yank-pop walks back through earlier kills

undo: UndoStack[EditorState] = UndoStack()
undo.push(state)
undo.pop()
```

`EditorComponent` is the interface the components leaf implements and the
autocomplete machinery talks to (`AutocompleteProvider`, `AutocompleteItem`,
`AutocompleteSuggestions`, `CompletionResult`, `AbortSignal`). It lives here so
that a provider can be written against the editor without depending on the whole
components leaf.

```bash
uv run pytest packages/tui/editing
```

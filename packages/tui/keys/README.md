# cortexcode-tui-keys

Key parsing and keybindings for `cortex.tui`. Port of hoocode's
`packages/tui/src/keys.ts` and `keybindings.ts`.

Turns raw terminal bytes into stable key identifiers, and resolves those against
the keybinding table every component consults.

```python
from cortex.tui.keys import get_keybindings, matches_key, parse_key

parse_key("\x1b[1;3D")  # "alt+left"   — CSI with a modifier
parse_key("\x1bb")  # "alt+left"   — the Emacs alias the TS maps to it
parse_key("\x1f")  # "ctrl+-"     — legacy control code

matches_key(data, "up")
get_keybindings().matches(data, "tui.editor.deleteWordBackward")
```

Three input families are supported, because a terminal may speak any of them:
legacy control codes and ESC-prefixed sequences, CSI sequences with modifier
parameters (`\x1b[1;5C`), and the kitty keyboard protocol including its
functional-key codepoints and release/repeat events (`is_key_release`,
`is_key_repeat`, `set_kitty_protocol_active`).

`KeybindingsManager` merges user overrides onto `TUI_KEYBINDINGS` and reports
`KeybindingConflict`s; `get_keybindings()` / `set_keybindings()` hold the
process-wide instance.

```bash
uv run pytest packages/tui/keys
```

# cortexcode-tui-terminal

The terminal device `cortex.tui.render` paints onto. Port of hoocode's
`packages/tui/src/terminal.ts` and `stdin-buffer.ts`.

```python
from cortex.tui.terminal import ProcessTerminal

term = ProcessTerminal()
term.start(on_input=handle_key, on_resize=handle_resize)
term.write("hello")
term.columns, term.rows
term.stop()  # restores cooked mode, cursor and kitty protocol state
```

`Terminal` is the abstract surface — `columns`/`rows`, cursor moves and hiding,
line and screen clearing, title and progress reporting, `kitty_protocol_active`.
Implementing it is how a test drives the renderer without a tty; `ProcessTerminal`
is the real one over `sys.stdin`/`sys.stdout`.

`StdinBuffer` sits in front of input: it reassembles escape sequences split
across reads, so a component never sees half of a `\x1b[1;5C`, and it recognises
bracketed paste, emitting the payload through `on_paste` rather than as
individual keystrokes.

```bash
uv run pytest packages/tui/terminal
```

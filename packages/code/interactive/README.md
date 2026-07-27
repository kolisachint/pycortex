# cortexcode-cli-interactive

Interactive TUI mode for the Cortex CLI — the app `pycortex` starts.

Port of `packages/coding-agent/src/modes/interactive/` (plus `core/wordmark.ts`).

| Module | Ported from |
| --- | --- |
| `interactive_mode.py` | `interactive-mode.ts` — constructor, `init`, `run`, `shutdown` |
| `brand.py` | `brand.ts` |
| `wordmark.py` | `core/wordmark.ts` |
| `theme/` | `theme/theme.ts` (colour core) + `theme/dark.json`, verbatim |
| `components/footer.py` | `components/footer.ts` |

```python
from cortex.code.interactive import build_app_root, run_interactive_mode
```

`run_interactive_mode()` owns the real terminal and returns an exit code.
`build_app_root(tui, **options)` attaches the component tree to a TUI somebody
else made and started — the seam `cortex.code.e2e` boots the app through.

Step 7.2 delivers the shell: the startup banner, an editor showing `>`, a footer,
and Ctrl+C twice to exit. Chat, streaming, tools, slash commands and overlays
arrive with steps 7.3–7.9.

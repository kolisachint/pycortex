# cortexcode-tui-util

ANSI-aware text measurement for `cortex.tui`. Port of hoocode's
`packages/tui/src/utils.ts`.

Every other tui leaf sizes text through this one, so "how wide is this string"
has a single answer: escape sequences are zero-width, wide and emoji clusters
take two cells, and combining marks belong to the grapheme in front of them.

```python
from cortex.tui.util import truncate_to_width, visible_width, wrap_text_with_ansi

visible_width("\x1b[31mred\x1b[0m")  # 3 — SGR codes take no cells
truncate_to_width("hello world", 8)  # cuts on cells, not characters
wrap_text_with_ansi(text, columns)  # re-opens active styles on each line
```

Also public: `slice_by_column` / `slice_with_width` (column-accurate slicing),
`extract_segments` / `extract_ansi_code` (split text from styling),
`apply_background_to_line`, `normalize_terminal_output`, the word-motion
predicates `is_whitespace_char` / `is_punctuation_char`, and `get_segmenter` /
`grapheme_segments` — the segmentation `input.py` and `editor.py` split on.

```bash
uv run pytest packages/tui/util
```

# cortexcode-tui-fuzzy

Fuzzy subsequence matching for `cortex.tui`. Port of hoocode's
`packages/tui/src/fuzzy.ts`.

Used by the autocomplete and select-list components to decide which candidates
survive a query and in what order.

```python
from cortex.tui.fuzzy import fuzzy_filter, fuzzy_match

match = fuzzy_match("gcm", "git commit -m")
match.matches  # True — the query is a subsequence
match.score  # **lower is better**: word starts, runs and exact hits subtract

fuzzy_filter(commands, "gcm", lambda c: c.name)  # ranked, non-matches dropped
```

Multi-token queries must match every token. Letter/digit swaps (`abc123` ↔
`123abc`) match with a small penalty. Matching is case-insensitive.

Dependency-free by design — it is a leaf every component tier can reach.

```bash
uv run pytest packages/tui/fuzzy
```

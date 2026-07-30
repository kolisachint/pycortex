"""``--list-models``: every model this machine can actually reach.

Port of ``cli/list-models.ts`` from ``packages/coding-agent/src/``.

The flag exited non-zero until 7.12, naming the model registry as what it was
waiting for — while four error messages across the port already told the user to
"use --list-models to see available models". The registry landed in 7.11, so the
flag is the last thing between those messages and being true.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from cortex.ai.types import Model
from cortex.code.config import format_no_models_available_message
from cortex.tui.fuzzy import fuzzy_filter

__all__ = ["format_token_count", "list_models"]

#: Columns, in the TS's order, as ``(header, reader)``.
_COLUMNS: tuple[tuple[str, Callable[[Model], str]], ...] = (
    ("provider", lambda model: model.provider),
    ("model", lambda model: model.id),
    ("context", lambda model: format_token_count(model.context_window)),
    ("max-out", lambda model: format_token_count(model.max_tokens)),
    ("thinking", lambda model: "yes" if model.reasoning else "no"),
    ("images", lambda model: "yes" if "image" in model.input else "no"),
)


def format_token_count(count: int) -> str:
    """``200000`` -> ``200K``, ``1000000`` -> ``1M``. Port of ``formatTokenCount``."""
    if count >= 1_000_000:
        millions = count / 1_000_000
        return f"{millions:.0f}M" if millions % 1 == 0 else f"{millions:.1f}M"
    if count >= 1_000:
        thousands = count / 1_000
        return f"{thousands:.0f}K" if thousands % 1 == 0 else f"{thousands:.1f}K"
    return str(count)


async def list_models(model_registry: Any, search_pattern: str | None = None) -> None:
    """Print the available models as a table, optionally fuzzy-filtered.

    ``get_available`` is async in this port where the TS's is synchronous — it is
    the same call the model overlay makes — so this is a coroutine and
    ``code/main`` runs it.
    """
    load_error = model_registry.get_error()
    if load_error:
        print(f"Warning: errors loading models.json:\n{load_error}", file=sys.stderr)

    models: list[Model] = await model_registry.get_available()
    if not models:
        print(format_no_models_available_message())
        return

    if search_pattern:
        models = fuzzy_filter(models, search_pattern, lambda model: f"{model.provider} {model.id}")
        if not models:
            print(f'No models matching "{search_pattern}"')
            return

    models = sorted(models, key=lambda model: (model.provider, model.id))

    rows = [[str(read(model)) for _, read in _COLUMNS] for model in models]
    headers = [header for header, _ in _COLUMNS]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    def line(cells: list[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True))

    print(line(headers))
    for row in rows:
        print(line(row))

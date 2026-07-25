"""Hold the autocomplete provider to results captured from the real TS.

`autocomplete.ts` draws nothing, so the surface harness cannot see it; what it
produces is data — a suggestion list, and the text `applyCompletion` writes back
— which means it can be diffed exactly rather than eyeballed. The goldens come
from `reference/autocomplete_dump.ts`, which imports the actual hoocode module.

Each scenario builds its own tree under `tmp_path/{cwd,outside,home}`, with HOME
pointed at `home`, so `~` and absolute-path scenarios are machine-independent —
`{cwd}`/`{home}`/`{root}` are substituted in and back out exactly as the dumper
does it.

One ordering caveat, and only for the `@` scenarios: `fd` walks in parallel, so
the order it reports equally-scored entries in is not a contract on either side.
Those compare as multisets, plus the first item when the top score is unique;
the ranking itself is pinned directly by replaying the real `scoreEntry` calls
the dumper recorded.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from cortex.tui.components.autocomplete import (
    AutocompleteItem,
    CombinedAutocompleteProvider,
    SlashCommand,
)

# The testkit owns the goldens; reach them through the installed package so this
# keeps working regardless of where pytest is invoked from.
from cortex.tui.testkit._scene import GOLDENS_DIR as GOLDENS

CORPUS = json.loads((GOLDENS / "autocomplete-corpus.json").read_text())
TS = {s["id"]: s for s in json.loads((GOLDENS / "ts-autocomplete.json").read_text())["scenarios"]}
SCENARIOS: list[dict[str, Any]] = CORPUS["scenarios"]

FD_PATH = shutil.which("fd") or shutil.which("fdfind")


class _Signal:
    aborted = False


def build_tree(base: Path, spec: dict[str, Any] | None) -> None:
    base.mkdir(parents=True, exist_ok=True)
    spec = spec or {}
    for directory in spec.get("dirs", []):
        (base / directory).mkdir(parents=True, exist_ok=True)
    for file_path, contents in spec.get("files", {}).items():
        full_path = base / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(contents)
    for link_path, target in spec.get("symlinks", {}).items():
        full_path = base / link_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, full_path)


def build_argument_completer(completions: dict[str, Any]) -> Any:
    """The corpus describes `get_argument_completions`; here it is built."""
    kind = completions["kind"]
    items = [
        AutocompleteItem(
            value=item["value"], label=item["label"], description=item.get("description")
        )
        for item in completions.get("items", [])
    ]

    async def async_items(_prefix: str) -> Any:
        return items

    def sync_items(_prefix: str) -> Any:
        return items

    async def async_none(_prefix: str) -> Any:
        return None

    def invalid(_prefix: str) -> Any:
        # What the TS `Array.isArray` guard exists to reject.
        return "not-an-array"

    return {"async": async_items, "sync": sync_items, "null": async_none}.get(kind, invalid)


def build_command(spec: dict[str, Any]) -> SlashCommand | AutocompleteItem:
    if "name" not in spec:
        return AutocompleteItem(
            value=spec["value"], label=spec["label"], description=spec.get("description")
        )

    completions = spec.get("argumentCompletions")
    return SlashCommand(
        name=spec["name"],
        description=spec.get("description"),
        argument_hint=spec.get("argumentHint"),
        get_argument_completions=(
            None if completions is None else build_argument_completer(completions)
        ),
    )


def item_record(item: AutocompleteItem, unsubstitute: Any) -> dict[str, Any]:
    return {
        "value": unsubstitute(item.value),
        "label": unsubstitute(item.label),
        "description": None if item.description is None else unsubstitute(item.description),
    }


def sort_by_value(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda record: record["value"])


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
async def test_matches_the_ts(
    scenario: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uses_fd = bool(scenario.get("fd"))
    if uses_fd and FD_PATH is None:
        pytest.skip("fd is not installed")

    expected = TS.get(scenario["id"])
    assert expected is not None, (
        f"no TS capture for {scenario['id']} — run `uv run scripts/tui_goldens.py --refresh`"
    )

    root = tmp_path
    cwd = root / "cwd"
    build_tree(cwd, scenario.get("tree"))
    build_tree(root / "outside", scenario.get("outsideTree"))
    build_tree(root / "home", scenario.get("homeTree"))
    monkeypatch.setenv("HOME", str(root / "home"))

    def substitute(value: str) -> str:
        return (
            value.replace("{cwd}", str(cwd))
            .replace("{home}", str(root / "home"))
            .replace("{root}", str(root))
        )

    def unsubstitute(value: str) -> str:
        return (
            value.replace(str(cwd), "{cwd}")
            .replace(str(root / "home"), "{home}")
            .replace(str(root), "{root}")
        )

    line = substitute(scenario["line"])
    cursor_col = (
        len(line) - scenario["cursorFromEnd"] if scenario.get("cursorFromEnd") else len(line)
    )
    commands = [build_command(spec) for spec in scenario.get("commands", [])]
    provider = CombinedAutocompleteProvider(commands, str(cwd), FD_PATH if uses_fd else None)

    # The ranking function, pinned against the real one call for call.
    for call in expected["scoreCalls"]:
        assert (
            provider._score_entry(call["path"], call["query"], call["isDirectory"])  # pyright: ignore[reportPrivateUsage]
            == call["score"]
        ), f"scoreEntry({call['path']!r}, {call['query']!r}, {call['isDirectory']})"

    result = await provider.get_suggestions(
        [line], 0, cursor_col, signal=_Signal(), force=bool(scenario.get("force"))
    )

    assert (
        provider.should_trigger_file_completion([line], 0, cursor_col)
        == (expected["shouldTriggerFileCompletion"])
    )

    if expected["suggestions"] is None:
        assert result is None
        return

    assert result is not None, f"expected {len(expected['suggestions']['items'])} suggestions"
    assert unsubstitute(result.prefix) == expected["suggestions"]["prefix"]

    items = [item_record(item, unsubstitute) for item in result.items]

    def apply_record(item: AutocompleteItem) -> dict[str, Any]:
        assert result is not None
        completion = provider.apply_completion([line], 0, cursor_col, item, result.prefix)
        cursor_line_text = (
            completion.lines[completion.cursor_line]
            if 0 <= completion.cursor_line < len(completion.lines)
            else ""
        )
        return {
            "value": unsubstitute(item.value),
            "lines": [unsubstitute(text) for text in completion.lines],
            "cursorLine": completion.cursor_line,
            # Measured in the placeholder-rendered line; see the dumper.
            "cursorCol": len(unsubstitute(cursor_line_text[: completion.cursor_col])),
        }

    applied = [apply_record(item) for item in result.items]

    if not uses_fd:
        assert items == expected["suggestions"]["items"]
        assert applied == expected["applied"]
        return

    assert sort_by_value(items) == sort_by_value(expected["suggestions"]["items"])
    assert sort_by_value(applied) == sort_by_value(expected["applied"])

    scores = sorted((call["score"] for call in expected["scoreCalls"]), reverse=True)
    if len(scores) < 2 or scores[0] > scores[1]:
        # Only one entry can be first, so the order is a contract here.
        assert items[0] == expected["suggestions"]["items"][0]

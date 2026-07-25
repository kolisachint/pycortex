"""Build the shared corpus's scenes with the *Python* implementation.

The corpus is a data file both sides read (`goldens/scenarios.json`), so this
module and `reference/dump.ts` are two interpreters of the same script. When a
component or renderer feature has not been ported yet, building raises
`Unported` rather than improvising — an unported scenario must show up as a
reported gap, never as a passing test over a stand-in.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "Corpus",
    "Unported",
    "build_component",
    "load_corpus",
    "load_golden",
]


def _find_goldens() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "goldens" / "scenarios.json"
        if candidate.is_file():
            return candidate.parent
    raise RuntimeError("goldens/ not found — is cortexcode-tui-testkit installed editable?")


GOLDENS_DIR = _find_goldens()


class Unported(Exception):
    """Raised when the corpus asks for something pycortex has not ported yet.

    Carries the plan step that will close the gap so the parity report can group
    failures by the work that fixes them.
    """

    def __init__(self, what: str, blocked_by: str) -> None:
        super().__init__(f"{what} (blocked by step {blocked_by})")
        self.what = what
        self.blocked_by = blocked_by


class Renderable(Protocol):
    def render(self, width: int) -> list[str]: ...
    def invalidate(self) -> None: ...


@dataclass(frozen=True)
class Corpus:
    ansi: list[dict[str, Any]]
    components: list[dict[str, Any]]
    renderer: list[dict[str, Any]]


def load_corpus(goldens_dir: Path | None = None) -> Corpus:
    data = json.loads(((goldens_dir or GOLDENS_DIR) / "scenarios.json").read_text())
    return Corpus(
        ansi=data.get("ansi", []),
        components=data.get("components", []),
        renderer=data.get("renderer", []),
    )


def load_golden(name: str, goldens_dir: Path | None = None) -> dict[str, Any]:
    path = (goldens_dir or GOLDENS_DIR) / name
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} is missing — run `uv run scripts/tui_goldens.py --refresh`"
        )
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def _bg_fn(spec: dict[str, Any] | None) -> Callable[[str], str] | None:
    if not spec:
        return None
    open_seq = spec["open"]
    close_seq = spec["close"]
    return lambda text: f"{open_seq}{text}{close_seq}"


def _theme_pair_fn(spec: dict[str, Any] | None) -> Callable[[str, bool], str]:
    """Theme fn taking (text, selected) — the settings-list label/value shape."""
    if not spec:
        return lambda text, _selected: text
    open_seq = spec["open"]
    close_seq = spec["close"]
    return lambda text, selected: f"{open_seq}{text}{close_seq}" if selected else text


def _wrap_fn(spec: dict[str, Any] | None) -> Callable[[str], str]:
    """Same idea as `_bg_fn` for the loader's colour functions; identity by default."""
    if not spec:
        return lambda text: text
    open_seq = spec["open"]
    close_seq = spec["close"]
    return lambda text: f"{open_seq}{text}{close_seq}"


# Component name -> the plan step that ports it. Everything absent from
# `_BUILDERS` below is reported against this map.
COMPONENT_STEPS = {
    "Text": "1.7",
    "TruncatedText": "1.7",
    "Box": "1.7",
    "Spacer": "1.7",
    "Loader": "1.7",
    "CancellableLoader": "1.7",
    "Input": "1.10",
    "SelectList": "1.11",
    "SettingsList": "1.11",
    "Markdown": "1.12",
    "Editor": "1.13",
    "Autocomplete": "1.14",
    "Image": "1.15",
}


class StaticLines:
    """Fixed-line component — the mirror of `StaticLines` in `reference/dump.ts`.

    Lets scenarios exercise the renderer (cursor markers, ANSI runs, wide
    characters, exact-width lines) without waiting on components that are still
    unported. Caches the list so repeated renders stay *identity*-stable: the
    root memoizes on list identity, so returning a fresh list every frame would
    silently disable the very patch path these scenarios test.
    """

    def __init__(self, lines: list[str]) -> None:
        self._cached = list(lines)

    def set_lines(self, lines: list[str]) -> None:
        self._cached = list(lines)

    def render(self, width: int) -> list[str]:
        return self._cached

    def invalidate(self) -> None:
        pass


def build_component(spec: dict[str, Any]) -> Renderable:
    """Instantiate the component a scenario describes, or raise `Unported`."""
    name = spec["component"]
    args = spec.get("args", {})
    bg = _bg_fn(spec.get("bgFn"))

    if name == "StaticLines":
        return StaticLines(args.get("lines", []))
    if name == "SelectList":
        from cortex.tui.components import (
            SelectItem,
            SelectList,
            SelectListLayoutOptions,
            SelectListTheme,
        )

        theme_spec = args.get("theme", {})
        layout_spec = args.get("layout", {})
        select_list = SelectList(
            [
                SelectItem(
                    value=raw["value"],
                    label=raw.get("label", ""),
                    description=raw.get("description"),
                )
                for raw in args.get("items", [])
            ],
            args.get("maxVisible", 5),
            SelectListTheme(
                selected_prefix=_wrap_fn(theme_spec.get("selectedPrefix")),
                selected_text=_wrap_fn(theme_spec.get("selectedText")),
                description=_wrap_fn(theme_spec.get("description")),
                scroll_info=_wrap_fn(theme_spec.get("scrollInfo")),
                no_match=_wrap_fn(theme_spec.get("noMatch")),
            ),
            SelectListLayoutOptions(
                min_primary_column_width=layout_spec.get("minPrimaryColumnWidth"),
                max_primary_column_width=layout_spec.get("maxPrimaryColumnWidth"),
            ),
        )
        if args.get("filter") is not None:
            select_list.set_filter(args["filter"])
        if args.get("selectedIndex") is not None:
            select_list.set_selected_index(args["selectedIndex"])
        return select_list
    if name == "SettingsList":
        from cortex.tui.components import (
            SettingItem,
            SettingsList,
            SettingsListOptions,
            SettingsListTheme,
        )

        theme_spec = args.get("theme", {})
        options_spec = args.get("options", {})
        return SettingsList(
            [
                SettingItem(
                    id=raw["id"],
                    label=raw["label"],
                    current_value=raw["currentValue"],
                    description=raw.get("description"),
                    values=raw.get("values"),
                )
                for raw in args.get("items", [])
            ],
            args.get("maxVisible", 5),
            SettingsListTheme(
                label=_theme_pair_fn(theme_spec.get("label")),
                value=_theme_pair_fn(theme_spec.get("value")),
                description=_wrap_fn(theme_spec.get("description")),
                cursor=theme_spec.get("cursor", "> "),
                hint=_wrap_fn(theme_spec.get("hint")),
            ),
            lambda _id, _value: None,
            lambda: None,
            SettingsListOptions(enable_search=options_spec.get("enableSearch", False)),
        )
    if name == "Input":
        from cortex.tui.components import Input

        component = Input()
        if args.get("value") is not None:
            component.set_value(args["value"])
        return component
    if name in ("Loader", "CancellableLoader"):
        from cortex.tui.components import (
            CancellableLoader,
            Loader,
            LoaderIndicatorOptions,
        )

        indicator_spec = args.get("indicator")
        indicator = (
            LoaderIndicatorOptions(
                frames=indicator_spec.get("frames"),
                interval_ms=indicator_spec.get("intervalMs"),
            )
            if indicator_spec is not None
            else None
        )
        loader_cls = Loader if name == "Loader" else CancellableLoader
        # `ui=None` keeps it out of the render loop — the frame is what is under
        # test, not the animation timer.
        loader = loader_cls(
            None,
            _wrap_fn(args.get("spinnerColor")),
            _wrap_fn(args.get("messageColor")),
            args.get("message", "Loading..."),
            indicator,
        )
        loader.stop()
        return loader
    if name == "Text":
        from cortex.tui.components import Text

        return Text(args.get("text", ""), args.get("paddingX", 1), args.get("paddingY", 1), bg)
    if name == "TruncatedText":
        from cortex.tui.components import TruncatedText

        return TruncatedText(args.get("text", ""), args.get("paddingX", 0), args.get("paddingY", 0))
    if name == "Spacer":
        from cortex.tui.components import Spacer

        return Spacer(args.get("lines", 1))
    if name == "Box":
        from cortex.tui.components import Box

        box = Box(args.get("paddingX", 1), args.get("paddingY", 1), bg)
        for child in spec.get("children", []):
            box.add_child(build_component(child))
        return box

    raise Unported(f"component {name}", COMPONENT_STEPS.get(name, "1.7"))

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
    "apply_scenario_capabilities",
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


# Serialisable stand-in for the app's markdown theme, mirroring the chalk
# colours `test/test-themes.ts` uses at chalk level 3. The same table lives in
# `reference/dump.ts`; every element gets a *distinct* code so a mis-applied
# style shows up on the surface instead of blending in.
DEFAULT_MARKDOWN_THEME: dict[str, dict[str, str]] = {
    "heading": {"open": "\x1b[1m\x1b[36m", "close": "\x1b[39m\x1b[22m"},
    "link": {"open": "\x1b[34m", "close": "\x1b[39m"},
    "linkUrl": {"open": "\x1b[2m", "close": "\x1b[22m"},
    "code": {"open": "\x1b[33m", "close": "\x1b[39m"},
    "codeBlock": {"open": "\x1b[32m", "close": "\x1b[39m"},
    "codeBlockBorder": {"open": "\x1b[2m", "close": "\x1b[22m"},
    "quote": {"open": "\x1b[35m", "close": "\x1b[39m"},
    "quoteBorder": {"open": "\x1b[2m", "close": "\x1b[22m"},
    "hr": {"open": "\x1b[2m", "close": "\x1b[22m"},
    "listBullet": {"open": "\x1b[36m", "close": "\x1b[39m"},
    "bold": {"open": "\x1b[1m", "close": "\x1b[22m"},
    "italic": {"open": "\x1b[3m", "close": "\x1b[23m"},
    "strikethrough": {"open": "\x1b[9m", "close": "\x1b[29m"},
    "underline": {"open": "\x1b[4m", "close": "\x1b[24m"},
}


def _highlight_fn(spec: dict[str, Any] | None) -> Callable[[str, str | None], list[str]] | None:
    """Test double for a syntax highlighter — one styled line per code line.

    Tags each line with the language so a dropped `lang` argument is visible.
    """
    if not spec:
        return None
    open_seq = spec["open"]
    close_seq = spec["close"]

    def highlight(code: str, lang: str | None = None) -> list[str]:
        return [f"{open_seq}{lang or ''}|{line}{close_seq}" for line in code.split("\n")]

    return highlight


#: `test-themes.ts` builds the editor border from `chalk.dim`.
DEFAULT_EDITOR_BORDER = {"open": "\x1b[2m", "close": "\x1b[22m"}

#: Capabilities the goldens are captured under, so the capture machine's
#: TERM_PROGRAM cannot change what is recorded. Mirrors `reference/dump.ts`.
DEFAULT_CAPABILITIES: dict[str, Any] = {"images": None, "trueColor": True, "hyperlinks": False}

#: The cell size images are measured against. `terminal-image.ts` starts here
#: and only the TUI's cell-size query moves it, so pinning it per scenario keeps
#: a stray `set_cell_dimensions` in an earlier test out of the goldens.
DEFAULT_CELL_DIMENSIONS: dict[str, int] = {"widthPx": 9, "heightPx": 18}


def apply_scenario_capabilities(spec: dict[str, Any]) -> None:
    """Pin the terminal capabilities and cell size a scenario renders under.

    Both are process-global in `terminal-image.ts` and read at *render* time
    (markdown links pick OSC 8 over `text (url)` from the first; an image's row
    count comes from the second), so the corpus states them per scenario and
    both interpreters apply them the same way, before building.
    """
    from cortex.tui.images import CellDimensions, TerminalCapabilities
    from cortex.tui.images import set_capabilities as _set_capabilities
    from cortex.tui.images import set_cell_dimensions as _set_cell_dimensions

    caps = {**DEFAULT_CAPABILITIES, **(spec.get("capabilities") or {})}
    _set_capabilities(
        TerminalCapabilities(
            images=caps["images"],
            true_color=caps["trueColor"],
            hyperlinks=caps["hyperlinks"],
        )
    )
    cells = {**DEFAULT_CELL_DIMENSIONS, **(spec.get("cellDimensions") or {})}
    _set_cell_dimensions(CellDimensions(width_px=cells["widthPx"], height_px=cells["heightPx"]))


def _select_list_theme(spec: dict[str, Any] | None) -> Any:
    """The select-list theme, shared by `SelectList` and the editor's autocomplete."""
    from cortex.tui.components import SelectListTheme

    theme = spec or {}
    return SelectListTheme(
        selected_prefix=_wrap_fn(theme.get("selectedPrefix")),
        selected_text=_wrap_fn(theme.get("selectedText")),
        description=_wrap_fn(theme.get("description")),
        scroll_info=_wrap_fn(theme.get("scrollInfo")),
        no_match=_wrap_fn(theme.get("noMatch")),
    )


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
    if name == "Editor":
        from cortex.tui.components import Editor, EditorOptions, EditorTheme
        from cortex.tui.render import TUI
        from cortex.tui.testkit._capture import CaptureTerminal

        theme_spec = args.get("theme", {})
        # The editor reads `tui.terminal.rows` (30% of it caps the visible lines)
        # and calls `request_render`, so it needs a real TUI. The terminal is the
        # same recording stub the renderer scenarios use; nothing it writes is
        # part of a component golden.
        terminal = CaptureTerminal(args.get("cols", spec["width"]), args.get("rows", 24))
        editor = Editor(
            TUI(terminal, False),  # pyright: ignore[reportArgumentType]
            EditorTheme(
                border_color=_wrap_fn(theme_spec.get("borderColor", DEFAULT_EDITOR_BORDER)),
                select_list=_select_list_theme(theme_spec.get("selectList")),
            ),
            EditorOptions(
                padding_x=args.get("paddingX"),
                autocomplete_max_visible=args.get("autocompleteMaxVisible"),
            ),
        )
        if args.get("text") is not None:
            editor.set_text(args["text"])
        if args.get("promptPrefix") is not None:
            editor.prompt_prefix = args["promptPrefix"]
        if args.get("promptColor") is not None:
            editor.prompt_color = _wrap_fn(args["promptColor"])
        if args.get("disableSubmit"):
            editor.disable_submit = True
        return editor
    if name == "SelectList":
        from cortex.tui.components import (
            SelectItem,
            SelectList,
            SelectListLayoutOptions,
        )

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
            _select_list_theme(args.get("theme")),
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
    if name == "Image":
        from cortex.tui.images import Image, ImageDimensions, ImageOptions, ImageTheme

        dimensions_spec = args.get("dimensions")
        return Image(
            args.get("data", ""),
            args.get("mimeType", "image/png"),
            ImageTheme(fallback_color=_wrap_fn(args.get("fallbackColor"))),
            ImageOptions(
                max_width_cells=args.get("maxWidthCells"),
                max_height_cells=args.get("maxHeightCells"),
                filename=args.get("filename"),
                image_id=args.get("imageId"),
            ),
            ImageDimensions(
                width_px=dimensions_spec["widthPx"], height_px=dimensions_spec["heightPx"]
            )
            if dimensions_spec
            else None,
        )
    if name == "Markdown":
        from cortex.tui.components import DefaultTextStyle, Markdown, MarkdownTheme

        theme_spec = {**DEFAULT_MARKDOWN_THEME, **(args.get("theme") or {})}
        style_spec = args.get("defaultTextStyle")
        return Markdown(
            args.get("text", ""),
            args.get("paddingX", 1),
            args.get("paddingY", 1),
            MarkdownTheme(
                heading=_wrap_fn(theme_spec["heading"]),
                link=_wrap_fn(theme_spec["link"]),
                link_url=_wrap_fn(theme_spec["linkUrl"]),
                code=_wrap_fn(theme_spec["code"]),
                code_block=_wrap_fn(theme_spec["codeBlock"]),
                code_block_border=_wrap_fn(theme_spec["codeBlockBorder"]),
                quote=_wrap_fn(theme_spec["quote"]),
                quote_border=_wrap_fn(theme_spec["quoteBorder"]),
                hr=_wrap_fn(theme_spec["hr"]),
                list_bullet=_wrap_fn(theme_spec["listBullet"]),
                bold=_wrap_fn(theme_spec["bold"]),
                italic=_wrap_fn(theme_spec["italic"]),
                strikethrough=_wrap_fn(theme_spec["strikethrough"]),
                underline=_wrap_fn(theme_spec["underline"]),
                highlight_code=_highlight_fn(args.get("highlightCode")),
                code_block_indent=args.get("codeBlockIndent"),
            ),
            DefaultTextStyle(
                color=_bg_fn(style_spec.get("color")),
                bg_color=_bg_fn(style_spec.get("bgColor")),
                bold=style_spec.get("bold", False),
                italic=style_spec.get("italic", False),
                strikethrough=style_spec.get("strikethrough", False),
                underline=style_spec.get("underline", False),
            )
            if style_spec
            else None,
        )
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

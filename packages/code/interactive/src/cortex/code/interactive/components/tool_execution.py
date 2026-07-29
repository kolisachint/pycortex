"""One tool call, as a block in the chat log.

Port of ``components/tool-execution.ts``. A block is created the moment the model
starts streaming a tool call and lives until the result lands, changing three
times on the way: the arguments fill in, execution starts, the result arrives —
each of which redraws it. The status dot in front of the first line is the whole
state machine in one glyph: yellow while partial, green on success, red on error.

What it draws comes from the tool's *renderer*
(:mod:`cortex.code.interactive.tool_renderers`), not from here. With no renderer
for the name, :meth:`ToolExecutionComponent._format_tool_execution` is the
fallback: the tool's name, its arguments as JSON, and its text output.

Two behaviours are worth naming because they are not obvious from the shape:

* **Expansion is two settings, not one.** ``display_level`` is the persisted
  choice (``standard`` shows the result, ``collapsed`` never does, ``peek`` hides
  it behind a ``▸``), and ``revealed``/``expanded`` is what the global expand key
  flips. For a *standard* block the key switches truncated ↔ full; for the other
  two it is what makes the body appear at all.
* **A finished block far above the viewport is frozen.** :meth:`freeze` marks it;
  the next render captures its lines and releases the result, the base64 image
  copies and the child components. A frozen block never reflows again, which is
  the trade: old scrollback keeps the wrapping it was drawn at, and any full
  rebuild (theme toggle, session reload) restores it from the intact session
  data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from cortex.code.interactive.theme import get_theme
from cortex.code.interactive.tool_renderers import (
    ToolRenderContext,
    ToolRenderer,
    ToolRenderResultOptions,
)
from cortex.tui.components import Box, Spacer, Text
from cortex.tui.images import Image, ImageOptions, ImageTheme, get_capabilities, is_image_line
from cortex.tui.render import Component, Container
from cortex.tui.util import truncate_to_width, visible_width

__all__ = [
    "PrefixFirstLine",
    "ToolExecutionComponent",
    "ToolExecutionOptions",
    "ToolExecutionResult",
]

#: How a tool block's result body is displayed:
#:
#: * ``"standard"`` — result shown (truncated preview, expandable); the default.
#: * ``"collapsed"`` — result hidden; only the call line + status dot render.
#: * ``"peek"`` — result hidden with a ``▸`` affordance the expand key reveals.
ToolOutputDisplayLevel = Literal["collapsed", "peek", "standard"]


@dataclass
class ToolExecutionResult:
    """A tool result as the block holds it: the payload plus whether it failed.

    The TS spreads ``{...event.result, isError: event.isError}`` into one object
    because it can; a dataclass is that object. ``is_error`` never reaches a
    renderer through here — it goes down
    :attr:`~cortex.code.interactive.tool_renderers.ToolRenderContext.is_error`,
    and :meth:`ToolExecutionComponent.result_payload` is what the renderer sees.
    """

    content: list[Any] = field(default_factory=list)
    details: Any = None
    is_error: bool = False


@dataclass(frozen=True)
class _ResultPayload:
    """``{content, details}`` — what ``render_result`` is handed."""

    content: list[Any]
    details: Any


class ToolExecutionOptions:
    """Display options for a tool block."""

    def __init__(
        self,
        show_images: bool = True,
        image_width_cells: int = 60,
        display_level: ToolOutputDisplayLevel = "standard",
    ) -> None:
        self.show_images = show_images
        self.image_width_cells = image_width_cells
        self.display_level: ToolOutputDisplayLevel = display_level


class PrefixFirstLine:
    """Render a child and put *prefix* in front of its first line.

    The status dot has to sit *inline* with the call line. Adding it to the
    container as a sibling would stack it on a line of its own, so it is prepended
    here and continuation lines are indented to match — which also means the child
    must be rendered at a narrower width, or it wraps into the indent.
    """

    def __init__(self, prefix: str, child: Component) -> None:
        self.prefix = prefix
        self.child = child
        self.indent_width = visible_width(prefix)
        # Keyed on the child's returned list *identity*, so an unchanged child
        # keeps this wrapper reference-stable too — parents memoize flattening
        # by reference.
        self._memo: tuple[list[str], int, list[str]] | None = None

    def invalidate(self) -> None:
        invalidate = getattr(self.child, "invalidate", None)
        if callable(invalidate):
            invalidate()
        self._memo = None

    def render(self, width: int) -> list[str]:
        # Reserve room for the prefix so the child wraps within what is left.
        child_width = max(1, width - self.indent_width)
        lines = self.child.render(child_width)
        if self._memo is not None and self._memo[0] is lines and self._memo[1] == width:
            return self._memo[2]
        indent = " " * self.indent_width
        if not lines:
            out = [self.prefix]
        else:
            out = [
                (self.prefix + line) if i == 0 else (indent + line) for i, line in enumerate(lines)
            ]
        self._memo = (lines, width, out)
        return out


def _content_blocks(result: Any) -> list[Any]:
    if result is None:
        return []
    content = (
        result.get("content") if isinstance(result, dict) else getattr(result, "content", None)
    )
    return list(content) if content else []


def _block_field(block: Any, name: str) -> Any:
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


def _is_error(result: Any) -> bool:
    if result is None:
        return False
    value = (
        result.get("is_error") if isinstance(result, dict) else getattr(result, "is_error", None)
    )
    return bool(value)


class ToolExecutionComponent(Container):
    """The chat-log block for one tool call."""

    def __init__(
        self,
        tool_name: str,
        tool_call_id: str,
        args: Any,
        options: ToolExecutionOptions | None = None,
        renderer: ToolRenderer | None = None,
        ui: Any = None,
        cwd: str = "",
    ) -> None:
        super().__init__()
        resolved = options if options is not None else ToolExecutionOptions()
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self.args = args
        self.renderer = renderer
        self.show_images = resolved.show_images
        self.image_width_cells = resolved.image_width_cells
        self.display_level: ToolOutputDisplayLevel = resolved.display_level
        self.ui = ui
        self.cwd = cwd

        self.expanded = False
        #: The per-block override the global expand key flips for
        #: collapsed/peek blocks.
        self.revealed = False
        self.is_partial = True
        self.execution_started = False
        self.args_complete = False
        self.result: Any = None

        self._call_renderer_component: Component | None = None
        self._result_renderer_component: Component | None = None
        self._renderer_state: dict[str, Any] = {}
        self._image_components: list[Image] = []
        self._image_spacers: list[Spacer] = []
        self._hide_component = False

        self._frozen = False
        self._frozen_lines: list[str] | None = None
        #: Width the frozen snapshot was captured at. A wider terminal can take
        #: it as-is; a narrower one needs re-truncation, or the renderer's
        #: over-width guard trips.
        self._frozen_width = 0
        self._frozen_truncated: list[str] | None = None
        self._frozen_truncated_width = -1

        self.add_child(Spacer(1))

        # All three shells are built up front. `content_box` frames a renderer's
        # output, `self_render_container` gets out of the way when the tool
        # frames itself, and `content_text` is the no-renderer fallback.
        # Boxless: no background fill — the hierarchy is the dot and the indent.
        # padding_y is 0 because the single leading Spacer(1) is the only gap
        # between blocks; anything more stacks three blank lines between two
        # consecutive commands.
        self.content_box = Box(1, 0)
        self.content_text = Text("", 1, 0)
        self.self_render_container = Container()

        if self.renderer is not None:
            self.add_child(
                self.self_render_container if self._render_shell() == "self" else self.content_box
            )
        else:
            self.add_child(self.content_text)

        self.update_display()

    # ---- renderer resolution ---------------------------------------------

    def _render_shell(self) -> str:
        if self.renderer is None or self.renderer.render_shell is None:
            return "default"
        return self.renderer.render_shell

    def _render_context(self, last_component: Component | None) -> ToolRenderContext:
        return ToolRenderContext(
            args=self.args,
            tool_call_id=self.tool_call_id,
            invalidate=self._invalidate_and_render,
            last_component=last_component,
            state=self._renderer_state,
            cwd=self.cwd,
            execution_started=self.execution_started,
            args_complete=self.args_complete,
            is_partial=self.is_partial,
            expanded=self.expanded,
            show_images=self.show_images,
            is_error=_is_error(self.result),
        )

    def _invalidate_and_render(self) -> None:
        self.invalidate()
        if self.ui is not None:
            self.ui.request_render()

    def _create_call_fallback(self) -> Component:
        theme = get_theme()
        return Text(theme.fg("toolTitle", theme.bold(self.tool_name)), 0, 0)

    def _create_result_fallback(self) -> Component | None:
        output = self.get_text_output()
        if not output:
            return None
        return Text(get_theme().fg("toolOutput", output), 0, 0)

    # ---- state changes ----------------------------------------------------

    def update_args(self, args: Any) -> None:
        self.args = args
        self.update_display()

    def mark_execution_started(self) -> None:
        self.execution_started = True
        self.update_display()
        if self.ui is not None:
            self.ui.request_render()

    def set_args_complete(self) -> None:
        self.args_complete = True
        self.update_display()
        if self.ui is not None:
            self.ui.request_render()

    def update_result(self, result: ToolExecutionResult, is_partial: bool = False) -> None:
        self.result = result
        self.is_partial = is_partial
        self.update_display()

    def result_payload(self) -> _ResultPayload:
        """The ``{content, details}`` half of the result, for a renderer."""
        return _ResultPayload(
            content=_content_blocks(self.result),
            details=getattr(self.result, "details", None),
        )

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        # For collapsed/peek blocks the same global toggle reveals the hidden
        # body; for standard blocks `expanded` alone switches truncated ↔ full.
        self.revealed = expanded
        self.update_display()

    def set_display_level(self, level: ToolOutputDisplayLevel) -> None:
        self.display_level = level
        self.update_display()

    def _should_show_body(self) -> bool:
        """Whether the result body renders, given the level and the reveal."""
        return self.display_level == "standard" or self.revealed

    def set_show_images(self, show: bool) -> None:
        self.show_images = show
        self.update_display()

    def set_image_width_cells(self, width: int) -> None:
        self.image_width_cells = max(1, int(width))
        self.update_display()

    def invalidate(self) -> None:
        # A frozen block has released the state update_display would rebuild
        # from; its snapshot is authoritative, so skip the rebuild entirely.
        if self._frozen:
            return
        super().invalidate()
        self.update_display()

    # ---- freezing ---------------------------------------------------------

    def is_freezable(self) -> bool:
        """Whether this is a finished, visible block still holding its payloads."""
        return (
            not self._frozen
            and not self.is_partial
            and not self._hide_component
            and self.result is not None
        )

    def freeze(self) -> None:
        """Mark for freezing; the snapshot is captured on the next render."""
        if self.is_freezable():
            self._frozen = True

    def _release_heavy_state(self) -> None:
        self.result = None
        self._image_components = []
        self._image_spacers = []
        self._renderer_state = {}
        self._call_renderer_component = None
        self._result_renderer_component = None
        # Drop the children so their line caches — which hold base64 image
        # payloads and the full text output — become collectable.
        self.clear()

    # ---- rendering --------------------------------------------------------

    def render(self, width: int) -> list[str]:
        if self._hide_component:
            return []
        if self._frozen_lines is not None:
            if width >= self._frozen_width:
                return self._frozen_lines
            if self._frozen_truncated_width == width and self._frozen_truncated is not None:
                return self._frozen_truncated
            self._frozen_truncated = [
                line
                if is_image_line(line) or visible_width(line) <= width
                else truncate_to_width(line, width)
                for line in self._frozen_lines
            ]
            self._frozen_truncated_width = width
            return self._frozen_truncated
        lines = super().render(width)
        if self._frozen:
            self._frozen_lines = lines
            self._frozen_width = width
            self._release_heavy_state()
        return lines

    def update_display(self) -> None:  # noqa: C901 - the TS's one function, one for one
        # Frozen blocks are immutable snapshots with their source released.
        if self._frozen:
            return
        theme = get_theme()
        has_content = False
        self._hide_component = False

        if self.renderer is not None:
            render_container: Any = (
                self.self_render_container if self._render_shell() == "self" else self.content_box
            )
            # Boxless: no background fill on the container.
            if isinstance(render_container, Box):
                render_container.set_bg_fn(None)
            render_container.clear()

            # Green for a clean finish, yellow while pending or partial, red on
            # error. Peek blocks advertise their hidden body with a caret first.
            dot_color = (
                "error" if _is_error(self.result) else "warning" if self.is_partial else "success"
            )
            caret = (
                theme.fg("muted", "▾ " if self.revealed else "▸ ")
                if self.display_level == "peek"
                else ""
            )
            dot = caret + theme.fg(dot_color, "● ")

            call_renderer = self.renderer.render_call
            if call_renderer is None:
                render_container.add_child(PrefixFirstLine(dot, self._create_call_fallback()))
                has_content = True
            else:
                try:
                    component = call_renderer(
                        self.args, theme, self._render_context(self._call_renderer_component)
                    )
                    self._call_renderer_component = component
                    render_container.add_child(PrefixFirstLine(dot, component))
                except Exception:  # noqa: BLE001 - the TS's bare catch: never let a renderer break the log
                    self._call_renderer_component = None
                    render_container.add_child(PrefixFirstLine(dot, self._create_call_fallback()))
                has_content = True

            if self.result is not None and self._should_show_body():
                result_renderer = self.renderer.render_result
                if result_renderer is None:
                    component = self._create_result_fallback()
                    if component is not None:
                        render_container.add_child(component)
                        has_content = True
                else:
                    try:
                        component = result_renderer(
                            self.result_payload(),
                            ToolRenderResultOptions(
                                expanded=self.expanded, is_partial=self.is_partial
                            ),
                            theme,
                            self._render_context(self._result_renderer_component),
                        )
                        self._result_renderer_component = component
                        render_container.add_child(component)
                        has_content = True
                    except Exception:  # noqa: BLE001 - as above
                        self._result_renderer_component = None
                        component = self._create_result_fallback()
                        if component is not None:
                            render_container.add_child(component)
                            has_content = True
        else:
            # Boxless: no background fill.
            self.content_text.set_custom_bg_fn(None)
            self.content_text.set_text(self._format_tool_execution())
            has_content = True

        for image in self._image_components:
            self.remove_child(image)
        self._image_components = []
        for spacer in self._image_spacers:
            self.remove_child(spacer)
        self._image_spacers = []

        if self.result is not None:
            image_blocks = [
                b for b in _content_blocks(self.result) if _block_field(b, "type") == "image"
            ]
            caps = get_capabilities()
            for block in image_blocks:
                data = _block_field(block, "data")
                mime_type = _block_field(block, "mime_type") or _block_field(block, "mimeType")
                if not (caps.images and self.show_images and data and mime_type):
                    continue
                # `utils/image-convert.ts` is not ported, so a non-PNG under the
                # kitty protocol is skipped rather than converted (the TS's
                # `maybeConvertImagesForKitty` is the other half of this branch).
                if caps.images == "kitty" and mime_type != "image/png":
                    continue
                spacer = Spacer(1)
                self.add_child(spacer)
                self._image_spacers.append(spacer)
                image_component = Image(
                    data,
                    mime_type,
                    ImageTheme(fallback_color=lambda s: get_theme().fg("toolOutput", s)),
                    ImageOptions(max_width_cells=self.image_width_cells),
                )
                self._image_components.append(image_component)
                self.add_child(image_component)

        if self.renderer is not None and not has_content and not self._image_components:
            self._hide_component = True

    def get_text_output(self) -> str:
        from cortex.code.tools import get_text_output

        return str(get_text_output(self.result, self.show_images))

    def _format_tool_execution(self) -> str:
        """The no-renderer fallback: name, arguments, output."""
        theme = get_theme()
        dot_color = (
            "error" if _is_error(self.result) else "warning" if self.is_partial else "success"
        )
        caret = (
            theme.fg("muted", "▾ " if self.revealed else "▸ ")
            if self.display_level == "peek"
            else ""
        )
        text = caret + theme.fg(dot_color, "● ") + theme.fg("toolTitle", theme.bold(self.tool_name))
        content = _stringify_args(self.args)
        if content:
            text += f"\n\n{content}"
        output = self.get_text_output() if self._should_show_body() else ""
        if output:
            text += f"\n{output}"
        return text


def _stringify_args(args: Any) -> str:
    """``JSON.stringify(args, null, 2)``, for whatever the model sent.

    ``JSON.stringify(undefined)`` is ``undefined`` in the TS and the caller drops
    it; the Python equivalent of that falsy result is the empty string.
    """
    if args is None:
        return ""
    try:
        return json.dumps(args, indent=2, default=str)
    except (TypeError, ValueError):
        return str(args)

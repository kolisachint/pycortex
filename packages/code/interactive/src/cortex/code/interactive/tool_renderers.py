"""How a tool block draws itself, and the built-in renderers there are.

:class:`~cortex.code.interactive.components.tool_execution.ToolExecutionComponent`
does not know what a `read` or an `edit` looks like — it draws a status dot, an
indent, and whatever the tool's *renderer* returns. In the TS that renderer is
the ``renderCall`` / ``renderResult`` pair on a ``ToolDefinition``, resolved from
two places and merged field by field:

1. the definition registered for the session (an extension's, or a replacement
   for a built-in), and
2. the built-in definition for that name, from ``createAllToolDefinitions``.

:class:`ToolRenderer` is that pair, and :func:`resolve_tool_renderer` is that
merge. What is *not* here is most of the second source: the built-in renderers
live on the tool definitions in ``core/tools/*.ts``, and
:mod:`cortex.code.tools` ported those files at execute level only — its module
docstring says as much. So :data:`BUILT_IN_TOOL_RENDERERS` holds the one step 7.6
owes, `edit`, because ``components/diff.ts`` is in this step's file list and
exists for nothing else, and "edits render as diffs" is what the step promises.
Every other tool falls to :class:`ToolExecutionComponent`'s own fallback — the
tool's name, its arguments and its text output — which is exactly what the TS
falls back to for a tool nobody wrote a renderer for.

The `edit` renderer is the TS's, with one difference forced by the port:
``computeEditsDiff`` is a promise there and a plain call here
(:func:`cortex.code.tools.compute_edits_diff`), so the preview is computed
inline instead of landing in a ``.then`` that calls ``context.invalidate()``.
The ``preview_args_key`` guard still matters and is still here — it is what stops
a re-render from re-reading the file.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from cortex.code.interactive.components.diff import render_diff
from cortex.tui.components import Box, Spacer, Text
from cortex.tui.render import Component, Container

__all__ = [
    "BUILT_IN_TOOL_RENDERERS",
    "EditCallComponent",
    "ToolRenderContext",
    "ToolRenderResultOptions",
    "ToolRenderer",
    "get_built_in_tool_renderer",
    "resolve_tool_renderer",
]

RenderShell = Literal["default", "self"]


@dataclass
class ToolRenderContext:
    """Everything a renderer knows about the tool call it is drawing.

    Port of ``ToolRenderContext``. ``last_component`` and ``state`` are what let
    a renderer be incremental: it may mutate and return the component it
    returned last time rather than building a new one on every delta.
    """

    #: Current tool call arguments, shared across call and result renders.
    args: Any
    #: Unique id for this tool execution, stable across renders.
    tool_call_id: str
    #: Invalidate just this tool block and ask for a frame.
    invalidate: Callable[[], None]
    #: The component this renderer returned last time, if any.
    last_component: Component | None
    #: Renderer-owned state for this tool row, initialised by the component.
    state: dict[str, Any]
    #: Working directory for this tool execution.
    cwd: str
    #: Whether the tool has begun executing.
    execution_started: bool
    #: Whether the streamed arguments are complete.
    args_complete: bool
    #: Whether the result so far is partial.
    is_partial: bool
    #: Whether the result view is expanded.
    expanded: bool
    #: Whether inline images are currently shown.
    show_images: bool
    #: Whether the current result is an error.
    is_error: bool


@dataclass(frozen=True)
class ToolRenderResultOptions:
    """The view state a ``render_result`` is drawing under."""

    expanded: bool = False
    is_partial: bool = False


RenderCall = Callable[[Any, Any, ToolRenderContext], Component]
RenderResult = Callable[[Any, ToolRenderResultOptions, Any, ToolRenderContext], Component]


@dataclass(frozen=True)
class ToolRenderer:
    """The render half of a ``ToolDefinition``.

    ``render_shell`` picks the framing: ``"default"`` puts the renderer's
    components inside the block's own indented box, ``"self"`` hands the whole
    block over to the tool.
    """

    render_call: RenderCall | None = None
    render_result: RenderResult | None = None
    render_shell: RenderShell | None = None


@runtime_checkable
class ToolDefinitionSource(Protocol):
    """The slice of the session a tool block reads its renderers from."""

    def get_tool_definition(self, tool_name: str) -> Any: ...


def _renderer_of(definition: Any) -> ToolRenderer | None:
    """Read a :class:`ToolRenderer` off whatever the session handed back."""
    if definition is None:
        return None
    if isinstance(definition, ToolRenderer):
        return definition
    render_call = getattr(definition, "render_call", None)
    render_result = getattr(definition, "render_result", None)
    render_shell = getattr(definition, "render_shell", None)
    if render_call is None and render_result is None and render_shell is None:
        return None
    return ToolRenderer(
        render_call=render_call, render_result=render_result, render_shell=render_shell
    )


def get_built_in_tool_renderer(tool_name: str) -> ToolRenderer | None:
    """The built-in renderer for *tool_name*, if this port has one."""
    return BUILT_IN_TOOL_RENDERERS.get(tool_name)


def resolve_tool_renderer(session: Any, tool_name: str) -> ToolRenderer | None:
    """Merge the session's renderer for *tool_name* over the built-in one.

    Field by field, as the TS's three ``get*Renderer`` accessors do: a
    registered definition that only overrides ``render_call`` still gets the
    built-in ``render_result``.
    """
    registered: ToolRenderer | None = None
    getter = getattr(session, "get_tool_definition", None)
    if callable(getter):
        registered = _renderer_of(getter(tool_name))
    built_in = get_built_in_tool_renderer(tool_name)

    if built_in is None:
        return registered
    if registered is None:
        return built_in
    return ToolRenderer(
        render_call=registered.render_call or built_in.render_call,
        render_result=registered.render_result or built_in.render_result,
        render_shell=registered.render_shell or built_in.render_shell,
    )


# ---------------------------------------------------------------------------
# edit — the one built-in renderer this step owes
# ---------------------------------------------------------------------------


def _str_value(value: Any) -> str | None:
    """``str`` from ``render-utils``: "" for missing, ``None`` for a non-string."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return None


def _arg(args: Any, name: str) -> Any:
    """One argument, whether the call arrived as a dict or an object."""
    if isinstance(args, dict):
        return args.get(name)
    return getattr(args, name, None)


def _coalesce(*values: Any) -> Any:
    """``a ?? b`` — the first value that is not ``None``."""
    for value in values:
        if value is not None:
            return value
    return None


def _details_field(result: Any, name: str) -> Any:
    details = (
        result.get("details") if isinstance(result, dict) else getattr(result, "details", None)
    )
    if details is None:
        return None
    if isinstance(details, dict):
        return details.get(name)
    return getattr(details, name, None)


def _result_text(result: Any) -> str:
    from cortex.code.tools import get_text_output

    return str(get_text_output(result))


def _renderable_preview_input(args: Any) -> tuple[str, list[Any]] | None:
    """``(path, edits)`` when the streamed arguments are complete enough to diff.

    Port of ``getRenderablePreviewInput``, including the legacy single-edit
    (``oldText``/``newText``) shape some models still send.
    """
    from cortex.code.tools import Edit

    if args is None:
        return None

    raw_path = _arg(args, "path")
    if not isinstance(raw_path, str):
        raw_path = _arg(args, "file_path")
    if not isinstance(raw_path, str):
        return None

    edits = _arg(args, "edits")
    if isinstance(edits, list) and edits:
        converted: list[Any] = []
        for edit in edits:
            old_text = _arg(edit, "old_text")
            if not isinstance(old_text, str):
                old_text = _arg(edit, "oldText")
            new_text = _arg(edit, "new_text")
            if not isinstance(new_text, str):
                new_text = _arg(edit, "newText")
            if not isinstance(old_text, str) or not isinstance(new_text, str):
                return None
            replace_all = _coalesce(_arg(edit, "replace_all"), _arg(edit, "replaceAll"))
            converted.append(
                Edit(old_text=old_text, new_text=new_text, replace_all=replace_all is True)
            )
        return raw_path, converted

    old_text = _coalesce(_arg(args, "old_text"), _arg(args, "oldText"))
    new_text = _coalesce(_arg(args, "new_text"), _arg(args, "newText"))
    if isinstance(old_text, str) and isinstance(new_text, str):
        return raw_path, [Edit(old_text=old_text, new_text=new_text)]

    return None


def _preview_args_key(preview_input: tuple[str, list[Any]] | None) -> str | None:
    if preview_input is None:
        return None
    path, edits = preview_input
    return json.dumps(
        {
            "path": path,
            "edits": [
                {"oldText": e.old_text, "newText": e.new_text, "replaceAll": e.replace_all}
                for e in edits
            ],
        },
        sort_keys=True,
    )


def _format_edit_call(args: Any, theme: Any) -> str:
    """``edit <path>`` — the path accented, or a placeholder while it streams."""
    from cortex.code.tools import shorten_path

    raw_path = _str_value(_coalesce(_arg(args, "file_path"), _arg(args, "path")))
    if raw_path is None:
        path_display = theme.fg("error", "[invalid arg]")
    elif raw_path:
        path_display = theme.fg("accent", shorten_path(raw_path))
    else:
        path_display = theme.fg("toolOutput", "...")
    return f"{theme.fg('toolTitle', theme.bold('edit'))} {path_display}"


class EditCallComponent(Box):
    """The `edit` call line, with the diff of what it will change underneath.

    ``renderShell: "self"`` means this box *is* the tool block's frame, which is
    why it carries the background: pending while the edit is in flight, green
    once it lands, red if it fails.
    """

    def __init__(self) -> None:
        super().__init__(1, 0, lambda text: text)
        #: An :class:`~cortex.code.tools.EditDiffResult` or
        #: :class:`~cortex.code.tools.EditDiffError`, once one has been computed.
        self.preview: Any = None
        self.preview_args_key: str | None = None
        self.settled_error: bool = False


def _preview_error(preview: Any) -> str | None:
    """The message of an :class:`EditDiffError`, or ``None`` for a real diff."""
    return getattr(preview, "error", None) if preview is not None else None


def _edit_header_bg(component: EditCallComponent, theme: Any) -> Callable[[str], str]:
    if component.preview is not None:
        if _preview_error(component.preview) is not None:
            return lambda text: theme.bg("toolErrorBg", text)
        return lambda text: theme.bg("toolSuccessBg", text)
    if component.settled_error:
        return lambda text: theme.bg("toolErrorBg", text)
    return lambda text: theme.bg("toolPendingBg", text)


def _build_edit_call_component(
    component: EditCallComponent, args: Any, theme: Any
) -> EditCallComponent:
    component.set_bg_fn(_edit_header_bg(component, theme))
    component.clear()
    component.add_child(Text(_format_edit_call(args, theme), 0, 0))

    if component.preview is None:
        return component

    error = _preview_error(component.preview)
    body = theme.fg("error", error) if error is not None else render_diff(component.preview.diff)
    component.add_child(Spacer(1))
    component.add_child(Text(body, 0, 0))
    return component


def _set_edit_preview(component: EditCallComponent, preview: Any, args_key: str | None) -> bool:
    """Store *preview* on *component*; report whether it is different."""
    current = component.preview
    current_error = _preview_error(current)
    new_error = _preview_error(preview)
    if current is None:
        changed = True
    elif (current_error is None) != (new_error is None):
        changed = True
    elif current_error is not None:
        changed = current_error != new_error
    else:
        changed = (
            current.diff != preview.diff or current.first_changed_line != preview.first_changed_line
        )
    component.preview = preview
    component.preview_args_key = args_key
    return changed


def _edit_call_component(state: dict[str, Any], last_component: Any) -> EditCallComponent:
    if isinstance(last_component, EditCallComponent):
        state["call_component"] = last_component
        return last_component
    existing = state.get("call_component")
    if isinstance(existing, EditCallComponent):
        return existing
    component = EditCallComponent()
    state["call_component"] = component
    return component


def _render_edit_call(args: Any, theme: Any, context: ToolRenderContext) -> Component:
    """``render_call`` for `edit`: the call line, plus a preview of the diff.

    The preview is the point. An edit is the one tool whose *arguments* say what
    will change, so the block can show the diff before the write happens — and
    once it has, the result renderer has nothing left to add.
    """
    from cortex.code.tools import compute_edits_diff

    component = _edit_call_component(context.state, context.last_component)
    preview_input = _renderable_preview_input(args)
    args_key = _preview_args_key(preview_input)

    if component.preview_args_key != args_key:
        component.preview = None
        component.preview_args_key = args_key
        component.settled_error = False

    if context.args_complete and preview_input is not None and component.preview is None:
        path, edits = preview_input
        _set_edit_preview(component, compute_edits_diff(path, edits, context.cwd), args_key)

    return _build_edit_call_component(component, args, theme)


def _format_edit_result(args: Any, preview: Any, result: Any, theme: Any, is_error: bool) -> str:
    """What the result adds to what the call already showed, if anything."""
    preview_diff = None if _preview_error(preview) is not None else getattr(preview, "diff", None)
    preview_error = _preview_error(preview)

    if is_error:
        error_text = _result_text(result)
        if not error_text or error_text == preview_error:
            return ""
        return str(theme.fg("error", error_text))

    result_diff = _details_field(result, "diff")
    if isinstance(result_diff, str) and result_diff and result_diff != preview_diff:
        raw_path = _str_value(_coalesce(_arg(args, "file_path"), _arg(args, "path")))
        return render_diff(result_diff, raw_path)

    return ""


def _render_edit_result(
    result: Any,
    options: ToolRenderResultOptions,  # noqa: ARG001 - parity with the TS's `_options`
    theme: Any,
    context: ToolRenderContext,
) -> Component:
    """``render_result`` for `edit`.

    Usually draws *nothing*: the call component already shows the diff, and the
    real diff the tool reports is the one the preview predicted. What is left is
    the two cases where they differ — the edit failed, or it changed something
    the preview did not foresee.
    """
    call_component = context.state.get("call_component")
    preview_input = _renderable_preview_input(context.args)
    args_key = _preview_args_key(preview_input)
    result_diff = None if context.is_error else _details_field(result, "diff")

    changed = False
    if isinstance(call_component, EditCallComponent):
        if isinstance(result_diff, str):
            from cortex.code.tools import EditDiffResult

            changed = (
                _set_edit_preview(
                    call_component,
                    EditDiffResult(
                        diff=result_diff,
                        first_changed_line=_details_field(result, "first_changed_line"),
                    ),
                    args_key,
                )
                or changed
            )
        if call_component.settled_error != context.is_error:
            call_component.settled_error = context.is_error
            changed = True
        if changed:
            _build_edit_call_component(call_component, context.args, theme)

    preview = call_component.preview if isinstance(call_component, EditCallComponent) else None
    output = _format_edit_result(context.args, preview, result, theme, context.is_error)

    component = context.last_component if isinstance(context.last_component, Container) else None
    if component is None:
        component = Container()
    component.clear()
    if not output:
        return component
    component.add_child(Spacer(1))
    component.add_child(Text(output, 1, 0))
    return component


#: Built-in renderers, by tool name. See the module docstring for why `edit` is
#: the only entry.
BUILT_IN_TOOL_RENDERERS: dict[str, ToolRenderer] = {
    "edit": ToolRenderer(
        render_call=_render_edit_call,
        render_result=_render_edit_result,
        render_shell="self",
    ),
}

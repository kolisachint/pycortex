"""Tests for the tool block and the renderer seam behind it.

The corpus proves a real turn puts a real tool on screen; what is checked here is
everything a turn cannot easily reach — the state machine (partial → started →
result), the display levels, the fallback for a tool with no renderer, the
freeze, and the two guarantees the renderer seam owes: a renderer that throws
never breaks the log, and a registered renderer beats the built-in one field by
field.
"""

from __future__ import annotations

import re

from cortex.code.interactive.components.tool_execution import (
    PrefixFirstLine,
    ToolExecutionComponent,
    ToolExecutionOptions,
    ToolExecutionResult,
)
from cortex.code.interactive.tool_renderers import (
    ToolRenderContext,
    ToolRenderer,
    get_built_in_tool_renderer,
    resolve_tool_renderer,
)
from cortex.tui.components import Text
from cortex.tui.util import visible_width


def plain(lines: list[str]) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(lines))


def text_result(text: str, *, is_error: bool = False) -> ToolExecutionResult:
    from cortex.ai.types import TextContent

    return ToolExecutionResult(content=[TextContent(text=text)], details=None, is_error=is_error)


def block(**overrides: object) -> ToolExecutionComponent:
    options: dict[str, object] = {
        "tool_name": "grep",
        "tool_call_id": "call-1",
        "args": {"pattern": "needle"},
    }
    options.update(overrides)
    return ToolExecutionComponent(**options)  # type: ignore[arg-type]


class TestFallbackRendering:
    """No renderer for the name: the name, the arguments, the output."""

    def test_shows_the_tool_name(self):
        assert "grep" in plain(block().render(80))

    def test_shows_the_arguments_as_json(self):
        rendered = plain(block().render(80))
        assert '"pattern": "needle"' in rendered

    def test_shows_the_result_text_once_it_lands(self):
        component = block()
        component.update_result(text_result("3 matches"))
        assert "3 matches" in plain(component.render(80))

    def test_a_result_with_no_text_adds_nothing(self):
        component = block()
        component.update_result(ToolExecutionResult(content=[], details=None))
        assert "grep" in plain(component.render(80))

    def test_arguments_that_are_not_json_still_render(self):
        component = block(args=object())
        assert "grep" in plain(component.render(80))


def label_renderer() -> ToolRenderer:
    """A renderer that draws a line, so the *renderer* path is under test.

    The dot and the caret are built twice — once in `update_display` for a block
    with a renderer, once in `_format_tool_execution` for one without — and a
    test that only ever drives the fallback leaves half of that unwatched.
    """

    def draw(args: object, theme: object, ctx: ToolRenderContext) -> Text:
        return Text("CALL LINE", 0, 0)

    def draw_result(result: object, options: object, theme: object, ctx: object) -> Text:
        return Text("RESULT BODY", 0, 0)

    return ToolRenderer(render_call=draw, render_result=draw_result)


class TestStatusDot:
    def test_starts_pending(self):
        assert _dot_color(block()) == "warning"

    def test_turns_green_on_success(self):
        component = block()
        component.update_result(text_result("done"))
        assert _dot_color(component) == "success"

    def test_turns_red_on_error(self):
        component = block()
        component.update_result(text_result("boom", is_error=True))
        assert _dot_color(component) == "error"

    def test_a_partial_result_is_still_pending(self):
        component = block()
        component.update_result(text_result("half of it"), True)
        assert _dot_color(component) == "warning"

    def test_a_rendered_block_starts_pending_too(self):
        assert _dot_color(block(renderer=label_renderer())) == "warning"

    def test_a_rendered_block_turns_green_on_success(self):
        component = block(renderer=label_renderer())
        component.update_result(text_result("done"))
        assert _dot_color(component) == "success"

    def test_a_rendered_block_stays_pending_on_a_partial_result(self):
        component = block(renderer=label_renderer())
        component.update_result(text_result("half of it"), True)
        assert _dot_color(component) == "warning"

    def test_a_rendered_block_turns_red_on_error(self):
        component = block(renderer=label_renderer())
        component.update_result(text_result("boom", is_error=True))
        assert _dot_color(component) == "error"


def _dot_color(component: ToolExecutionComponent) -> str:
    """Which theme colour the block painted its dot with."""
    from cortex.code.interactive.theme import get_theme

    theme = get_theme()
    rendered = "\n".join(component.render(80))
    for name in ("error", "warning", "success"):
        if theme.fg(name, "● ") in rendered:
            return name
    raise AssertionError(f"no status dot in\n{rendered!r}")


class TestDisplayLevels:
    def test_standard_shows_the_body(self):
        component = block(options=ToolExecutionOptions(display_level="standard"))
        component.update_result(text_result("the body"))
        assert "the body" in plain(component.render(80))

    def test_collapsed_hides_the_body(self):
        component = block(options=ToolExecutionOptions(display_level="collapsed"))
        component.update_result(text_result("the body"))
        assert "the body" not in plain(component.render(80))

    def test_collapsed_stays_hidden_behind_no_affordance(self):
        component = block(options=ToolExecutionOptions(display_level="collapsed"))
        component.update_result(text_result("the body"))
        assert "▸" not in plain(component.render(80))

    def test_peek_hides_the_body_behind_a_caret(self):
        component = block(options=ToolExecutionOptions(display_level="peek"))
        component.update_result(text_result("the body"))
        rendered = plain(component.render(80))
        assert "▸" in rendered
        assert "the body" not in rendered

    def test_expanding_a_peek_block_reveals_it(self):
        component = block(options=ToolExecutionOptions(display_level="peek"))
        component.update_result(text_result("the body"))
        component.set_expanded(True)
        rendered = plain(component.render(80))
        assert "▾" in rendered
        assert "the body" in rendered

    def test_the_level_can_be_changed_after_construction(self):
        component = block()
        component.update_result(text_result("the body"))
        component.set_display_level("collapsed")
        assert "the body" not in plain(component.render(80))

    def test_a_rendered_block_hides_its_body_when_collapsed(self):
        component = block(
            renderer=label_renderer(), options=ToolExecutionOptions(display_level="collapsed")
        )
        component.update_result(text_result("ignored"))
        rendered = plain(component.render(80))
        assert "CALL LINE" in rendered
        assert "RESULT BODY" not in rendered

    def test_a_rendered_peek_block_advertises_its_body_with_a_caret(self):
        component = block(
            renderer=label_renderer(), options=ToolExecutionOptions(display_level="peek")
        )
        component.update_result(text_result("ignored"))
        rendered = plain(component.render(80))
        assert "▸" in rendered
        assert "RESULT BODY" not in rendered

        component.set_expanded(True)
        rendered = plain(component.render(80))
        assert "▾" in rendered
        assert "RESULT BODY" in rendered

    def test_a_rendered_standard_block_has_no_caret(self):
        component = block(renderer=label_renderer())
        component.update_result(text_result("ignored"))
        rendered = plain(component.render(80))
        assert "▸" not in rendered and "▾" not in rendered


class TestFreeze:
    def test_a_finished_block_is_freezable(self):
        component = block()
        component.update_result(text_result("done"))
        assert component.is_freezable()

    def test_an_unfinished_block_is_not(self):
        assert not block().is_freezable()

    def test_a_partial_result_is_not_freezable(self):
        component = block()
        component.update_result(text_result("half"), True)
        assert not component.is_freezable()

    def test_freezing_keeps_the_lines_and_drops_the_payload(self):
        component = block()
        component.update_result(text_result("done"))
        before = plain(component.render(80))
        component.freeze()
        after = plain(component.render(80))
        assert after == before
        assert component.result is None, "the payload survived the freeze"

    def test_a_frozen_block_stops_rebuilding(self):
        component = block()
        component.update_result(text_result("done"))
        component.freeze()
        component.render(80)
        component.update_result(text_result("something else"))
        assert "something else" not in plain(component.render(80))

    def test_a_narrower_terminal_re_truncates_rather_than_overflowing(self):
        component = block(args={"pattern": "x" * 200})
        component.update_result(text_result("done"))
        component.freeze()
        component.render(80)
        for line in component.render(40):
            assert visible_width(line) <= 40, f"a frozen line overflowed: {line!r}"

    def test_freezing_twice_is_harmless(self):
        component = block()
        component.update_result(text_result("done"))
        component.freeze()
        component.freeze()
        assert "done" in plain(component.render(80))


class TestPrefixFirstLine:
    def test_the_prefix_goes_on_the_first_line_only(self):
        wrapper = PrefixFirstLine("● ", Text("one\ntwo", 0, 0))
        assert [line.rstrip() for line in wrapper.render(40)] == ["● one", "  two"]

    def test_an_empty_child_still_shows_the_prefix(self):
        assert PrefixFirstLine("● ", Text("", 0, 0)).render(40) == ["● "]

    def test_the_child_is_rendered_narrower_so_its_wrap_clears_the_indent(self):
        # Two cells of prefix on a 12-cell terminal leaves the child 10; if it
        # were rendered at 12 the wrapped lines would run two cells past the edge.
        wrapper = PrefixFirstLine("● ", Text("aaaa bbbb cccc", 0, 0))
        lines = wrapper.render(12)
        assert len(lines) > 1, "the child did not wrap"
        assert lines[0].startswith("● ")
        assert all(line.startswith("  ") for line in lines[1:])
        assert all(visible_width(line) <= 12 for line in lines)

    def test_an_unchanged_child_keeps_the_wrapper_reference_stable(self):
        wrapper = PrefixFirstLine("● ", Text("one", 0, 0))
        assert wrapper.render(40) is wrapper.render(40)


class TestRendererSeam:
    def test_a_renderer_draws_instead_of_the_fallback(self):
        def draw(args: object, theme: object, ctx: ToolRenderContext) -> Text:
            return Text("CUSTOM", 0, 0)

        renderer = ToolRenderer(render_call=draw)
        rendered = plain(block(renderer=renderer).render(80))
        assert "CUSTOM" in rendered
        assert '"pattern"' not in rendered, "the fallback drew as well"

    def test_a_throwing_call_renderer_falls_back_to_the_name(self):
        def explode(args: object, theme: object, ctx: ToolRenderContext) -> Text:
            raise RuntimeError("renderer is broken")

        assert "grep" in plain(block(renderer=ToolRenderer(render_call=explode)).render(80))

    def test_a_throwing_result_renderer_falls_back_to_the_output(self):
        def explode(*_args: object) -> Text:
            raise RuntimeError("renderer is broken")

        component = block(renderer=ToolRenderer(render_result=explode))
        component.update_result(text_result("the raw output"))
        assert "the raw output" in plain(component.render(80))

    def test_the_context_carries_the_block_state(self):
        seen: list[ToolRenderContext] = []

        def capture(args: object, theme: object, ctx: ToolRenderContext) -> Text:
            seen.append(ctx)
            return Text("", 0, 0)

        component = block(renderer=ToolRenderer(render_call=capture), cwd="/w/project")
        component.set_args_complete()
        component.mark_execution_started()
        component.update_result(text_result("done"))

        last = seen[-1]
        assert last.tool_call_id == "call-1"
        assert last.cwd == "/w/project"
        assert last.args_complete and last.execution_started
        assert not last.is_partial and not last.is_error

    def test_the_result_renderer_sees_content_and_details_only(self):
        seen: list[object] = []

        def capture(result: object, options: object, theme: object, ctx: object) -> Text:
            seen.append(result)
            return Text("", 0, 0)

        component = block(renderer=ToolRenderer(render_result=capture))
        component.update_result(text_result("done", is_error=True))
        component.render(80)
        assert not hasattr(seen[-1], "is_error"), "is_error leaked into the renderer payload"

    def test_a_renderer_that_draws_nothing_still_leaves_the_dot(self):
        # The TS marks the block as having content as soon as a call renderer
        # returned *anything*, drawn or not — so `hideComponent` is unreachable
        # once a renderer exists, and the status dot is what is left on screen.
        from cortex.tui.render import Container

        def draw(*_args: object) -> Container:
            return Container()

        component = block(renderer=ToolRenderer(render_call=draw))
        assert "●" in plain(component.render(80))


class TestResolveToolRenderer:
    def test_a_name_with_no_renderer_resolves_to_nothing(self):
        assert resolve_tool_renderer(object(), "grep") is None

    def test_edit_has_a_built_in_renderer(self):
        renderer = get_built_in_tool_renderer("edit")
        assert renderer is not None
        assert renderer.render_shell == "self"

    def test_a_registered_renderer_wins_field_by_field(self):
        def draw(*_args: object) -> Text:
            return Text("", 0, 0)

        call = ToolRenderer(render_call=draw)

        class Session:
            def get_tool_definition(self, tool_name: str) -> object:
                return call if tool_name == "edit" else None

        merged = resolve_tool_renderer(Session(), "edit")
        assert merged is not None
        assert merged.render_call is call.render_call
        # Not overridden, so the built-in's half of the pair stays.
        built_in = get_built_in_tool_renderer("edit")
        assert built_in is not None
        assert merged.render_result is built_in.render_result
        assert merged.render_shell == "self"

    def test_a_session_without_the_hook_is_fine(self):
        assert resolve_tool_renderer(object(), "edit") is get_built_in_tool_renderer("edit")

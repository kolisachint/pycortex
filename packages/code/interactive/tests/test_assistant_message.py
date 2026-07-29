"""Tests for the assistant message component (step 7.5).

Two things are being checked, and they are different in kind. What the component
*shows* — markdown rendered rather than raw, the thinking trace, the aborted and
error lines — is the port's contract, and is asserted on the rendered lines. What
it *keeps* — the reused `Markdown` children and the segmentation of a big
streaming block — is performance work whose absence is invisible on screen for a
short message and expensive for a long one, so it is asserted on the object.
"""

from __future__ import annotations

from typing import Any

from cortex.ai.providers.faux import faux_assistant_message, faux_text, faux_thinking
from cortex.ai.types import ToolCall
from cortex.code.interactive.components.assistant_message import (
    SEGMENT_MIN_CHARS,
    AssistantMessageComponent,
    segment_streaming_markdown,
)


def _rendered(component: AssistantMessageComponent, width: int = 60) -> str:
    return "\n".join(component.render(width))


def _plain(component: AssistantMessageComponent, width: int = 60) -> str:
    """The rendered lines with every ANSI escape removed."""
    import re

    return re.sub(r"\x1b\[[0-9;]*m|\x1b\][^\x07]*\x07", "", _rendered(component, width))


class TestRendering:
    def test_an_empty_message_renders_nothing(self):
        component = AssistantMessageComponent(faux_assistant_message(""))
        assert component.render(60) == []

    def test_text_renders_as_markdown_not_as_source(self):
        component = AssistantMessageComponent(
            faux_assistant_message("# Heading\n\nsome **bold** text")
        )
        plain = _plain(component)
        assert "Heading" in plain
        assert "#" not in plain, "the heading marker was not consumed"
        assert "**" not in plain, "the emphasis markers were not consumed"
        assert "bold" in plain

    def test_the_message_is_wrapped_in_osc_133_zones(self):
        """Shell integration: the same markers `UserMessageComponent` emits."""
        component = AssistantMessageComponent(faux_assistant_message("hello"))
        lines = component.render(60)
        assert lines[0].startswith("\x1b]133;A\x07")
        assert "\x1b]133;B\x07\x1b]133;C\x07" in lines[-1]

    def test_a_message_with_tool_calls_is_not_zone_wrapped(self):
        """The zone ends at the *answer*, and a tool call is not the answer yet."""
        component = AssistantMessageComponent(
            faux_assistant_message(
                [faux_text("running it"), ToolCall(id="t1", name="read", arguments={})]
            )
        )
        lines = component.render(60)
        assert not lines[0].startswith("\x1b]133;A\x07")

    def test_a_thinking_block_renders_with_its_marker(self):
        component = AssistantMessageComponent(
            faux_assistant_message([faux_thinking("weighing it up"), faux_text("the answer")])
        )
        plain = _plain(component)
        assert "✻ weighing it up" in plain
        assert "the answer" in plain

    def test_a_hidden_thinking_block_becomes_its_label(self):
        component = AssistantMessageComponent(
            faux_assistant_message([faux_thinking("private"), faux_text("public")]),
            True,
        )
        plain = _plain(component)
        assert "private" not in plain
        assert "Thinking..." in plain
        assert "public" in plain

    def test_the_hidden_label_can_be_changed_after_the_fact(self):
        component = AssistantMessageComponent(
            faux_assistant_message([faux_thinking("private")]), True
        )
        component.set_hidden_thinking_label("Mulling...")
        assert "Mulling..." in _plain(component)

    def test_unhiding_brings_the_thinking_trace_back(self):
        component = AssistantMessageComponent(
            faux_assistant_message([faux_thinking("out loud")]), True
        )
        assert "out loud" not in _plain(component)
        component.set_hide_thinking_block(False)
        assert "out loud" in _plain(component)

    def test_an_aborted_message_keeps_its_partial_text_and_says_so(self):
        component = AssistantMessageComponent(
            faux_assistant_message("half an answer", stop_reason="aborted")
        )
        plain = _plain(component)
        assert "half an answer" in plain
        assert "Operation aborted" in plain

    def test_an_aborted_message_prefers_a_real_error_message(self):
        component = AssistantMessageComponent(
            faux_assistant_message(
                "", stop_reason="aborted", error_message="Aborted after 2 retry attempts"
            )
        )
        assert "Aborted after 2 retry attempts" in _plain(component)

    def test_the_providers_generic_abort_wording_is_replaced(self):
        """ "Request was aborted" is the provider's phrase, not something to show."""
        component = AssistantMessageComponent(
            faux_assistant_message("", stop_reason="aborted", error_message="Request was aborted")
        )
        plain = _plain(component)
        assert "Request was aborted" not in plain
        assert "Operation aborted" in plain

    def test_an_error_message_is_labelled(self):
        component = AssistantMessageComponent(
            faux_assistant_message("", stop_reason="error", error_message="Provider is overloaded")
        )
        assert "Error: Provider is overloaded" in _plain(component)

    def test_an_error_with_no_message_still_says_something(self):
        component = AssistantMessageComponent(faux_assistant_message("", stop_reason="error"))
        assert "Error: Unknown error" in _plain(component)

    def test_a_failed_turn_with_tool_calls_leaves_the_error_to_the_tools(self):
        """The tool execution components show it; two copies would be worse."""
        component = AssistantMessageComponent(
            faux_assistant_message(
                [ToolCall(id="t1", name="read", arguments={})],
                stop_reason="error",
                error_message="Provider is overloaded",
            )
        )
        assert "Provider is overloaded" not in _plain(component)


class TestStreaming:
    def test_updating_replaces_the_content_rather_than_appending_to_it(self):
        component = AssistantMessageComponent(faux_assistant_message("one"))
        component.update_content(faux_assistant_message("one two"))
        plain = _plain(component)
        assert "one two" in plain
        assert plain.count("one") == 1, plain

    def test_a_finished_block_keeps_its_markdown_child(self):
        """The cache is the whole point: rebuilding re-parses the message a frame."""
        component = AssistantMessageComponent(faux_assistant_message("growing"))
        first = component.content_container.children[1]
        component.update_content(faux_assistant_message("growing longer"), True)
        assert component.content_container.children[1] is first

    def test_a_big_streaming_block_is_segmented_and_collapses_when_it_finishes(self):
        body = "\n\n".join(f"Paragraph {i} with enough text to matter." for i in range(80))
        assert len(body) >= SEGMENT_MIN_CHARS
        component = AssistantMessageComponent()

        component.update_content(faux_assistant_message(body), True)
        streaming_children = len(component.content_container.children)
        assert streaming_children > 2, "a long streaming block was not segmented"

        component.update_content(faux_assistant_message(body))
        # Spacer + one canonical Markdown.
        assert len(component.content_container.children) == 2
        assert not any(":seg:" in key for key in component._markdown_cache)  # pyright: ignore[reportPrivateUsage]

    def test_a_small_streaming_block_is_not_segmented(self):
        component = AssistantMessageComponent()
        component.update_content(faux_assistant_message("one\n\ntwo\n\nthree"), True)
        assert len(component.content_container.children) == 2

    def test_invalidate_redraws_from_the_last_message(self):
        """A theme or width change has to be able to reach a detached child."""
        component = AssistantMessageComponent(faux_assistant_message("still here"))
        component.invalidate()
        assert "still here" in _plain(component)


class TestSegmentStreamingMarkdown:
    def test_it_cuts_at_a_blank_line(self):
        assert segment_streaming_markdown("alpha\n\nbeta") == ["alpha", "beta"]

    def test_it_is_prefix_stable(self):
        """The property the markdown cache depends on: earlier cuts never move."""
        text = "alpha\n\nbeta\n\ngamma"
        for extra in ("", "\n\ndelta", " and more"):
            chunks = segment_streaming_markdown(text + extra)
            assert chunks[0] == "alpha"
            assert chunks[1] == "beta"

    def test_it_does_not_cut_inside_a_fence(self):
        text = "before\n\n```\ncode\n\nmore code\n```\n\nafter"
        chunks = segment_streaming_markdown(text)
        assert chunks == ["before", "```\ncode\n\nmore code\n```", "after"]

    def test_it_does_not_split_a_loose_list(self):
        text = "- one\n\n- two"
        assert segment_streaming_markdown(text) == [text]

    def test_it_does_not_cut_before_an_indented_continuation(self):
        text = "- one\n\n    continued"
        assert segment_streaming_markdown(text) == [text]

    def test_it_does_not_cut_around_a_table(self):
        text = "| a | b |\n| - | - |\n\n| c | d |"
        assert segment_streaming_markdown(text) == [text]

    def test_it_does_not_cut_before_a_setext_underline(self):
        text = "title\n\n====="
        assert segment_streaming_markdown(text) == [text]

    def test_a_document_with_link_definitions_is_never_segmented(self):
        """Reference links resolve document-wide; a chunk cannot see the others."""
        text = "see [it]\n\nmore text\n\n[it]: https://example.invalid"
        assert segment_streaming_markdown(text) == [text]

    def test_a_trailing_blank_run_stays_attached(self):
        assert segment_streaming_markdown("alpha\n\n") == ["alpha\n\n"]

    def test_text_with_no_boundary_comes_back_whole(self):
        assert segment_streaming_markdown("just one line") == ["just one line"]

    def test_blank_text_comes_back_as_itself(self):
        assert segment_streaming_markdown("") == [""]

    def test_the_chunks_rejoin_to_the_source(self):
        """Segmentation is a view of the text, not an edit of it."""
        text = "alpha\n\nbeta\n\n## gamma\n\ndelta"
        chunks: Any = segment_streaming_markdown(text)
        assert "\n\n".join(chunks) == text

"""The assistant's half of the chat log.

Port of ``modes/interactive/components/assistant-message.ts``. The component is
built on ``message_start`` and fed the growing message on every delta, so what
the user reads is markdown rendered as it arrives rather than a wall of text at
the end of the turn.

Two things here are performance work in the TS and are ported as such, because
dropping them changes what the screen does under load rather than only how fast
it gets there:

* the **markdown cache** — ``Markdown`` caches its rendered lines by (text,
  width), so rebuilding the child components on every delta would re-parse the
  whole message (thinking trace included) per frame. The children are reused,
  keyed by content index and kind, so finished blocks stay cached and only the
  block whose text actually changed re-parses;
* **segmentation** — above :data:`SEGMENT_MIN_CHARS` a streaming block is cut at
  blank-line boundaries that lex independently, so each throttle tick re-parses
  only the growing tail. The cuts are prefix-stable: appending text never moves
  an earlier boundary, which is what keeps every chunk but the last byte-identical
  across updates (and therefore cached). The final, non-streaming render collapses
  back to one canonical ``Markdown``, so any segmentation artifact is transient by
  construction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cortex.tui.components import DefaultTextStyle, Markdown, Spacer, Text
from cortex.tui.render import Container

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cortex.ai.types import AssistantMessage
    from cortex.tui.components import MarkdownTheme

from cortex.code.interactive.theme import get_markdown_theme, get_theme

__all__ = ["AssistantMessageComponent", "segment_streaming_markdown"]

OSC133_ZONE_START = "\x1b]133;A\x07"
OSC133_ZONE_END = "\x1b]133;B\x07"
OSC133_ZONE_FINAL = "\x1b]133;C\x07"

#: Streaming messages below this size render as a single ``Markdown``; above it
#: the text is segmented at stable block boundaries.
SEGMENT_MIN_CHARS = 2048

_LIST_ITEM_RE = re.compile(r"^ {0,3}(?:[-*+] |\d{1,9}[.)] )")
_FENCE_RE = re.compile(r"^ {0,3}(?:```|~~~)")
_LINK_DEF_RE = re.compile(r"^ {0,3}\[[^\]]+\]: ", re.MULTILINE)
_SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}(?:=+|-+)\s*$")
_LEADING_SPACE_RE = re.compile(r"^\s")


def _is_safe_boundary(prev: str, next_line: str) -> bool:
    """Whether the blank-line gap between two non-blank lines can be cut at.

    "Safe" means both sides lex to the same blocks apart as they do together.
    """
    # Indented continuation (loose list item body, indented code) binds to the
    # block above the gap.
    if _LEADING_SPACE_RE.match(next_line):
        return False
    # A blank line between two list items is a loose list, not two lists.
    if _LIST_ITEM_RE.match(prev) and _LIST_ITEM_RE.match(next_line):
        return False
    # Tables and raw HTML blocks can span blank lines in surprising ways.
    if next_line.startswith("|") or prev.lstrip().startswith("|"):
        return False
    if next_line.startswith("<") or prev.lstrip().startswith("<"):
        return False
    # A bare ===/--- line after the gap could lex as a setext underline or an hr
    # differently without its preceding text; keep it attached.
    if _SETEXT_UNDERLINE_RE.match(next_line):
        return False
    return True


def segment_streaming_markdown(text: str) -> list[str]:
    """Split markdown at blank-line boundaries that lex independently.

    Prefix-stable: appending text never changes an earlier boundary, because
    every decision depends only on preceding fence parity and the two lines
    adjacent to the gap.
    """
    # Reference-style link/footnote definitions resolve across the whole
    # document; segmenting would break lookups from other chunks.
    if _LINK_DEF_RE.search(text):
        return [text]
    lines = text.split("\n")
    chunks: list[str] = []
    chunk_start = 0
    fence_open = False
    prev_nonblank = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE_RE.match(line):
            fence_open = not fence_open
        if line.strip() == "" and not fence_open:
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            # Only cut when the gap has content on both sides; a trailing blank
            # run stays attached so the decision is never revisited.
            if j < len(lines) and chunk_start < i and _is_safe_boundary(prev_nonblank, lines[j]):
                chunks.append("\n".join(lines[chunk_start:i]))
                chunk_start = j
            i = j
            continue
        if line.strip() != "":
            prev_nonblank = line
        i += 1
    last = "\n".join(lines[chunk_start:])
    if last.strip() != "":
        chunks.append(last)
    return chunks if chunks else [text]


@dataclass
class _CachedMarkdown:
    md: Markdown
    text: str


def _content_type(block: Any) -> str:
    return str(getattr(block, "type", ""))


def _block_text(block: Any, field: str) -> str:
    """A content block's text, read by name.

    The TS narrows the content union with `content.type === "text"`; the fields
    are read through `getattr` here for the same reason the import above is
    type-only — this leaf has no runtime dependency on `cortex.ai.types`, and the
    TS's is a `import type` too.
    """
    return str(getattr(block, field, ""))


def _is_visible(block: Any) -> bool:
    """Whether a content block puts anything on screen."""
    kind = _content_type(block)
    if kind == "text":
        return bool(_block_text(block, "text").strip())
    if kind == "thinking":
        return bool(_block_text(block, "thinking").strip())
    return False


class AssistantMessageComponent(Container):
    """Renders one assistant message, complete or mid-stream."""

    def __init__(
        self,
        message: AssistantMessage | None = None,
        hide_thinking_block: bool = False,
        markdown_theme: MarkdownTheme | None = None,
        hidden_thinking_label: str = "Thinking...",
    ) -> None:
        super().__init__()
        self._hide_thinking_block = hide_thinking_block
        self._markdown_theme = (
            markdown_theme if markdown_theme is not None else get_markdown_theme()
        )
        self._hidden_thinking_label = hidden_thinking_label
        self._last_message: AssistantMessage | None = None
        self._has_tool_calls = False
        self._markdown_cache: dict[str, _CachedMarkdown] = {}
        #: Set while the children include streaming segments; the next
        #: non-streaming render purges their cache entries.
        self._has_segmented_blocks = False
        #: OSC-zone wrap memo, keyed on the source list's identity — `Container`
        #: returns the same list across frames when nothing changed, so the
        #: wrapped copy must not mutate it.
        self._zone_src: Sequence[str] | None = None
        self._zone_out: list[str] | None = None

        # Container for text/thinking content.
        self.content_container = Container()
        self.add_child(self.content_container)

        if message is not None:
            self.update_content(message)

    def invalidate(self) -> None:
        # Cached Markdown blocks may be detached right now (hidden thinking, say);
        # drop their render caches too, so a theme or width change cannot resurface
        # stale styling when they re-attach.
        for entry in self._markdown_cache.values():
            entry.md.invalidate()
        super().invalidate()
        if self._last_message is not None:
            self.update_content(self._last_message)

    def set_hide_thinking_block(self, hide: bool) -> None:
        self._hide_thinking_block = hide
        if self._last_message is not None:
            self.update_content(self._last_message)

    def set_hidden_thinking_label(self, label: str) -> None:
        self._hidden_thinking_label = label
        if self._last_message is not None:
            self.update_content(self._last_message)

    def _reuse_markdown(
        self,
        key: str,
        text: str,
        style: DefaultTextStyle | None = None,
    ) -> Markdown:
        entry = self._markdown_cache.get(key)
        if entry is not None:
            if entry.text != text:
                entry.md.set_text(text)
                entry.text = text
            return entry.md
        md = Markdown(text, 1, 0, self._markdown_theme, style)
        self._markdown_cache[key] = _CachedMarkdown(md=md, text=text)
        return md

    def _add_markdown_block(
        self,
        key_base: str,
        text: str,
        streaming: bool,
        style: DefaultTextStyle | None = None,
    ) -> None:
        """Add one markdown block, segmented if it is big and still growing."""
        if streaming and len(text) >= SEGMENT_MIN_CHARS:
            chunks = segment_streaming_markdown(text)
            if len(chunks) > 1:
                for index, chunk in enumerate(chunks):
                    if index > 0:
                        self.content_container.add_child(Spacer(1))
                    self.content_container.add_child(
                        self._reuse_markdown(f"{key_base}:seg:{index}", chunk, style)
                    )
                self._has_segmented_blocks = True
                return
        self.content_container.add_child(self._reuse_markdown(key_base, text, style))

    def render(self, width: int) -> list[str]:
        lines = super().render(width)
        if self._has_tool_calls or not lines:
            return lines

        if self._zone_src is lines and self._zone_out is not None:
            return self._zone_out
        out = list(lines)
        out[0] = OSC133_ZONE_START + out[0]
        out[-1] = OSC133_ZONE_END + OSC133_ZONE_FINAL + out[-1]
        self._zone_src = lines
        self._zone_out = out
        return out

    def update_content(self, message: AssistantMessage, streaming: bool = False) -> None:
        """Rebuild the message's children from `message`.

        `streaming=True` is the mid-turn form (segmented, per the throttle);
        `False` is the canonical one the finished message renders as.
        """
        self._last_message = message

        # A final/rebuild render replaces streaming segments with the canonical
        # single-Markdown form; drop the segment cache entries they used.
        if not streaming and self._has_segmented_blocks:
            for key in [k for k in self._markdown_cache if ":seg:" in k]:
                del self._markdown_cache[key]
            self._has_segmented_blocks = False

        self.content_container.clear()

        content = list(message.content)
        has_visible_content = any(_is_visible(block) for block in content)

        if has_visible_content:
            self.content_container.add_child(Spacer(1))

        theme = get_theme()

        for index, block in enumerate(content):
            kind = _content_type(block)
            if kind == "text" and _block_text(block, "text").strip():
                # Assistant text has no background of its own, and paddingY=0 so
                # nothing extra sits between it and a tool execution below.
                self._add_markdown_block(
                    f"{index}:text", _block_text(block, "text").strip(), streaming
                )
            elif kind == "thinking" and _block_text(block, "thinking").strip():
                # Space it only when another visible block follows, so a
                # separately-rendered tool execution gets no stray blank line.
                has_visible_content_after = any(_is_visible(b) for b in content[index + 1 :])

                if self._hide_thinking_block:
                    self.content_container.add_child(
                        Text(
                            theme.italic(theme.fg("thinkingText", self._hidden_thinking_label)),
                            1,
                            0,
                        )
                    )
                    if has_visible_content_after:
                        self.content_container.add_child(Spacer(1))
                else:
                    self._add_markdown_block(
                        f"{index}:thinking",
                        f"✻ {_block_text(block, 'thinking').strip()}",
                        streaming,
                        DefaultTextStyle(
                            color=lambda text: get_theme().fg("thinkingText", text),
                            italic=True,
                        ),
                    )
                    if has_visible_content_after:
                        self.content_container.add_child(Spacer(1))

        # The aborted/error line goes under whatever partial content arrived —
        # but only when there are no tool calls, since the tool execution
        # components show the error themselves.
        has_tool_calls = any(_content_type(block) == "toolCall" for block in content)
        self._has_tool_calls = has_tool_calls
        if has_tool_calls:
            return

        stop_reason = getattr(message, "stop_reason", None)
        error_message = getattr(message, "error_message", None)
        if stop_reason == "aborted":
            abort_message = (
                error_message
                if error_message and error_message != "Request was aborted"
                else "Operation aborted"
            )
            self.content_container.add_child(Spacer(1))
            self.content_container.add_child(Text(theme.fg("error", abort_message), 1, 0))
        elif stop_reason == "error":
            self.content_container.add_child(Spacer(1))
            self.content_container.add_child(
                Text(theme.fg("error", f"Error: {error_message or 'Unknown error'}"), 1, 0)
            )

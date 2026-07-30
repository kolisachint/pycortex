"""The ``/tree`` overlay. Port of ``components/tree-selector.ts``.

A session is a tree, not a transcript: every fork and every branch-summary leaves
a sibling behind, and this is the only screen that shows them. Picking a node
navigates the *current* session's leaf to it — no new file, unlike ``/fork``.

The drawing is the hard part and it is worth naming what the rules are for:

* **indentation only grows at a branch.** A single-child chain stays in its
  column, so a hundred-turn conversation with one fork in it is two columns wide
  rather than a hundred. The one exception is the first generation after a
  branch, which shifts once so the two branches read as blocks.
* **filters recompute the structure, not just the rows.** Hiding tool results
  can leave a child whose parent is gone; it re-attaches to the nearest visible
  ancestor and the connectors are rebuilt around what is left
  (:meth:`TreeList._recalculate_visual_structure`), which is why the same
  indentation code exists twice — once over the whole tree, once over the
  visible one.
* **folding is per branch point.** Only a node that starts a segment can fold,
  because folding anything else would hide a chain nobody chose to leave.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from cortex.code.interactive.components.dynamic_border import DynamicBorder
from cortex.code.interactive.components.keybinding_hints import key_hint, key_text
from cortex.code.interactive.theme import get_theme
from cortex.tui.components import Input, Spacer, Text, TruncatedText
from cortex.tui.keys import get_keybindings
from cortex.tui.render import Container
from cortex.tui.util import truncate_to_width

__all__ = ["FilterMode", "TreeSelectorComponent"]

#: Which entries the tree shows.
FilterMode = Literal["default", "no-tools", "user-only", "labeled-only", "all"]

FILTER_MODES: list[FilterMode] = ["default", "no-tools", "user-only", "labeled-only", "all"]

#: How much of a message body a row will show before giving up.
MAX_CONTENT_CHARS = 200

#: Entry types the default view hides: settings and bookkeeping, not conversation.
SETTINGS_ENTRY_TYPES = frozenset(
    {"label", "custom", "model_change", "thinking_level_change", "session_info"}
)


@dataclass
class GutterInfo:
    """A vertical line to draw, and the indent level it belongs to."""

    position: int
    show: bool


@dataclass
class FlatNode:
    """A tree node placed on a row."""

    node: Any
    indent: int
    show_connector: bool
    is_last: bool
    gutters: list[GutterInfo] = field(default_factory=list)
    #: A root drawn under the virtual root that multiple roots imply. Its
    #: connector is suppressed — there is no real parent to connect it to.
    is_virtual_root_child: bool = False


@dataclass(frozen=True)
class ToolCallInfo:
    """What a tool-result row needs to describe the call it answers."""

    name: str
    arguments: dict[str, Any]


def _entry_field(entry: Any, name: str) -> Any:
    """A field of a session entry, dict or object."""
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


def _message_field(message: Any, name: str) -> Any:
    if isinstance(message, dict):
        return message.get(name)
    return getattr(message, name, None)


def _shorten_path(path: str) -> str:
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
    if home and path.startswith(home):
        return f"~{path[len(home) :]}"
    return path


class TreeList:
    """The rows, the cursor, the filters and the fold state."""

    def __init__(
        self,
        tree: list[Any],
        current_leaf_id: str | None,
        max_visible_lines: int,
        initial_selected_id: str | None = None,
        initial_filter_mode: FilterMode | None = None,
    ) -> None:
        self._current_leaf_id = current_leaf_id
        self._max_visible_lines = max_visible_lines
        self._filter_mode: FilterMode = initial_filter_mode or "default"
        self._search_query = ""
        self._tool_call_map: dict[str, ToolCallInfo] = {}
        self._multiple_roots = len(tree) > 1
        self._show_label_timestamps = False
        self._active_path_ids: set[str] = set()
        self._visible_parent_map: dict[str, str | None] = {}
        self._visible_children_map: dict[str | None, list[str]] = {}
        self._folded_nodes: set[str] = set()
        self._selected_index = 0
        #: The entry the cursor was last on, so a filter that hides it can put
        #: the cursor on its nearest visible ancestor rather than at the top.
        self._last_selected_id: str | None = None

        self.on_select: Callable[[str], None] | None = None
        self.on_cancel: Callable[[], None] | None = None
        self.on_label_edit: Callable[[str, str | None], None] | None = None

        self._flat_nodes = self._flatten_tree(tree)
        self._filtered_nodes: list[FlatNode] = []
        self._build_active_path()
        self._apply_filter()

        target_id = initial_selected_id if initial_selected_id is not None else current_leaf_id
        self._selected_index = self._find_nearest_visible_index(target_id)
        selected = self._selected()
        self._last_selected_id = _entry_field(selected.node.entry, "id") if selected else None

    # ---- structure --------------------------------------------------------

    def _selected(self) -> FlatNode | None:
        if 0 <= self._selected_index < len(self._filtered_nodes):
            return self._filtered_nodes[self._selected_index]
        return None

    def _find_nearest_visible_index(self, entry_id: str | None) -> int:
        """Where the cursor lands: the entry itself, or its nearest visible ancestor."""
        if not self._filtered_nodes:
            return 0

        entry_map = {_entry_field(n.node.entry, "id"): n for n in self._flat_nodes}
        visible_id_to_index = {
            _entry_field(n.node.entry, "id"): i for i, n in enumerate(self._filtered_nodes)
        }

        current_id = entry_id
        while current_id is not None:
            index = visible_id_to_index.get(current_id)
            if index is not None:
                return index
            node = entry_map.get(current_id)
            if node is None:
                break
            current_id = _entry_field(node.node.entry, "parentId")

        return len(self._filtered_nodes) - 1

    def _build_active_path(self) -> None:
        """The ids from the current leaf back to the root — the branch you are on."""
        self._active_path_ids.clear()
        if not self._current_leaf_id:
            return

        entry_map = {_entry_field(n.node.entry, "id"): n for n in self._flat_nodes}
        current_id: str | None = self._current_leaf_id
        while current_id:
            self._active_path_ids.add(current_id)
            node = entry_map.get(current_id)
            if node is None:
                break
            current_id = _entry_field(node.node.entry, "parentId")

    def _flatten_tree(self, roots: list[Any]) -> list[FlatNode]:
        """Depth-first order, with the branch holding the active leaf drawn first."""
        result: list[FlatNode] = []
        self._tool_call_map.clear()

        # Which subtrees hold the leaf. Post-order over an explicitly built list
        # rather than recursion: a long session is a very deep tree.
        contains_active: dict[int, bool] = {}
        all_nodes: list[Any] = []
        pre_order_stack = list(roots)
        while pre_order_stack:
            node = pre_order_stack.pop()
            all_nodes.append(node)
            for child in reversed(node.children):
                pre_order_stack.append(child)
        for node in reversed(all_nodes):
            has = self._current_leaf_id is not None and (
                _entry_field(node.entry, "id") == self._current_leaf_id
            )
            for child in node.children:
                if contains_active.get(id(child)):
                    has = True
            contains_active[id(node)] = has

        multiple_roots = len(roots) > 1
        ordered_roots = sorted(roots, key=lambda n: not contains_active.get(id(n), False))

        # (node, indent, just_branched, show_connector, is_last, gutters, virtual)
        stack: list[tuple[Any, int, bool, bool, bool, list[GutterInfo], bool]] = []
        for index in range(len(ordered_roots) - 1, -1, -1):
            is_last = index == len(ordered_roots) - 1
            stack.append(
                (
                    ordered_roots[index],
                    1 if multiple_roots else 0,
                    multiple_roots,
                    multiple_roots,
                    is_last,
                    [],
                    multiple_roots,
                )
            )

        while stack:
            node, indent, just_branched, show_connector, is_last, gutters, virtual = stack.pop()

            entry = node.entry
            if _entry_field(entry, "type") == "message":
                message = _entry_field(entry, "message")
                if _message_field(message, "role") == "assistant":
                    content = _message_field(message, "content")
                    if isinstance(content, list):
                        for block in content:
                            block_type = (
                                block.get("type")
                                if isinstance(block, dict)
                                else getattr(block, "type", None)
                            )
                            if block_type != "toolCall":
                                continue
                            call_id = (
                                block.get("id")
                                if isinstance(block, dict)
                                else getattr(block, "id", "")
                            )
                            name = (
                                block.get("name")
                                if isinstance(block, dict)
                                else getattr(block, "name", "")
                            )
                            arguments = (
                                block.get("arguments")
                                if isinstance(block, dict)
                                else getattr(block, "arguments", {})
                            )
                            self._tool_call_map[str(call_id)] = ToolCallInfo(
                                name=str(name), arguments=arguments or {}
                            )

            result.append(
                FlatNode(
                    node=node,
                    indent=indent,
                    show_connector=show_connector,
                    is_last=is_last,
                    gutters=gutters,
                    is_virtual_root_child=virtual,
                )
            )

            children = node.children
            multiple_children = len(children) > 1
            ordered_children = [c for c in children if contains_active.get(id(c))] + [
                c for c in children if not contains_active.get(id(c))
            ]

            child_indent = self._child_indent(indent, just_branched, multiple_children)
            child_gutters = self._child_gutters(gutters, indent, show_connector, is_last, virtual)

            for index in range(len(ordered_children) - 1, -1, -1):
                stack.append(
                    (
                        ordered_children[index],
                        child_indent,
                        multiple_children,
                        multiple_children,
                        index == len(ordered_children) - 1,
                        child_gutters,
                        False,
                    )
                )

        return result

    def _child_indent(self, indent: int, just_branched: bool, multiple_children: bool) -> int:
        if multiple_children:
            return indent + 1
        if just_branched and indent > 0:
            return indent + 1
        return indent

    def _child_gutters(
        self,
        gutters: list[GutterInfo],
        indent: int,
        show_connector: bool,
        is_last: bool,
        is_virtual_root_child: bool,
    ) -> list[GutterInfo]:
        connector_displayed = show_connector and not is_virtual_root_child
        if not connector_displayed:
            return gutters
        current_display_indent = max(0, indent - 1) if self._multiple_roots else indent
        connector_position = max(0, current_display_indent - 1)
        return [*gutters, GutterInfo(position=connector_position, show=not is_last)]

    def _apply_filter(self) -> None:  # noqa: C901 - 1:1 with the TS predicate chain
        # Only remember the selection when there is one: switching through a
        # filter that matches nothing must not forget where the cursor was.
        if self._filtered_nodes:
            selected = self._selected()
            if selected is not None:
                self._last_selected_id = _entry_field(selected.node.entry, "id")

        search_tokens = [token for token in self._search_query.lower().split() if token]

        filtered: list[FlatNode] = []
        for flat_node in self._flat_nodes:
            entry = flat_node.node.entry
            entry_type = _entry_field(entry, "type")
            is_current_leaf = _entry_field(entry, "id") == self._current_leaf_id

            # An assistant message that is nothing but tool calls has no row of
            # its own — the tool results below it say what happened. Errors and
            # aborts stay: those are the rows you came here to find.
            if entry_type == "message" and not is_current_leaf:
                message = _entry_field(entry, "message")
                if _message_field(message, "role") == "assistant":
                    has_text = self._has_text_content(_message_field(message, "content"))
                    stop_reason = _message_field(message, "stop_reason") or _message_field(
                        message, "stopReason"
                    )
                    is_error_or_aborted = bool(stop_reason) and stop_reason not in (
                        "stop",
                        "toolUse",
                    )
                    if not has_text and not is_error_or_aborted:
                        continue

            is_settings_entry = entry_type in SETTINGS_ENTRY_TYPES

            if self._filter_mode == "user-only":
                passes = entry_type == "message" and (
                    _message_field(_entry_field(entry, "message"), "role") == "user"
                )
            elif self._filter_mode == "no-tools":
                is_tool_result = entry_type == "message" and (
                    _message_field(_entry_field(entry, "message"), "role") == "toolResult"
                )
                passes = not is_settings_entry and not is_tool_result
            elif self._filter_mode == "labeled-only":
                passes = flat_node.node.label is not None
            elif self._filter_mode == "all":
                passes = True
            else:
                passes = not is_settings_entry

            if not passes:
                continue

            if search_tokens:
                node_text = self._searchable_text(flat_node.node).lower()
                if not all(token in node_text for token in search_tokens):
                    continue

            filtered.append(flat_node)

        self._filtered_nodes = filtered

        if self._folded_nodes:
            skip: set[str] = set()
            for flat_node in self._flat_nodes:
                entry_id = _entry_field(flat_node.node.entry, "id")
                parent_id = _entry_field(flat_node.node.entry, "parentId")
                if parent_id is not None and (parent_id in self._folded_nodes or parent_id in skip):
                    skip.add(entry_id)
            self._filtered_nodes = [
                n for n in self._filtered_nodes if _entry_field(n.node.entry, "id") not in skip
            ]

        self._recalculate_visual_structure()

        if self._last_selected_id:
            self._selected_index = self._find_nearest_visible_index(self._last_selected_id)
        elif self._selected_index >= len(self._filtered_nodes):
            self._selected_index = max(0, len(self._filtered_nodes) - 1)

        selected = self._selected()
        if selected is not None:
            self._last_selected_id = _entry_field(selected.node.entry, "id")

    def _recalculate_visual_structure(self) -> None:
        """Re-derive indent, connectors and gutters from the *visible* tree."""
        if not self._filtered_nodes:
            return

        visible_ids = {_entry_field(n.node.entry, "id") for n in self._filtered_nodes}
        entry_map = {_entry_field(n.node.entry, "id"): n for n in self._flat_nodes}

        def find_visible_ancestor(node_id: str) -> str | None:
            node = entry_map.get(node_id)
            current_id = _entry_field(node.node.entry, "parentId") if node else None
            while current_id is not None:
                if current_id in visible_ids:
                    return current_id
                parent = entry_map.get(current_id)
                current_id = _entry_field(parent.node.entry, "parentId") if parent else None
            return None

        visible_parent: dict[str, str | None] = {}
        visible_children: dict[str | None, list[str]] = {None: []}

        for flat_node in self._filtered_nodes:
            node_id = _entry_field(flat_node.node.entry, "id")
            ancestor_id = find_visible_ancestor(node_id)
            visible_parent[node_id] = ancestor_id
            visible_children.setdefault(ancestor_id, []).append(node_id)

        visible_root_ids = visible_children[None]
        self._multiple_roots = len(visible_root_ids) > 1

        filtered_node_map = {_entry_field(n.node.entry, "id"): n for n in self._filtered_nodes}

        stack: list[tuple[str, int, bool, bool, bool, list[GutterInfo], bool]] = []
        for index in range(len(visible_root_ids) - 1, -1, -1):
            stack.append(
                (
                    visible_root_ids[index],
                    1 if self._multiple_roots else 0,
                    self._multiple_roots,
                    self._multiple_roots,
                    index == len(visible_root_ids) - 1,
                    [],
                    self._multiple_roots,
                )
            )

        while stack:
            node_id, indent, just_branched, show_connector, is_last, gutters, virtual = stack.pop()

            flat_node = filtered_node_map.get(node_id)
            if flat_node is None:
                continue

            flat_node.indent = indent
            flat_node.show_connector = show_connector
            flat_node.is_last = is_last
            flat_node.gutters = gutters
            flat_node.is_virtual_root_child = virtual

            children = visible_children.get(node_id, [])
            multiple_children = len(children) > 1
            child_indent = self._child_indent(indent, just_branched, multiple_children)
            child_gutters = self._child_gutters(gutters, indent, show_connector, is_last, virtual)

            for index in range(len(children) - 1, -1, -1):
                stack.append(
                    (
                        children[index],
                        child_indent,
                        multiple_children,
                        multiple_children,
                        index == len(children) - 1,
                        child_gutters,
                        False,
                    )
                )

        self._visible_parent_map = visible_parent
        self._visible_children_map = visible_children

    # ---- text -------------------------------------------------------------

    def _searchable_text(self, node: Any) -> str:
        entry = node.entry
        entry_type = _entry_field(entry, "type")
        parts: list[str] = []

        if node.label:
            parts.append(node.label)

        if entry_type == "message":
            message = _entry_field(entry, "message")
            role = _message_field(message, "role") or ""
            parts.append(str(role))
            content = _message_field(message, "content")
            if content:
                parts.append(self._extract_content(content))
            if role == "bashExecution":
                command = _message_field(message, "command")
                if command:
                    parts.append(str(command))
        elif entry_type == "custom_message":
            parts.append(str(_entry_field(entry, "customType") or ""))
            content = _entry_field(entry, "content")
            parts.append(content if isinstance(content, str) else self._extract_content(content))
        elif entry_type == "compaction":
            parts.append("compaction")
        elif entry_type == "branch_summary":
            parts.extend(["branch summary", str(_entry_field(entry, "summary") or "")])
        elif entry_type == "session_info":
            parts.append("title")
            name = _entry_field(entry, "name")
            if name:
                parts.append(str(name))
        elif entry_type == "model_change":
            parts.extend(["model", str(_entry_field(entry, "modelId") or "")])
        elif entry_type == "thinking_level_change":
            parts.extend(["thinking", str(_entry_field(entry, "thinkingLevel") or "")])
        elif entry_type == "custom":
            parts.extend(["custom", str(_entry_field(entry, "customType") or "")])
        elif entry_type == "label":
            parts.extend(["label", str(_entry_field(entry, "label") or "")])

        return " ".join(parts)

    def _extract_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content[:MAX_CONTENT_CHARS]
        if not isinstance(content, list):
            return ""
        result = ""
        for block in content:
            block_type = (
                block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            )
            if block_type != "text":
                continue
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", "")
            result += str(text or "")
            if len(result) >= MAX_CONTENT_CHARS:
                return result[:MAX_CONTENT_CHARS]
        return result

    def _has_text_content(self, content: Any) -> bool:
        if isinstance(content, str):
            return bool(content.strip())
        if not isinstance(content, list):
            return False
        for block in content:
            block_type = (
                block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            )
            if block_type != "text":
                continue
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", "")
            if text and str(text).strip():
                return True
        return False

    def _format_tool_call(self, name: str, args: dict[str, Any]) -> str:
        """The one-line form of a tool call, per tool. Port of ``formatToolCall``."""
        if name == "read":
            path = _shorten_path(str(args.get("path") or args.get("file_path") or ""))
            offset = args.get("offset")
            limit = args.get("limit")
            display = path
            if offset is not None or limit is not None:
                start = offset if offset is not None else 1
                end = start + limit - 1 if limit is not None else ""
                display += f":{start}{f'-{end}' if end != '' else ''}"
            return f"[read: {display}]"
        if name == "write":
            return f"[write: {_shorten_path(str(args.get('path') or args.get('file_path') or ''))}]"
        if name == "edit":
            return f"[edit: {_shorten_path(str(args.get('path') or args.get('file_path') or ''))}]"
        if name == "bash":
            raw = str(args.get("command") or "")
            command = raw.replace("\n", " ").replace("\t", " ").strip()[:50]
            return f"[bash: {command}{'...' if len(raw) > 50 else ''}]"
        if name == "grep":
            pattern = str(args.get("pattern") or "")
            return f"[grep: /{pattern}/ in {_shorten_path(str(args.get('path') or '.'))}]"
        if name == "find":
            pattern = str(args.get("pattern") or "")
            return f"[find: {pattern} in {_shorten_path(str(args.get('path') or '.'))}]"
        if name == "ls":
            return f"[ls: {_shorten_path(str(args.get('path') or '.'))}]"
        args_str = json.dumps(args, separators=(",", ":"))
        return f"[{name}: {args_str[:40]}{'...' if len(args_str) > 40 else ''}]"

    def _entry_display_text(self, node: Any, is_selected: bool) -> str:  # noqa: C901
        theme = get_theme()
        entry = node.entry
        entry_type = _entry_field(entry, "type")

        def normalize(text: str) -> str:
            return text.replace("\n", " ").replace("\t", " ").strip()

        result = ""
        if entry_type == "message":
            message = _entry_field(entry, "message")
            role = _message_field(message, "role")
            if role == "user":
                result = theme.fg("accent", "user: ") + normalize(
                    self._extract_content(_message_field(message, "content"))
                )
            elif role == "assistant":
                text_content = normalize(self._extract_content(_message_field(message, "content")))
                stop_reason = _message_field(message, "stop_reason") or _message_field(
                    message, "stopReason"
                )
                error_message = _message_field(message, "error_message") or _message_field(
                    message, "errorMessage"
                )
                if text_content:
                    result = theme.fg("success", "assistant: ") + text_content
                elif stop_reason == "aborted":
                    result = theme.fg("success", "assistant: ") + theme.fg("muted", "(aborted)")
                elif error_message:
                    result = theme.fg("success", "assistant: ") + theme.fg(
                        "error", normalize(str(error_message))[:80]
                    )
                else:
                    result = theme.fg("success", "assistant: ") + theme.fg("muted", "(no content)")
            elif role == "toolResult":
                tool_call_id = _message_field(message, "tool_call_id") or _message_field(
                    message, "toolCallId"
                )
                tool_call = self._tool_call_map.get(str(tool_call_id)) if tool_call_id else None
                if tool_call is not None:
                    result = theme.fg(
                        "muted", self._format_tool_call(tool_call.name, tool_call.arguments)
                    )
                else:
                    tool_name = (
                        _message_field(message, "tool_name")
                        or _message_field(message, "toolName")
                        or "tool"
                    )
                    result = theme.fg("muted", f"[{tool_name}]")
            elif role == "bashExecution":
                command = _message_field(message, "command") or ""
                result = theme.fg("dim", f"[bash]: {normalize(str(command))}")
            else:
                result = theme.fg("dim", f"[{role}]")
        elif entry_type == "custom_message":
            content = _entry_field(entry, "content")
            text = content if isinstance(content, str) else self._extract_content(content)
            result = theme.fg(
                "customMessageLabel", f"[{_entry_field(entry, 'customType')}]: "
            ) + normalize(text)
        elif entry_type == "compaction":
            tokens = round((_entry_field(entry, "tokensBefore") or 0) / 1000)
            result = theme.fg("borderAccent", f"[compaction: {tokens}k tokens]")
        elif entry_type == "branch_summary":
            result = theme.fg("warning", "[branch summary]: ") + normalize(
                str(_entry_field(entry, "summary") or "")
            )
        elif entry_type == "model_change":
            result = theme.fg("dim", f"[model: {_entry_field(entry, 'modelId')}]")
        elif entry_type == "thinking_level_change":
            result = theme.fg("dim", f"[thinking: {_entry_field(entry, 'thinkingLevel')}]")
        elif entry_type == "custom":
            result = theme.fg("dim", f"[custom: {_entry_field(entry, 'customType')}]")
        elif entry_type == "label":
            label = _entry_field(entry, "label")
            result = theme.fg("dim", f"[label: {label if label else '(cleared)'}]")
        elif entry_type == "session_info":
            name = _entry_field(entry, "name")
            if name:
                result = (
                    theme.fg("dim", "[title: ") + theme.fg("dim", str(name)) + theme.fg("dim", "]")
                )
            else:
                result = (
                    theme.fg("dim", "[title: ")
                    + theme.italic(theme.fg("dim", "empty"))
                    + theme.fg("dim", "]")
                )

        return theme.bold(result) if is_selected else result

    def _format_label_timestamp(self, timestamp: str) -> str:
        """A label's time, as short as it can be and still be unambiguous."""
        try:
            date = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except ValueError:
            return ""
        now = datetime.now(date.tzinfo) if date.tzinfo is not None else datetime.now()
        time = f"{date.hour:02d}:{date.minute:02d}"

        if (date.year, date.month, date.day) == (now.year, now.month, now.day):
            return time
        if date.year == now.year:
            return f"{date.month}/{date.day} {time}"
        return f"{str(date.year)[-2:]}/{date.month}/{date.day} {time}"

    def _status_labels(self) -> str:
        labels = {
            "no-tools": " [no-tools]",
            "user-only": " [user]",
            "labeled-only": " [labeled]",
            "all": " [all]",
        }.get(self._filter_mode, "")
        if self._show_label_timestamps:
            labels += " [+label time]"
        return labels

    # ---- rendering --------------------------------------------------------

    def invalidate(self) -> None:
        """Nothing is cached."""

    def get_search_query(self) -> str:
        return self._search_query

    def get_selected_node(self) -> Any:
        selected = self._selected()
        return selected.node if selected is not None else None

    def update_node_label(
        self, entry_id: str, label: str | None, label_timestamp: str | None = None
    ) -> None:
        """Reflect a label the user just set, without rebuilding the tree."""
        for flat_node in self._flat_nodes:
            if _entry_field(flat_node.node.entry, "id") != entry_id:
                continue
            flat_node.node.label = label
            flat_node.node.label_timestamp = (
                (label_timestamp or datetime.now().isoformat()) if label else None
            )
            break

    def render(self, width: int) -> list[str]:
        theme = get_theme()
        lines: list[str] = []

        if not self._filtered_nodes:
            lines.append(truncate_to_width(theme.fg("muted", "  No entries found"), width))
            lines.append(
                truncate_to_width(theme.fg("muted", f"  (0/0){self._status_labels()}"), width)
            )
            return lines

        start_index = max(
            0,
            min(
                self._selected_index - self._max_visible_lines // 2,
                len(self._filtered_nodes) - self._max_visible_lines,
            ),
        )
        end_index = min(start_index + self._max_visible_lines, len(self._filtered_nodes))

        for index in range(start_index, end_index):
            flat_node = self._filtered_nodes[index]
            entry = flat_node.node.entry
            entry_id = _entry_field(entry, "id")
            is_selected = index == self._selected_index

            cursor = theme.fg("accent", "› ") if is_selected else "  "
            display_indent = (
                max(0, flat_node.indent - 1) if self._multiple_roots else flat_node.indent
            )

            connector = ""
            if flat_node.show_connector and not flat_node.is_virtual_root_child:
                connector = "└─ " if flat_node.is_last else "├─ "
            connector_position = display_indent - 1 if connector else -1

            is_folded = entry_id in self._folded_nodes
            prefix_chars: list[str] = []
            for char_index in range(display_indent * 3):
                level = char_index // 3
                position_in_level = char_index % 3
                gutter = next((g for g in flat_node.gutters if g.position == level), None)
                if gutter is not None:
                    if position_in_level == 0:
                        prefix_chars.append("│" if gutter.show else " ")
                    else:
                        prefix_chars.append(" ")
                elif connector and level == connector_position:
                    if position_in_level == 0:
                        prefix_chars.append("└" if flat_node.is_last else "├")
                    elif position_in_level == 1:
                        foldable = self._is_foldable(entry_id)
                        prefix_chars.append("⊞" if is_folded else ("⊟" if foldable else "─"))
                    else:
                        prefix_chars.append(" ")
                else:
                    prefix_chars.append(" ")
            prefix = "".join(prefix_chars)

            shows_fold_in_connector = (
                flat_node.show_connector and not flat_node.is_virtual_root_child
            )
            fold_marker = (
                theme.fg("accent", "⊞ ") if is_folded and not shows_fold_in_connector else ""
            )
            path_marker = theme.fg("accent", "• ") if entry_id in self._active_path_ids else ""

            label = (
                theme.fg("warning", f"[{flat_node.node.label}] ") if flat_node.node.label else ""
            )
            label_timestamp = ""
            if (
                self._show_label_timestamps
                and flat_node.node.label
                and flat_node.node.label_timestamp
            ):
                label_timestamp = theme.fg(
                    "muted", f"{self._format_label_timestamp(flat_node.node.label_timestamp)} "
                )
            content = self._entry_display_text(flat_node.node, is_selected)

            line = (
                cursor
                + theme.fg("dim", prefix)
                + fold_marker
                + path_marker
                + label
                + label_timestamp
                + content
            )
            if is_selected:
                line = theme.bg("selectedBg", line)
            lines.append(truncate_to_width(line, width))

        lines.append(
            truncate_to_width(
                theme.fg(
                    "muted",
                    f"  ({self._selected_index + 1}/{len(self._filtered_nodes)})"
                    f"{self._status_labels()}",
                ),
                width,
            )
        )

        return lines

    # ---- input ------------------------------------------------------------

    def handle_input(self, data: str) -> None:  # noqa: C901 - 1:1 with the TS dispatch
        keybindings = get_keybindings()
        selected = self._selected()
        current_id = _entry_field(selected.node.entry, "id") if selected else None

        if keybindings.matches(data, "tui.select.up"):
            self._selected_index = (
                len(self._filtered_nodes) - 1
                if self._selected_index == 0
                else self._selected_index - 1
            )
        elif keybindings.matches(data, "tui.select.down"):
            self._selected_index = (
                0
                if self._selected_index == len(self._filtered_nodes) - 1
                else self._selected_index + 1
            )
        elif keybindings.matches(data, "app.tree.foldOrUp"):
            # One key, two jobs: fold what can fold, otherwise jump to where this
            # branch segment began.
            foldable_now = (
                current_id is not None
                and self._is_foldable(current_id)
                and current_id not in self._folded_nodes
            )
            if foldable_now and current_id is not None:
                self._folded_nodes.add(current_id)
                self._apply_filter()
            else:
                self._selected_index = self._find_branch_segment_start("up")
        elif keybindings.matches(data, "app.tree.unfoldOrDown"):
            if current_id and current_id in self._folded_nodes:
                self._folded_nodes.discard(current_id)
                self._apply_filter()
            else:
                self._selected_index = self._find_branch_segment_start("down")
        elif keybindings.matches(data, "tui.editor.cursorLeft") or keybindings.matches(
            data, "tui.select.pageUp"
        ):
            self._selected_index = max(0, self._selected_index - self._max_visible_lines)
        elif keybindings.matches(data, "tui.editor.cursorRight") or keybindings.matches(
            data, "tui.select.pageDown"
        ):
            self._selected_index = min(
                len(self._filtered_nodes) - 1, self._selected_index + self._max_visible_lines
            )
        elif keybindings.matches(data, "tui.select.confirm"):
            if selected is not None and self.on_select is not None:
                self.on_select(_entry_field(selected.node.entry, "id"))
        elif keybindings.matches(data, "tui.select.cancel"):
            # Escape clears the search first; only an already-clear list closes.
            if self._search_query:
                self._search_query = ""
                self._folded_nodes.clear()
                self._apply_filter()
            elif self.on_cancel is not None:
                self.on_cancel()
        elif keybindings.matches(data, "app.tree.filter.default"):
            self._set_filter_mode("default")
        elif keybindings.matches(data, "app.tree.filter.noTools"):
            self._toggle_filter_mode("no-tools")
        elif keybindings.matches(data, "app.tree.filter.userOnly"):
            self._toggle_filter_mode("user-only")
        elif keybindings.matches(data, "app.tree.filter.labeledOnly"):
            self._toggle_filter_mode("labeled-only")
        elif keybindings.matches(data, "app.tree.filter.all"):
            self._toggle_filter_mode("all")
        elif keybindings.matches(data, "app.tree.filter.cycleBackward"):
            index = FILTER_MODES.index(self._filter_mode)
            self._set_filter_mode(FILTER_MODES[(index - 1) % len(FILTER_MODES)])
        elif keybindings.matches(data, "app.tree.filter.cycleForward"):
            index = FILTER_MODES.index(self._filter_mode)
            self._set_filter_mode(FILTER_MODES[(index + 1) % len(FILTER_MODES)])
        elif keybindings.matches(data, "tui.editor.deleteCharBackward"):
            if self._search_query:
                self._search_query = self._search_query[:-1]
                self._folded_nodes.clear()
                self._apply_filter()
        elif keybindings.matches(data, "app.tree.editLabel"):
            if selected is not None and self.on_label_edit is not None:
                self.on_label_edit(_entry_field(selected.node.entry, "id"), selected.node.label)
        elif keybindings.matches(data, "app.tree.toggleLabelTimestamp"):
            self._show_label_timestamps = not self._show_label_timestamps
        else:
            # There is no search box to focus: anything printable is the query.
            has_control_chars = any(
                ord(char) < 32 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in data
            )
            if not has_control_chars and data:
                self._search_query += data
                self._folded_nodes.clear()
                self._apply_filter()

    def _set_filter_mode(self, mode: FilterMode) -> None:
        self._filter_mode = mode
        self._folded_nodes.clear()
        self._apply_filter()

    def _toggle_filter_mode(self, mode: FilterMode) -> None:
        self._set_filter_mode("default" if self._filter_mode == mode else mode)

    def _is_foldable(self, entry_id: str) -> bool:
        """Whether this node starts a segment worth collapsing."""
        children = self._visible_children_map.get(entry_id)
        if not children:
            return False
        parent_id = self._visible_parent_map.get(entry_id)
        if parent_id is None:
            return True
        siblings = self._visible_children_map.get(parent_id)
        return siblings is not None and len(siblings) > 1

    def _find_branch_segment_start(self, direction: Literal["up", "down"]) -> int:
        """The next branch point in that direction, following first children."""
        selected = self._selected()
        if selected is None:
            return self._selected_index
        selected_id = _entry_field(selected.node.entry, "id")

        index_by_entry_id = {
            _entry_field(n.node.entry, "id"): i for i, n in enumerate(self._filtered_nodes)
        }
        current_id: str = selected_id

        if direction == "down":
            while True:
                children = self._visible_children_map.get(current_id) or []
                if not children:
                    return index_by_entry_id[current_id]
                if len(children) > 1:
                    return index_by_entry_id[children[0]]
                current_id = children[0]

        while True:
            parent_id = self._visible_parent_map.get(current_id)
            if parent_id is None:
                return index_by_entry_id[current_id]
            children = self._visible_children_map.get(parent_id) or []
            if len(children) > 1:
                segment_start = index_by_entry_id[current_id]
                if segment_start < self._selected_index:
                    return segment_start
            current_id = parent_id


class SearchLine:
    """The one-line echo of what has been typed into the tree's search."""

    def __init__(self, tree_list: TreeList) -> None:
        self._tree_list = tree_list

    def invalidate(self) -> None:
        """Nothing is cached."""

    def render(self, width: int) -> list[str]:
        theme = get_theme()
        query = self._tree_list.get_search_query()
        prompt = theme.fg("muted", "Type to search:")
        if query:
            return [truncate_to_width(f"  {prompt} {theme.fg('accent', query)}", width)]
        return [truncate_to_width(f"  {prompt}", width)]

    def handle_input(self, data: str) -> None:
        """The tree list owns the keyboard; this only shows what it holds."""


class LabelInput:
    """The label editor that takes the tree's place while a label is being set."""

    def __init__(self, entry_id: str, current_label: str | None) -> None:
        self._entry_id = entry_id
        self.input = Input()
        if current_label:
            self.input.set_value(current_label)
        self.on_submit: Callable[[str, str | None], None] | None = None
        self.on_cancel: Callable[[], None] | None = None
        self._focused = False

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        self._focused = value
        self.input.focused = value

    def invalidate(self) -> None:
        """Nothing is cached."""

    def render(self, width: int) -> list[str]:
        theme = get_theme()
        indent = "  "
        available_width = width - len(indent)
        prompt = theme.fg("muted", "Label (empty to remove):")
        lines = [truncate_to_width(f"{indent}{prompt}", width)]
        lines.extend(
            truncate_to_width(f"{indent}{line}", width)
            for line in self.input.render(available_width)
        )
        lines.append(
            truncate_to_width(
                f"{indent}{key_hint('tui.select.confirm', 'save')}  "
                f"{key_hint('tui.select.cancel', 'cancel')}",
                width,
            )
        )
        return lines

    def handle_input(self, data: str) -> None:
        keybindings = get_keybindings()
        if keybindings.matches(data, "tui.select.confirm"):
            value = self.input.get_value().strip()
            if self.on_submit is not None:
                self.on_submit(self._entry_id, value or None)
        elif keybindings.matches(data, "tui.select.cancel"):
            if self.on_cancel is not None:
                self.on_cancel()
        else:
            self.input.handle_input(data)


class TreeSelectorComponent(Container):
    """The ``/tree`` overlay: title, key legend, search echo, and the tree."""

    def __init__(
        self,
        tree: list[Any],
        current_leaf_id: str | None,
        terminal_height: int,
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
        on_label_change: Callable[[str, str | None], None] | None = None,
        initial_selected_id: str | None = None,
        initial_filter_mode: FilterMode | None = None,
    ) -> None:
        super().__init__()
        theme = get_theme()

        self._on_label_change = on_label_change
        # Half the terminal, never less than five rows: the transcript above the
        # overlay is what tells you where you are in the session.
        max_visible_lines = max(5, terminal_height // 2)

        self.tree_list = TreeList(
            tree, current_leaf_id, max_visible_lines, initial_selected_id, initial_filter_mode
        )
        self.tree_list.on_select = on_select
        self.tree_list.on_cancel = on_cancel
        self.tree_list.on_label_edit = self._show_label_input

        self.tree_container = Container()
        self.tree_container.add_child(self.tree_list)
        self.label_input_container = Container()
        self.label_input: LabelInput | None = None
        self._focused = False

        self.add_child(Spacer(1))
        self.add_child(DynamicBorder())
        self.add_child(Text(theme.bold("  Session Tree"), 1, 0))
        filter_keys = "/".join(
            key_text(binding)
            for binding in (
                "app.tree.filter.default",
                "app.tree.filter.noTools",
                "app.tree.filter.userOnly",
                "app.tree.filter.labeledOnly",
                "app.tree.filter.all",
            )
        )
        cycle_keys = (
            f"{key_text('app.tree.filter.cycleForward')}/"
            f"{key_text('app.tree.filter.cycleBackward')}"
        )
        branch_keys = f"{key_text('app.tree.foldOrUp')}/{key_text('app.tree.unfoldOrDown')}"
        self.add_child(
            TruncatedText(
                theme.fg(
                    "muted",
                    f"  ↑/↓: move. ←/→: page. {branch_keys}: fold/branch. "
                    f"{key_text('app.tree.editLabel')}: label. {filter_keys}: filters "
                    f"({cycle_keys} cycle). "
                    f"{key_text('app.tree.toggleLabelTimestamp')}: label time",
                ),
                0,
                0,
            )
        )
        self.add_child(SearchLine(self.tree_list))
        self.add_child(DynamicBorder())
        self.add_child(Spacer(1))
        self.add_child(self.tree_container)
        self.add_child(self.label_input_container)
        self.add_child(Spacer(1))
        self.add_child(DynamicBorder())

        # An empty tree is nothing to navigate; the caller checks first, so this
        # is the belt to that's braces.
        if not tree:
            on_cancel()

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        self._focused = value
        if self.label_input is not None:
            self.label_input.focused = value

    def _show_label_input(self, entry_id: str, current_label: str | None) -> None:
        label_input = LabelInput(entry_id, current_label)

        def submit(target_id: str, label: str | None) -> None:
            self.tree_list.update_node_label(target_id, label)
            if self._on_label_change is not None:
                self._on_label_change(target_id, label)
            self._hide_label_input()

        label_input.on_submit = submit
        label_input.on_cancel = self._hide_label_input
        label_input.focused = self._focused
        self.label_input = label_input

        self.tree_container.clear()
        self.label_input_container.clear()
        self.label_input_container.add_child(label_input)

    def _hide_label_input(self) -> None:
        self.label_input = None
        self.label_input_container.clear()
        self.tree_container.clear()
        self.tree_container.add_child(self.tree_list)

    def handle_input(self, data: str) -> None:
        if self.label_input is not None:
            self.label_input.handle_input(data)
        else:
            self.tree_list.handle_input(data)

    def get_tree_list(self) -> TreeList:
        return self.tree_list

"""Tests for the built-in `edit` renderer.

The corpus proves an edit turn puts a coloured diff on screen. What is checked
here is the three decisions that make the block behave while the call is still
*streaming*: the preview waits for complete arguments, it is computed once per
distinct set of them, and the header colour tracks the outcome.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from cortex.code.interactive.components.tool_execution import (
    ToolExecutionComponent,
    ToolExecutionResult,
)
from cortex.code.interactive.tool_renderers import get_built_in_tool_renderer


def plain(lines: list[str]) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(lines))


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    (tmp_path / "greet.py").write_text('def greet():\n    print("hello")\n', encoding="utf-8")
    return tmp_path


def edit_block(workdir: Path, args: Any) -> ToolExecutionComponent:
    return ToolExecutionComponent(
        "edit", "call-1", args, None, get_built_in_tool_renderer("edit"), None, str(workdir)
    )


COMPLETE_ARGS = {
    "path": "greet.py",
    "edits": [{"oldText": 'print("hello")', "newText": 'print("goodbye")'}],
}


class TestEditCallRenderer:
    def test_the_call_line_names_the_file(self, workdir: Path):
        rendered = plain(edit_block(workdir, COMPLETE_ARGS).render(80))
        assert "edit" in rendered
        assert "greet.py" in rendered

    def test_a_path_that_has_not_arrived_yet_shows_a_placeholder(self, workdir: Path):
        rendered = plain(edit_block(workdir, {"path": ""}).render(80))
        assert "..." in rendered

    def test_a_path_of_the_wrong_type_says_so(self, workdir: Path):
        rendered = plain(edit_block(workdir, {"path": 42}).render(80))
        assert "[invalid arg]" in rendered

    def test_no_preview_until_the_arguments_are_complete(self, workdir: Path):
        # Half-streamed arguments name a real file and a plausible edit; diffing
        # them would show the user a change the model has not finished asking for
        # — and would read the file on every delta.
        component = edit_block(workdir, COMPLETE_ARGS)
        assert 'print("goodbye")' not in plain(component.render(80))

        component.set_args_complete()
        assert 'print("goodbye")' in plain(component.render(80))

    def test_the_preview_is_computed_once_per_set_of_arguments(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import cortex.code.tools as tools

        calls: list[str] = []
        real = tools.compute_edits_diff

        def counted(path: str, edits: Any, cwd: str) -> Any:
            calls.append(path)
            return real(path, edits, cwd)

        monkeypatch.setattr(tools, "compute_edits_diff", counted)

        component = edit_block(workdir, COMPLETE_ARGS)
        component.set_args_complete()
        component.render(80)
        component.invalidate()
        component.render(80)
        assert calls == ["greet.py"], f"the file was re-read: {calls!r}"

    def test_changing_the_arguments_recomputes_the_preview(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import cortex.code.tools as tools

        calls: list[str] = []
        real = tools.compute_edits_diff

        def counted(path: str, edits: Any, cwd: str) -> Any:
            calls.append(path)
            return real(path, edits, cwd)

        monkeypatch.setattr(tools, "compute_edits_diff", counted)

        component = edit_block(workdir, COMPLETE_ARGS)
        component.set_args_complete()
        component.render(80)
        component.update_args(
            {"path": "greet.py", "edits": [{"oldText": "def greet", "newText": "def hello"}]}
        )
        component.render(80)
        assert len(calls) == 2, f"the changed edit reused the old preview: {calls!r}"
        assert "def hello" in plain(component.render(80))

    def test_a_failing_edit_previews_the_error_instead(self, workdir: Path):
        component = edit_block(
            workdir,
            {"path": "greet.py", "edits": [{"oldText": "not in the file", "newText": "x"}]},
        )
        component.set_args_complete()
        assert "must match exactly" in plain(component.render(80))

    def test_the_legacy_single_edit_shape_still_previews(self, workdir: Path):
        component = edit_block(
            workdir, {"path": "greet.py", "oldText": 'print("hello")', "newText": "pass"}
        )
        component.set_args_complete()
        assert "pass" in plain(component.render(80))


def header_bg(component: ToolExecutionComponent, monkeypatch: pytest.MonkeyPatch) -> str:
    """Which theme background key the `edit` header painted itself with.

    Read off the *key*, not off the escape sequence: the three tool backgrounds
    are near-identical greys and all quantise to the same 256-colour index, so
    the rendered bytes cannot tell success from failure — but the user's terminal
    in true colour can, and the key is what decides.
    """
    from cortex.code.interactive.theme.theme import Theme

    seen: list[str] = []
    original = Theme.bg

    def record(self: Theme, color: str, text: str) -> str:
        seen.append(color)
        return original(self, color, text)

    monkeypatch.setattr(Theme, "bg", record)
    component.invalidate()
    component.render(80)
    backgrounds = [key for key in seen if key.startswith("tool")]
    assert backgrounds, "the edit header painted no background"
    return backgrounds[0]


class TestEditHeaderColour:
    def test_pending_while_the_arguments_stream(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        assert header_bg(edit_block(workdir, COMPLETE_ARGS), monkeypatch) == "toolPendingBg"

    def test_green_once_the_edit_previews_cleanly(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        component = edit_block(workdir, COMPLETE_ARGS)
        component.set_args_complete()
        assert header_bg(component, monkeypatch) == "toolSuccessBg"

    def test_red_when_the_preview_fails(self, workdir: Path, monkeypatch: pytest.MonkeyPatch):
        component = edit_block(
            workdir, {"path": "greet.py", "edits": [{"oldText": "missing", "newText": "x"}]}
        )
        component.set_args_complete()
        assert header_bg(component, monkeypatch) == "toolErrorBg"

    def test_red_when_the_tool_itself_fails(self, workdir: Path, monkeypatch: pytest.MonkeyPatch):
        from cortex.ai.types import TextContent

        component = edit_block(workdir, {"path": "greet.py"})
        component.set_args_complete()
        component.update_result(
            ToolExecutionResult(
                content=[TextContent(text="Could not edit file")], details=None, is_error=True
            )
        )
        assert header_bg(component, monkeypatch) == "toolErrorBg"


class TestEditResultRenderer:
    def test_a_result_matching_the_preview_adds_nothing(self, workdir: Path):
        from cortex.code.tools import Edit, EditDiffError, compute_edits_diff

        component = edit_block(workdir, COMPLETE_ARGS)
        component.set_args_complete()
        component.render(80)
        preview = compute_edits_diff(
            "greet.py",
            [Edit(old_text='print("hello")', new_text='print("goodbye")')],
            str(workdir),
        )
        assert not isinstance(preview, EditDiffError), preview

        before = plain(component.render(80))
        component.update_result(
            ToolExecutionResult(
                content=[], details={"diff": preview.diff, "first_changed_line": 2}, is_error=False
            )
        )
        # The call component already showed exactly this; drawing it twice is
        # what the TS's `resultDiff !== previewDiff` guard exists to prevent.
        assert plain(component.render(80)).count('print("goodbye")') == before.count(
            'print("goodbye")'
        )

    def test_an_error_result_shows_its_message(self, workdir: Path):
        from cortex.ai.types import TextContent

        component = edit_block(workdir, {"path": "greet.py"})
        component.update_result(
            ToolExecutionResult(
                content=[TextContent(text="Could not edit file: greet.py.")],
                details=None,
                is_error=True,
            )
        )
        assert "Could not edit file" in plain(component.render(80))

    def test_a_diff_the_preview_did_not_foresee_is_drawn(self, workdir: Path):
        component = edit_block(workdir, {"path": "greet.py"})
        component.set_args_complete()
        component.update_result(
            ToolExecutionResult(content=[], details={"diff": "+9 surprise"}, is_error=False)
        )
        assert "surprise" in plain(component.render(80))

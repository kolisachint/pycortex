"""Tests for the CLI entry point.

The interactive branch is exercised end-to-end by the corpus in
`cortex.code.e2e`; what is checked here is the dispatch around it — including
that a flag whose subsystem this port has not reached *fails*, rather than
printing a placeholder and reporting success to whatever is reading the exit
code.
"""

from __future__ import annotations

import pytest
from cortex.code.config import APP_NAME, VERSION
from cortex.code.interactive import InteractiveModeOptions
from cortex.code.main import main


class TestHelpAndVersion:
    def test_help_prints_usage(self, capsys: pytest.CaptureFixture[str]):
        assert main(["--help"]) == 0
        assert APP_NAME in capsys.readouterr().out

    def test_version_reports_the_release(self, capsys: pytest.CaptureFixture[str]):
        assert main(["--version"]) == 0
        assert capsys.readouterr().out.strip() == f"{APP_NAME} {VERSION}"


class TestUnavailableFlags:
    @pytest.mark.parametrize(
        ("argv", "flag", "step"),
        [
            (["--list-models"], "--list-models", "7.11"),
            (["--export", "out.md"], "--export", "7.10"),
            (["--print-token-surface"], "--print-token-surface", "7.4"),
        ],
    )
    def test_fails_loudly_and_names_the_step(
        self,
        argv: list[str],
        flag: str,
        step: str,
        capsys: pytest.CaptureFixture[str],
    ):
        assert main(argv) != 0
        err = capsys.readouterr().err
        assert flag in err
        assert step in err


class TestPrintMode:
    def test_reports_the_message_it_would_send(self, capsys: pytest.CaptureFixture[str]):
        assert main(["--print", "hello"]) == 0
        assert "hello" in capsys.readouterr().out

    def test_print_with_no_message_fails(self, capsys: pytest.CaptureFixture[str]):
        assert main(["--print"]) == 1
        assert "No message provided" in capsys.readouterr().out


class _RecordingRunner:
    """Stands in for `run_interactive_mode` at the seam.

    Stubbed rather than run for real: starting the actual interactive mode here
    would take over the terminal pytest is printing to.
    """

    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.calls: list[InteractiveModeOptions] = []

    def __call__(self, options: InteractiveModeOptions) -> int:
        self.calls.append(options)
        return self.code


class TestInteractive:
    def test_no_flags_starts_interactive_mode(self, monkeypatch: pytest.MonkeyPatch):
        """The whole point of 7.2: bare `pycortex` boots the TUI."""
        runner = _RecordingRunner()
        monkeypatch.setattr("cortex.code.main.run_interactive_mode", runner)
        assert main([]) == 0
        assert len(runner.calls) == 1

    def test_positional_messages_are_passed_through(self, monkeypatch: pytest.MonkeyPatch):
        runner = _RecordingRunner()
        monkeypatch.setattr("cortex.code.main.run_interactive_mode", runner)
        main(["say", "hello"])
        assert runner.calls[0].initial_messages == ["say", "hello"]

    def test_returns_the_exit_code_interactive_mode_produced(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("cortex.code.main.run_interactive_mode", _RecordingRunner(3))
        assert main([]) == 3

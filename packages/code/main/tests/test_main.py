"""Tests for the CLI entry point.

The interactive branch is exercised end-to-end by the corpus in
`cortex.code.e2e`; what is checked here is the dispatch around it — including
that a flag whose subsystem this port has not reached *fails*, rather than
printing a placeholder and reporting success to whatever is reading the exit
code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cortex.code.config import APP_NAME, VERSION
from cortex.code.interactive import InteractiveModeOptions, resolve_session_manager
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
            (["--export", "out.md"], "--export", "7.12"),
            (["--resume"], "--resume", "7.11"),
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
        self.session_managers: list[Any] = []

    def __call__(self, options: InteractiveModeOptions, session_manager: Any = None) -> int:
        self.calls.append(options)
        self.session_managers.append(session_manager)
        return self.code


class _RecordingResolver:
    """Stands in for `resolve_session_manager`.

    Stubbed for the same reason as the runner, one layer down: resolving a
    session for real creates a directory under the user's `~/.hoocode`, and a
    unit test must not touch it.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.manager = object()

    def __call__(self, cwd: str, **kwargs: Any) -> Any:
        self.calls.append({"cwd": cwd, **kwargs})
        return self.manager


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> tuple[_RecordingRunner, _RecordingResolver]:
    runner = _RecordingRunner()
    resolver = _RecordingResolver()
    monkeypatch.setattr("cortex.code.main.run_interactive_mode", runner)
    monkeypatch.setattr("cortex.code.main.resolve_session_manager", resolver)
    return runner, resolver


class TestInteractive:
    def test_no_flags_starts_interactive_mode(
        self, interactive: tuple[_RecordingRunner, _RecordingResolver]
    ):
        """The whole point of 7.2: bare `pycortex` boots the TUI."""
        runner, _ = interactive
        assert main([]) == 0
        assert len(runner.calls) == 1

    def test_positional_messages_are_passed_through(
        self, interactive: tuple[_RecordingRunner, _RecordingResolver]
    ):
        runner, _ = interactive
        main(["say", "hello"])
        assert runner.calls[0].initial_messages == ["say", "hello"]

    def test_returns_the_exit_code_interactive_mode_produced(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("cortex.code.main.run_interactive_mode", _RecordingRunner(3))
        monkeypatch.setattr("cortex.code.main.resolve_session_manager", _RecordingResolver())
        assert main([]) == 3


class TestSessionFlags:
    """Which session file the process opens on. Step 7.10."""

    def test_no_flags_asks_for_a_new_session(
        self, interactive: tuple[_RecordingRunner, _RecordingResolver]
    ):
        runner, resolver = interactive
        main([])
        assert resolver.calls[0]["continue_session"] is False
        assert resolver.calls[0]["session_path"] is None
        # And whatever it resolved is what the mode is handed.
        assert runner.session_managers == [resolver.manager]

    @pytest.mark.parametrize("flag", ["--continue", "-c"])
    def test_continue_asks_for_the_most_recent(
        self, flag: str, interactive: tuple[_RecordingRunner, _RecordingResolver]
    ):
        _, resolver = interactive
        main([flag])
        assert resolver.calls[0]["continue_session"] is True

    def test_no_session_asks_for_an_ephemeral_one(
        self, interactive: tuple[_RecordingRunner, _RecordingResolver]
    ):
        _, resolver = interactive
        main(["--no-session"])
        assert resolver.calls[0]["no_session"] is True

    def test_session_passes_the_path_and_the_directory(
        self, tmp_path: Path, interactive: tuple[_RecordingRunner, _RecordingResolver]
    ):
        session_file = tmp_path / "a.jsonl"
        session_file.write_text("{}\n", encoding="utf-8")
        _, resolver = interactive
        main(["--session", str(session_file), "--session-dir", str(tmp_path)])
        assert resolver.calls[0]["session_path"] == str(session_file)
        assert resolver.calls[0]["session_dir"] == str(tmp_path)

    def test_a_session_path_that_is_not_there_is_reported(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        interactive: tuple[_RecordingRunner, _RecordingResolver],
    ):
        # Silently starting a *new* session under that name is the one answer
        # that would look like it worked.
        runner, _ = interactive
        assert main(["--session", str(tmp_path / "gone.jsonl")]) == 1
        assert "no session file at" in capsys.readouterr().err
        assert runner.calls == []


class TestResolveSessionManager:
    """The resolver itself, against real files."""

    def test_defaults_to_a_new_session(self, tmp_path: Path):
        manager = resolve_session_manager(str(tmp_path), session_dir=str(tmp_path))
        assert manager.get_entries() == []
        assert manager.is_persisted()

    def test_continue_reopens_the_most_recent(self, tmp_path: Path):
        from cortex.ai.providers.faux import faux_assistant_message
        from cortex.code.session import SessionManager

        first = SessionManager(str(tmp_path), str(tmp_path))
        first.append_message({"role": "user", "content": "earlier", "timestamp": 1})
        first.append_message(faux_assistant_message("answered").model_dump())

        manager = resolve_session_manager(
            str(tmp_path), continue_session=True, session_dir=str(tmp_path)
        )
        assert manager.get_session_file() == first.get_session_file()

    def test_no_session_never_reaches_the_disk(self, tmp_path: Path):
        manager = resolve_session_manager(str(tmp_path), no_session=True, session_dir=str(tmp_path))
        assert not manager.is_persisted()

    def test_an_explicit_path_wins_over_continue(self, tmp_path: Path):
        from cortex.ai.providers.faux import faux_assistant_message
        from cortex.code.session import SessionManager

        named = SessionManager(str(tmp_path), str(tmp_path))
        named.append_message({"role": "user", "content": "the named one", "timestamp": 1})
        named.append_message(faux_assistant_message("answered").model_dump())
        named_file = named.get_session_file()
        assert named_file is not None

        later = SessionManager(str(tmp_path), str(tmp_path))
        later.append_message({"role": "user", "content": "the later one", "timestamp": 2})
        later.append_message(faux_assistant_message("answered").model_dump())

        manager = resolve_session_manager(
            str(tmp_path),
            continue_session=True,
            session_path=named_file,
            session_dir=str(tmp_path),
        )
        assert manager.get_session_file() == named_file

"""Tests for the CLI entry point.

The interactive branch is exercised end-to-end by the corpus in
`cortex.code.e2e`; what is checked here is the dispatch around it — including
that a flag whose subsystem this port has not reached *fails*, rather than
printing a placeholder and reporting success to whatever is reading the exit
code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cortex.code.config import APP_NAME, ENV_AGENT_DIR, ENV_SESSION_DIR, VERSION
from cortex.code.interactive import InteractiveModeOptions, resolve_session_manager
from cortex.code.main import main, resolve_session_dir


@pytest.fixture
def clean_agent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A config directory with nothing in it, and one provider key.

    Every test that reaches the registry, the settings file or the session
    directory needs this: without it the run reads whatever the machine running
    the suite happens to have configured, which is both flaky and rude.
    """
    agent_dir = tmp_path / "config"
    agent_dir.mkdir()
    monkeypatch.setenv(ENV_AGENT_DIR, str(agent_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", ENV_SESSION_DIR):
        monkeypatch.delenv(name, raising=False)
    return agent_dir


@pytest.fixture
def faux_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """`build_startup_session` answering with a session on the faux provider."""
    from cortex.ai.providers.faux import faux_assistant_message, register_faux_provider
    from cortex.code.config import SettingsManager
    from cortex.code.config.settings_storage import InMemorySettingsStorage
    from cortex.code.interactive import StartupSession
    from cortex.code.session import SessionManager, create_agent_session

    registration = register_faux_provider()
    settings = SettingsManager.from_storage(InMemorySettingsStorage())
    created = create_agent_session(
        cwd=str(tmp_path),
        settings_manager=settings,
        session_manager=SessionManager(str(tmp_path), "", persist=False),
        model=registration.get_model(),
    )

    class Startup:
        session = created.session

        @staticmethod
        def set_responses(texts: list[str]) -> None:
            registration.set_responses([faux_assistant_message(text) for text in texts])

    def startup(**_kwargs: Any) -> StartupSession:
        return StartupSession(session=created.session, settings=settings, cwd=str(tmp_path))

    monkeypatch.setattr("cortex.code.main.build_startup_session", startup)
    monkeypatch.setattr("cortex.code.main.resolve_session_manager", _RecordingResolver())
    try:
        yield Startup()
    finally:
        registration.unregister()


class TestHelpAndVersion:
    def test_help_prints_usage(self, capsys: pytest.CaptureFixture[str]):
        assert main(["--help"]) == 0
        assert APP_NAME in capsys.readouterr().out

    def test_version_reports_the_release(self, capsys: pytest.CaptureFixture[str]):
        # Bare, as `main.ts` prints it — `hoocode --version | …` reads a version.
        assert main(["--version"]) == 0
        assert capsys.readouterr().out.strip() == VERSION

    def test_help_names_the_environment_variables_that_work(
        self, capsys: pytest.CaptureFixture[str]
    ):
        # The help named `HOOCODE_AGENT_DIR`, which nothing reads: the variable
        # is `HOOCODE_CODING_AGENT_DIR`, and a user following the help would have
        # set one that does nothing.
        assert main(["--help"]) == 0
        out = capsys.readouterr().out
        assert ENV_AGENT_DIR in out
        assert ENV_SESSION_DIR in out


class TestUnavailableFlags:
    @pytest.mark.parametrize(
        ("argv", "flag"),
        [
            (["--export", "out.md"], "--export"),
            (["--resume"], "--resume"),
            (["--print-token-surface"], "--print-token-surface"),
        ],
    )
    def test_fails_loudly_and_names_the_flag(
        self,
        argv: list[str],
        flag: str,
        capsys: pytest.CaptureFixture[str],
    ):
        assert main(argv) != 0
        err = capsys.readouterr().err
        assert flag in err
        assert "does not have" in err


class TestListModels:
    """`--list-models`, which four error messages in this port already promise."""

    def test_lists_the_models_a_key_makes_reachable(
        self, clean_agent_dir: Path, capsys: pytest.CaptureFixture[str]
    ):
        assert main(["--list-models"]) == 0
        out = capsys.readouterr().out
        assert out.splitlines()[0].split() == [
            "provider",
            "model",
            "context",
            "max-out",
            "thinking",
            "images",
        ]
        assert "anthropic" in out
        # The table is about reachability, so a provider with no credentials is
        # not in it — that is the difference between this and a model catalogue.
        assert "openai" not in out
        # Sorted by provider, then by model id: the list is read by eye, and an
        # unordered one is a different (worse) thing to read.
        rows = [line.split() for line in out.splitlines()[1:] if line.strip()]
        keys = [(row[0], row[1]) for row in rows]
        assert keys == sorted(keys), keys

    def test_a_pattern_filters(self, clean_agent_dir: Path, capsys: pytest.CaptureFixture[str]):
        # The filter is `fuzzy_filter`, so the pattern is a subsequence rather
        # than a substring — `opus` alone also matches `claude-sOnnet…` — which
        # is the TS's behaviour and why the pattern here is a longer one.
        assert main(["--list-models", "claude-opus"]) == 0
        rows = [line for line in capsys.readouterr().out.splitlines()[1:] if line.strip()]
        assert rows, "the pattern matched nothing at all"
        assert all("opus" in row for row in rows), rows

    def test_a_pattern_that_matches_nothing_says_so(
        self, clean_agent_dir: Path, capsys: pytest.CaptureFixture[str]
    ):
        assert main(["--list-models", "zzzz"]) == 0
        assert 'No models matching "zzzz"' in capsys.readouterr().out

    def test_no_credentials_explains_itself(
        self,
        monkeypatch: pytest.MonkeyPatch,
        clean_agent_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert main(["--list-models"]) == 0
        assert "No models available" in capsys.readouterr().out


class TestPrintMode:
    """`-p`, wired to the real `cortex.code.print` at 7.12.

    Startup is stubbed at :func:`build_startup_session` — the seam `main` shares
    with the TUI — so the mode runs for real against the faux provider instead
    of reaching for the machine's credentials.
    """

    def test_answers_on_stdout_and_exits_zero(
        self, faux_startup: Any, capsys: pytest.CaptureFixture[str]
    ):
        faux_startup.set_responses(["Grace Hopper wrote the first compiler."])
        assert main(["-p", "who wrote the first compiler?"]) == 0
        out = capsys.readouterr().out
        assert out.strip() == "Grace Hopper wrote the first compiler."

    def test_later_messages_are_sent_as_further_turns(
        self, faux_startup: Any, capsys: pytest.CaptureFixture[str]
    ):
        faux_startup.set_responses(["first answer", "second answer"])
        assert main(["-p", "one", "two"]) == 0
        # Only the last assistant message is printed in text mode, and both
        # prompts reached the session.
        assert capsys.readouterr().out.strip() == "second answer"
        roles = [getattr(message, "role", "") for message in faux_startup.session.messages]
        assert roles == ["user", "assistant", "user", "assistant"]

    def test_an_unresolvable_model_is_fatal(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        from cortex.code.interactive import StartupSession

        def failing(**_kwargs: Any) -> StartupSession:
            return StartupSession(
                session=None,
                settings=None,  # pyright: ignore[reportArgumentType]
                cwd=".",
                error="Model not found: nope/nope",
            )

        monkeypatch.setattr("cortex.code.main.build_startup_session", failing)
        monkeypatch.setattr("cortex.code.main.resolve_session_manager", _RecordingResolver())
        assert main(["-p", "hello"]) == 1
        assert "Model not found" in capsys.readouterr().err

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
def interactive(
    monkeypatch: pytest.MonkeyPatch, clean_agent_dir: Path
) -> tuple[_RecordingRunner, _RecordingResolver]:
    runner = _RecordingRunner()
    resolver = _RecordingResolver()
    monkeypatch.setattr("cortex.code.main.run_interactive_mode", runner)
    monkeypatch.setattr("cortex.code.main.resolve_session_manager", resolver)
    return runner, resolver


class TestResolveSessionDir:
    """Where sessions are written. Port of `main.ts`'s three-way resolution."""

    def test_the_flag_wins(self, clean_agent_dir: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(ENV_SESSION_DIR, "/from/env")
        assert resolve_session_dir("/from/flag", str(clean_agent_dir)) == "/from/flag"

    def test_then_the_environment(self, clean_agent_dir: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(ENV_SESSION_DIR, "/from/env")
        assert resolve_session_dir(None, str(clean_agent_dir)) == "/from/env"

    def test_the_environment_expands_a_tilde(
        self, clean_agent_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(ENV_SESSION_DIR, "~/sessions")
        resolved = resolve_session_dir(None, str(clean_agent_dir))
        assert resolved is not None and not resolved.startswith("~")

    def test_then_the_setting(self, clean_agent_dir: Path, tmp_path: Path):
        (clean_agent_dir / "settings.json").write_text(
            json.dumps({"session_dir": str(tmp_path / "kept")}), encoding="utf-8"
        )
        assert resolve_session_dir(None, str(tmp_path)) == str(tmp_path / "kept")

    def test_otherwise_the_default(self, clean_agent_dir: Path, tmp_path: Path):
        # `None` means "beside the agent directory", which `SessionManager`
        # works out for itself.
        assert resolve_session_dir(None, str(tmp_path)) is None


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

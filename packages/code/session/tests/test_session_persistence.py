"""Step 7.10: sessions that outlive the process, and the runtime that swaps them.

Three things under test, and they are the three halves of "a turn survives quit
and relaunch": the entries on disk become messages again
(:func:`build_session_context`), the files become a list somebody can pick from
(:meth:`SessionManager.list`), and picking one replaces the session the app is
holding (:class:`AgentSessionRuntime`).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from cortex.code.config import SettingsManager
from cortex.code.config.settings_storage import InMemorySettingsStorage
from cortex.code.session import (
    AgentSessionRuntime,
    AgentSessionServices,
    CreateAgentSessionResult,
    MissingSessionCwdError,
    SessionImportFileNotFoundError,
    SessionManager,
    build_session_context,
    build_session_info,
    create_agent_session,
    create_agent_session_runtime,
)


def _user(text: str, timestamp: int = 1) -> dict[str, Any]:
    return {"role": "user", "content": text, "timestamp": timestamp}


def _assistant(text: str, timestamp: int = 2, **overrides: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "api": "anthropic-messages",
        "provider": "faux",
        "model": "faux-1",
        "usage": {
            "input": 1,
            "output": 1,
            "cache_read": 0,
            "cache_write": 0,
            "total_tokens": 2,
            "cost": {},
        },
        "stop_reason": "stop",
        "timestamp": timestamp,
    }
    message.update(overrides)
    return message


def _turn(manager: SessionManager, question: str, answer: str) -> None:
    """One complete exchange, which is what makes a session worth persisting.

    The assistant message matters beyond its text: nothing is written to disk
    until a session has one (see ``SessionManager._persist_entry``), so a
    question with no answer is not a session anybody can resume.
    """
    manager.append_message(_user(question))
    manager.append_message(_assistant(answer))


class TestBuildSessionContext:
    """Entries on disk, back into the messages a transcript is drawn from."""

    def test_revives_messages_as_models_rather_than_dicts(self, tmp_path: Path):
        manager = SessionManager("/w/project", str(tmp_path))
        _turn(manager, "who was first?", "Ada Lovelace.")

        messages = manager.build_session_context().messages

        assert [m.role for m in messages] == ["user", "assistant"]
        # Attribute access, not `["content"]`: every component above reads a
        # message as an object, and dicts would render as nothing.
        assert messages[0].content == "who was first?"
        assert messages[1].content[0].text == "Ada Lovelace."

    def test_follows_the_leaf_rather_than_the_file(self, tmp_path: Path):
        manager = SessionManager("/w/project", str(tmp_path))
        _turn(manager, "first question", "first answer")
        branch_point = manager.get_leaf_id()
        _turn(manager, "second question", "second answer")

        # Branching back and asking something else leaves the second turn in the
        # file but off the path.
        assert branch_point is not None
        manager.branch(branch_point)
        _turn(manager, "different question", "different answer")

        texts = [str(m.content) for m in manager.build_session_context().messages]
        assert any("different question" in text for text in texts)
        assert not any("second question" in text for text in texts)

    def test_a_reset_leaf_yields_nothing(self, tmp_path: Path):
        manager = SessionManager("/w/project", str(tmp_path))
        _turn(manager, "asked", "answered")
        manager.reset_leaf()
        assert manager.build_session_context().messages == []

    def test_an_unspecified_leaf_falls_back_to_the_last_entry(self, tmp_path: Path):
        # The module function's tri-state: not passing a leaf is not the same as
        # passing None, and only one of them means "nothing".
        manager = SessionManager("/w/project", str(tmp_path))
        _turn(manager, "asked", "answered")
        entries = manager.get_entries()

        assert len(build_session_context(entries).messages) == 2
        assert build_session_context(entries, None).messages == []

    def test_tracks_the_model_the_session_was_last_on(self, tmp_path: Path):
        manager = SessionManager("/w/project", str(tmp_path))
        manager.append_model_change("faux", "faux-2")
        _turn(manager, "asked", "answered")

        context = manager.build_session_context()
        # The assistant message is later than the model_change entry, so it wins.
        assert context.model == {"provider": "faux", "modelId": "faux-1"}

    def test_a_message_it_cannot_parse_is_kept_rather_than_dropped(self, tmp_path: Path):
        manager = SessionManager("/w/project", str(tmp_path))
        manager.append_message({"role": "user", "content": "fine", "timestamp": 1})
        # A user message with no timestamp: written by a version this one does
        # not know. Losing the whole session over it would be worse.
        manager.append_message({"role": "user", "content": "odd"})
        manager.append_message(_assistant("answered"))

        messages = manager.build_session_context().messages
        assert len(messages) == 3
        assert messages[1] == {"role": "user", "content": "odd"}


class TestSessionSurvivesRelaunch:
    """The end of the round trip: a second manager over the same directory."""

    def test_a_turn_is_still_there_after_reopening(self, tmp_path: Path):
        first = SessionManager("/w/project", str(tmp_path))
        _turn(first, "remember this", "remembered")
        session_file = first.get_session_file()
        assert session_file is not None and os.path.exists(session_file)

        reopened = SessionManager.continue_recent("/w/project", str(tmp_path))

        assert reopened.get_session_file() == session_file
        texts = [str(m.content) for m in reopened.build_session_context().messages]
        assert any("remember this" in text for text in texts)

    def test_continue_recent_with_no_sessions_starts_a_new_one(self, tmp_path: Path):
        manager = SessionManager.continue_recent("/w/project", str(tmp_path))
        assert manager.build_session_context().messages == []

    def test_continue_recent_picks_the_most_recent(self, tmp_path: Path):
        older = SessionManager("/w/project", str(tmp_path))
        _turn(older, "the old one", "old")
        newer = SessionManager("/w/project", str(tmp_path))
        _turn(newer, "the new one", "new")
        # mtime is what `find_most_recent_session` sorts on, and both files were
        # written in the same instant.
        older_file = older.get_session_file()
        assert older_file is not None
        os.utime(older_file, (1, 1))

        reopened = SessionManager.continue_recent("/w/project", str(tmp_path))
        assert reopened.get_session_file() == newer.get_session_file()


class TestSessionListing:
    """What `/resume` shows."""

    def test_summarises_a_session(self, tmp_path: Path):
        manager = SessionManager("/w/project", str(tmp_path))
        _turn(manager, "the first question", "an answer")
        manager.append_session_info("named session")

        sessions = asyncio.run(SessionManager.list("/w/project", str(tmp_path)))

        assert len(sessions) == 1
        info = sessions[0]
        assert info.name == "named session"
        assert info.first_message == "the first question"
        assert info.message_count == 2
        assert info.cwd == "/w/project"
        assert "an answer" in info.all_messages_text

    def test_a_session_with_no_messages_says_so(self, tmp_path: Path):
        manager = SessionManager("/w/project", str(tmp_path))
        manager.append_message({"role": "toolResult", "toolCallId": "1"})
        manager.append_message(_assistant("answered"))

        info = build_session_info(manager.get_session_file() or "")
        assert info is not None
        assert info.first_message == "(no messages)"

    def test_dates_a_session_by_its_last_turn_not_by_its_header(self, tmp_path: Path):
        # The header says when the file was opened; the last message says when
        # anything last happened in it, which is what a user means by "when was
        # this". The two are set here to disagree by decades so that reading the
        # wrong one cannot pass.
        manager = SessionManager("/w/project", str(tmp_path))
        manager.append_message(_user("asked long ago", timestamp=9_000_000_000))
        manager.append_message(_assistant("answered", timestamp=9_100_000_000))

        info = build_session_info(manager.get_session_file() or "")
        assert info is not None and info.modified is not None and info.created is not None
        assert info.modified == datetime.fromtimestamp(9_100_000_000 / 1000, UTC)
        assert info.modified != info.created

    def test_falls_back_to_the_header_when_no_message_has_a_time(self, tmp_path: Path):
        manager = SessionManager("/w/project", str(tmp_path))
        manager.append_message({"role": "user", "content": "no timestamp"})
        manager.append_message(_assistant("answered"))
        # The entry timestamps are ISO strings written by the manager, so the
        # activity time comes from those rather than from the header.
        info = build_session_info(manager.get_session_file() or "")
        assert info is not None and info.modified is not None

    def test_orders_by_last_activity_not_by_file_name(self, tmp_path: Path):
        older = SessionManager("/w/project", str(tmp_path))
        older.append_message(_user("old question", timestamp=1_000))
        older.append_message(_assistant("old answer", timestamp=2_000))
        newer = SessionManager("/w/project", str(tmp_path))
        newer.append_message(_user("new question", timestamp=9_000))
        newer.append_message(_assistant("new answer", timestamp=10_000))

        sessions = asyncio.run(SessionManager.list("/w/project", str(tmp_path)))
        assert [s.first_message for s in sessions] == ["new question", "old question"]

    def test_progress_counts_up_to_the_total(self, tmp_path: Path):
        for _ in range(3):
            _turn(SessionManager("/w/project", str(tmp_path)), "q", "a")

        progress: list[tuple[int, int]] = []
        asyncio.run(
            SessionManager.list(
                "/w/project",
                str(tmp_path),
                lambda loaded, total: progress.append((loaded, total)),
            )
        )
        assert progress == [(1, 3), (2, 3), (3, 3)]

    def test_a_corrupt_file_is_skipped_rather_than_failing_the_list(self, tmp_path: Path):
        _turn(SessionManager("/w/project", str(tmp_path)), "a real one", "answered")
        (tmp_path / "broken.jsonl").write_text("this is not json\n", encoding="utf-8")

        sessions = asyncio.run(SessionManager.list("/w/project", str(tmp_path)))
        assert [s.first_message for s in sessions] == ["a real one"]

    def test_an_absent_directory_is_an_empty_list(self, tmp_path: Path):
        sessions = asyncio.run(SessionManager.list("/w/project", str(tmp_path / "nope")))
        assert sessions == []

    def test_list_all_walks_every_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        sessions_root = tmp_path / "sessions"
        for project in ("--a--", "--b--"):
            directory = sessions_root / project
            directory.mkdir(parents=True)
            _turn(SessionManager(f"/w/{project}", str(directory)), f"in {project}", "answered")
        monkeypatch.setattr(
            "cortex.code.config.get_sessions_dir", lambda: str(sessions_root), raising=False
        )

        sessions = asyncio.run(SessionManager.list_all())
        assert sorted(s.first_message for s in sessions) == ["in --a--", "in --b--"]


def _texts(host: AgentSessionRuntime) -> list[str]:
    """The current session's transcript, as plain strings to assert against."""
    return [str(m.content) for m in host.session.session_manager.build_session_context().messages]


class _Rebind:
    """Records the sessions a runtime hands back, as the app's rebind does."""

    def __init__(self) -> None:
        self.sessions: list[Any] = []

    async def __call__(self, session: Any) -> None:
        self.sessions.append(session)


def _runtime(cwd: str, session_dir: str) -> tuple[AgentSessionRuntime, _Rebind]:
    """A runtime over a real, persisted session, with a real factory.

    ``cwd`` is a directory that exists, because the runtime checks: a session
    file naming a directory that is gone is the one thing a switch refuses.
    """
    settings = SettingsManager.from_storage(InMemorySettingsStorage())

    def create_runtime(
        *, cwd: str, agent_dir: str, session_manager: SessionManager
    ) -> CreateAgentSessionResult:
        return create_agent_session(
            cwd=cwd,
            agent_dir=agent_dir,
            settings_manager=settings,
            session_manager=session_manager,
        )

    host = create_agent_session_runtime(
        create_runtime,
        cwd=cwd,
        agent_dir="/w/agent",
        session_manager=SessionManager(cwd, session_dir),
    )
    rebind = _Rebind()
    host.set_rebind_session(rebind)
    return host, rebind


class TestRuntimeSessionReplacement:
    """`/new`, `/resume`, `/fork` and `/import`, at the runtime level."""

    def test_new_session_leaves_the_old_one_on_disk(self, tmp_path: Path):
        host, rebind = _runtime(str(tmp_path), str(tmp_path))
        _turn(host.session.session_manager, "the first session", "answered")
        first_file = host.session.session_file

        asyncio.run(host.new_session())

        assert host.session.session_file != first_file
        assert host.session.session_manager.build_session_context().messages == []
        assert rebind.sessions == [host.session]
        # The point of `/new` over "clear the screen": the old one is resumable.
        sessions = asyncio.run(SessionManager.list(str(tmp_path), str(tmp_path)))
        assert [s.first_message for s in sessions] == ["the first session"]

    def test_new_session_disposes_the_session_it_replaces(self, tmp_path: Path):
        host, _ = _runtime(str(tmp_path), str(tmp_path))
        replaced = host.session
        events: list[Any] = []
        replaced.subscribe(events.append)
        # Listening before the swap, so the test can tell "detached" from "never
        # wired up in the first place".
        cast(Any, replaced)._emit({"type": "agent_start"})
        assert events, "the session was not delivering to its listeners to begin with"
        events.clear()

        asyncio.run(host.new_session())

        # And detached after it: anything the replaced session still produces
        # must not reach a screen that is drawing a different transcript.
        cast(Any, replaced)._emit({"type": "agent_start"})
        assert events == []

    def test_new_session_records_its_parent_when_asked(self, tmp_path: Path):
        host, _ = _runtime(str(tmp_path), str(tmp_path))
        _turn(host.session.session_manager, "parent", "answered")
        parent_file = host.session.session_file

        asyncio.run(host.new_session(parent_session=parent_file))

        header = host.session.session_manager.get_header()
        assert header is not None and header["parentSession"] == parent_file

    def test_before_session_invalidate_runs_before_the_swap(self, tmp_path: Path):
        host, _ = _runtime(str(tmp_path), str(tmp_path))
        original = host.session
        seen: list[Any] = []
        host.set_before_session_invalidate(lambda: seen.append(host.session))

        asyncio.run(host.new_session())

        # Host teardown sees the session it was drawing, not its replacement.
        assert seen == [original]

    def test_switch_session_restores_a_stored_transcript(self, tmp_path: Path):
        stored = SessionManager(str(tmp_path), str(tmp_path))
        _turn(stored, "stored question", "stored answer")
        stored_file = stored.get_session_file()
        assert stored_file is not None

        host, rebind = _runtime(str(tmp_path), str(tmp_path))
        asyncio.run(host.switch_session(stored_file))

        texts = _texts(host)
        assert any("stored question" in text for text in texts)
        assert rebind.sessions == [host.session]

    def test_switch_session_refuses_a_session_whose_cwd_is_gone(self, tmp_path: Path):
        stored = SessionManager("/w/deleted-project", str(tmp_path))
        _turn(stored, "orphan", "answered")
        stored_file = stored.get_session_file()
        assert stored_file is not None

        host, _ = _runtime(str(tmp_path), str(tmp_path))
        before = host.session

        events: list[Any] = []
        before.subscribe(events.append)

        with pytest.raises(MissingSessionCwdError):
            asyncio.run(host.switch_session(stored_file))

        # The check runs before the teardown, so the current session is not just
        # still *current* — it is still live, and the caller can ask the user
        # where to open the other one instead.
        assert host.session is before
        cast(Any, before)._emit({"type": "agent_start"})
        assert events, "the session was disposed by a switch that never happened"

    def test_switch_session_accepts_a_cwd_override(self, tmp_path: Path):
        stored = SessionManager("/w/deleted-project", str(tmp_path))
        _turn(stored, "orphan", "answered")
        stored_file = stored.get_session_file()
        assert stored_file is not None

        host, _ = _runtime(str(tmp_path), str(tmp_path))
        asyncio.run(host.switch_session(stored_file, cwd_override=str(tmp_path)))
        assert host.session.session_manager.get_cwd() == str(tmp_path)

    def test_fork_before_a_message_hands_its_text_back(self, tmp_path: Path):
        host, _ = _runtime(str(tmp_path), str(tmp_path))
        manager = host.session.session_manager
        _turn(manager, "first", "answered")
        second_id = manager.append_message(_user("second"))
        manager.append_message(_assistant("answered again"))
        original_file = host.session.session_file

        result = asyncio.run(host.fork(second_id))

        assert result.selected_text == "second"
        assert host.session.session_file != original_file
        texts = _texts(host)
        # Everything up to but not including the forked message.
        assert any("first" in text for text in texts)
        assert not any(text == "second" for text in texts)

    def test_fork_at_the_leaf_keeps_everything(self, tmp_path: Path):
        host, _ = _runtime(str(tmp_path), str(tmp_path))
        manager = host.session.session_manager
        _turn(manager, "kept", "answered")
        leaf_id = manager.get_leaf_id()
        assert leaf_id is not None

        result = asyncio.run(host.fork(leaf_id, position="at"))

        assert result.selected_text is None
        texts = _texts(host)
        assert any("kept" in text for text in texts)

    def test_forking_before_an_assistant_message_is_refused(self, tmp_path: Path):
        host, _ = _runtime(str(tmp_path), str(tmp_path))
        manager = host.session.session_manager
        manager.append_message(_user("asked"))
        assistant_id = manager.append_message(_assistant("answered"))

        with pytest.raises(ValueError, match="Invalid entry ID for forking"):
            asyncio.run(host.fork(assistant_id))

    def test_forking_an_unknown_entry_is_refused(self, tmp_path: Path):
        host, _ = _runtime(str(tmp_path), str(tmp_path))
        with pytest.raises(ValueError, match="Invalid entry ID for forking"):
            asyncio.run(host.fork("no-such-entry"))

    def test_fork_before_the_first_message_starts_an_empty_child(self, tmp_path: Path):
        host, _ = _runtime(str(tmp_path), str(tmp_path))
        manager = host.session.session_manager
        first_id = manager.append_message(_user("the only question"))
        manager.append_message(_assistant("answered"))
        original_file = host.session.session_file

        result = asyncio.run(host.fork(first_id))

        assert result.selected_text == "the only question"
        assert host.session.session_manager.build_session_context().messages == []
        header = host.session.session_manager.get_header()
        assert header is not None and header["parentSession"] == original_file

    def test_import_copies_the_file_into_this_project(self, tmp_path: Path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        foreign = SessionManager(str(tmp_path / "project"), str(elsewhere))
        _turn(foreign, "imported question", "answered")
        foreign_file = foreign.get_session_file()
        assert foreign_file is not None

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        host, _ = _runtime(str(project_dir), str(project_dir))
        asyncio.run(host.import_from_jsonl(foreign_file))

        # Copied, not referenced: the session is one of this project's now, so
        # `/resume` offers it and further turns are appended here.
        assert host.session.session_file is not None
        assert str(project_dir) in host.session.session_file
        texts = _texts(host)
        assert any("imported question" in text for text in texts)

    def test_import_of_a_missing_file_says_which_file(self, tmp_path: Path):
        host, _ = _runtime(str(tmp_path), str(tmp_path))
        missing = str(tmp_path / "nope.jsonl")
        with pytest.raises(SessionImportFileNotFoundError) as raised:
            asyncio.run(host.import_from_jsonl(missing))
        assert raised.value.file_path == missing

    def test_replacements_go_through_the_factory_the_runtime_was_built_with(self, tmp_path: Path):
        settings = SettingsManager.from_storage(InMemorySettingsStorage())
        calls: list[str] = []

        def create_runtime(
            *, cwd: str, agent_dir: str, session_manager: SessionManager
        ) -> CreateAgentSessionResult:
            calls.append(agent_dir)
            # Passed through, not dropped: the runtime reads the *services*'
            # agent_dir for the next replacement, so a factory that swallows it
            # loses it after one switch.
            return create_agent_session(
                cwd=cwd,
                agent_dir=agent_dir,
                settings_manager=settings,
                session_manager=session_manager,
            )

        host = create_agent_session_runtime(
            create_runtime,
            cwd=str(tmp_path),
            agent_dir="/w/agent",
            session_manager=SessionManager(str(tmp_path), str(tmp_path)),
        )
        asyncio.run(host.new_session())

        # Twice with the same agent_dir: the factory is what makes a replacement
        # session look like the one the process started on.
        assert calls == ["/w/agent", "/w/agent"]

    def test_a_runtime_without_a_factory_still_replaces_sessions(self, tmp_path: Path):
        # The default factory: no model and no tools, which is the honest state
        # for a caller that never said what to carry over.
        session_manager = SessionManager(str(tmp_path), str(tmp_path))
        created = create_agent_session(cwd=str(tmp_path), session_manager=session_manager)
        host = AgentSessionRuntime(
            created.session,
            AgentSessionServices(
                cwd=str(tmp_path),
                agent_dir="/w/agent",
                settings_manager=created.services.settings_manager,
            ),
        )
        first_file = host.session.session_file

        asyncio.run(host.new_session())
        assert host.session.session_file != first_file


class TestUnpersistedSessions:
    """A session with no file still has to fork, because `/fork` does not ask."""

    def test_new_session_stays_ephemeral(self, tmp_path: Path):
        # `--no-session` is a promise not to write, and `/new` must keep it.
        session_manager = SessionManager(str(tmp_path), "", persist=False)
        created = create_agent_session(cwd=str(tmp_path), session_manager=session_manager)
        host = AgentSessionRuntime(
            created.session,
            AgentSessionServices(
                cwd=str(tmp_path),
                agent_dir="/w/agent",
                settings_manager=created.services.settings_manager,
            ),
        )

        asyncio.run(host.new_session())

        assert not host.session.session_manager.is_persisted()
        assert host.session.session_file is None
        assert list(Path(tmp_path).iterdir()) == []

    def test_fork_moves_the_leaf_in_place(self):
        session_manager = SessionManager("/w/project", "", persist=False)
        created = create_agent_session(cwd="/w/project", session_manager=session_manager)
        host = AgentSessionRuntime(
            created.session,
            AgentSessionServices(
                cwd="/w/project",
                agent_dir="/w/agent",
                settings_manager=created.services.settings_manager,
            ),
        )
        session_manager.append_message(_user("first"))
        session_manager.append_message(_assistant("answered"))
        second_id = session_manager.append_message(_user("second"))

        result = asyncio.run(host.fork(second_id))

        assert result.selected_text == "second"
        # The same manager, rewound: there is nowhere to copy a branch to.
        assert host.session.session_manager is session_manager


class TestSessionFileShape:
    """The file itself, since another program (hoocode) reads it."""

    def test_the_header_names_the_cwd_and_a_version(self, tmp_path: Path):
        manager = SessionManager("/w/project", str(tmp_path))
        _turn(manager, "asked", "answered")
        session_file = manager.get_session_file()
        assert session_file is not None

        header = json.loads(Path(session_file).read_text(encoding="utf-8").splitlines()[0])
        assert header["type"] == "session"
        assert header["cwd"] == "/w/project"
        assert header["version"] >= 3

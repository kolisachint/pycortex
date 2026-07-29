"""Tests for the footer's data provider.

Port of `test/footer-data-provider.test.ts`. The TS mocks `child_process` and
asserts on which of `spawnSync`/`execFile` ran; here the two module-level
resolvers stand in for that pair — the first blocking read of the branch goes
through one, every watcher-driven refresh through the other, and which one ran is
the difference between "the footer stalled the render loop" and "it didn't".

The timings are the provider's own class attributes, turned down so the suite
does not spend a second per debounce. What they must stay is *ordered*: the poll
interval below the debounce window, so a burst of writes still coalesces.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from cortex.code.interactive import footer_data_provider as fdp
from cortex.code.interactive.footer_data_provider import FooterDataProvider, find_git_paths


@pytest.fixture(autouse=True)
def _fast_watchers(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setattr(FooterDataProvider, "WATCH_POLL_INTERVAL_MS", 10)
    monkeypatch.setattr(FooterDataProvider, "WATCH_DEBOUNCE_MS", 30)
    monkeypatch.setattr(fdp, "FS_WATCH_RETRY_DELAY_MS", 50)
    yield


class _GitStub:
    """Stands in for the two `git symbolic-ref` calls, counting each separately."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, branch: str | None = "main") -> None:
        self.branch = branch
        self.sync_calls = 0
        self.off_thread_calls = 0
        monkeypatch.setattr(fdp, "_resolve_branch_with_git_sync", self._sync)
        monkeypatch.setattr(fdp, "_resolve_branch_with_git_off_thread", self._off_thread)

    def _sync(self, _repo_dir: str) -> str | None:
        self.sync_calls += 1
        return self.branch

    def _off_thread(self, _repo_dir: str) -> str | None:
        self.off_thread_calls += 1
        return self.branch


def _head_watcher(provider: FooterDataProvider) -> Any:
    """The watcher on HEAD's directory — the one the retry path rebuilds."""
    return provider._head_watcher  # pyright: ignore[reportPrivateUsage]


def _wait_for(condition: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        time.sleep(0.005)


def _plain_repo(tmp_path: Path, head: str = "ref: refs/heads/main\n") -> str:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text(head)
    return str(repo)


def _reftable_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    (repo / ".git" / "reftable").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/.invalid\n")
    return str(repo)


def _reftable_worktree(tmp_path: Path) -> tuple[str, str]:
    """A linked worktree whose refs live in the main repo's reftable directory."""
    common_git_dir = tmp_path / "repo" / ".git"
    git_dir = common_git_dir / "worktrees" / "src"
    worktree = tmp_path / "worktree"
    reftable = common_git_dir / "reftable"

    git_dir.mkdir(parents=True)
    reftable.mkdir(parents=True)
    worktree.mkdir(parents=True)

    (worktree / ".git").write_text(f"gitdir: {git_dir}\n")
    (git_dir / "HEAD").write_text("ref: refs/heads/.invalid\n")
    (git_dir / "commondir").write_text("../..\n")
    (reftable / "tables.list").write_text("0\n")
    return str(worktree), str(reftable)


class TestBranchDetection:
    def test_head_is_read_directly_from_a_nested_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        git = _GitStub(monkeypatch)
        repo = _plain_repo(tmp_path)
        nested = os.path.join(repo, "src", "nested")
        os.makedirs(nested)

        provider = FooterDataProvider(nested)
        try:
            assert provider.get_git_branch() == "main"
            # The common case never shells out: HEAD names the branch.
            assert git.sync_calls == 0
        finally:
            provider.dispose()

    def test_a_directory_outside_any_repo_has_no_branch(self, tmp_path: Path):
        provider = FooterDataProvider(str(tmp_path))
        try:
            assert provider.get_git_branch() is None
        finally:
            provider.dispose()

    def test_a_detached_head_says_so(self, tmp_path: Path):
        repo = _plain_repo(tmp_path, head="9f1c0de\n")
        provider = FooterDataProvider(repo)
        try:
            assert provider.get_git_branch() == "detached"
        finally:
            provider.dispose()

    def test_an_invalid_reftable_head_is_resolved_with_git(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        git = _GitStub(monkeypatch)
        provider = FooterDataProvider(_reftable_repo(tmp_path))
        try:
            assert provider.get_git_branch() == "main"
            assert git.sync_calls == 1
        finally:
            provider.dispose()

    def test_a_reftable_backed_worktree_resolves_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _GitStub(monkeypatch)
        worktree, _ = _reftable_worktree(tmp_path)
        provider = FooterDataProvider(worktree)
        try:
            assert provider.get_git_branch() == "main"
        finally:
            provider.dispose()

    def test_an_unresolvable_invalid_head_is_treated_as_detached(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _GitStub(monkeypatch, branch=None)
        provider = FooterDataProvider(_reftable_repo(tmp_path))
        try:
            assert provider.get_git_branch() == "detached"
        finally:
            provider.dispose()

    def test_the_branch_is_read_once_and_cached(self, tmp_path: Path):
        repo = _plain_repo(tmp_path)
        provider = FooterDataProvider(repo)
        try:
            assert provider.get_git_branch() == "main"
            # The footer asks on every frame; a rewritten HEAD reaches it through
            # the watcher, not through a re-read per render.
            Path(repo, ".git", "HEAD").write_text("ref: refs/heads/other\n")
            assert provider.get_git_branch() == "main"
        finally:
            provider.dispose()


class TestWatching:
    def test_a_branch_switch_updates_the_cache_and_notifies(self, tmp_path: Path):
        repo = _plain_repo(tmp_path)
        provider = FooterDataProvider(repo)
        changes: list[int] = []
        try:
            assert provider.get_git_branch() == "main"
            provider.on_branch_change(lambda: changes.append(1))

            Path(repo, ".git", "HEAD").write_text("ref: refs/heads/topic\n")
            _wait_for(lambda: provider.get_git_branch() == "topic")
            assert changes == [1]
        finally:
            provider.dispose()

    def test_an_unsubscribed_listener_stops_hearing(self, tmp_path: Path):
        repo = _plain_repo(tmp_path)
        provider = FooterDataProvider(repo)
        changes: list[int] = []
        try:
            assert provider.get_git_branch() == "main"
            unsubscribe = provider.on_branch_change(lambda: changes.append(1))
            unsubscribe()

            Path(repo, ".git", "HEAD").write_text("ref: refs/heads/topic\n")
            _wait_for(lambda: provider.get_git_branch() == "topic")
            assert changes == []
        finally:
            provider.dispose()

    def test_reftable_updates_that_keep_the_branch_notify_nobody(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        git = _GitStub(monkeypatch)
        worktree, reftable = _reftable_worktree(tmp_path)
        provider = FooterDataProvider(worktree)
        changes: list[int] = []
        try:
            assert provider.get_git_branch() == "main"
            git.sync_calls = 0
            provider.on_branch_change(lambda: changes.append(1))

            Path(reftable, "tables.list").write_text("1\n")
            _wait_for(lambda: git.off_thread_calls == 1)
            time.sleep(0.1)

            assert git.off_thread_calls == 1
            # The refresh went the off-thread route: the render loop was never
            # made to wait on a subprocess.
            assert git.sync_calls == 0
            assert provider.get_git_branch() == "main"
            assert changes == []
        finally:
            provider.dispose()

    def test_a_reftable_branch_switch_is_picked_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        git = _GitStub(monkeypatch)
        worktree, reftable = _reftable_worktree(tmp_path)
        provider = FooterDataProvider(worktree)
        changes: list[int] = []
        try:
            assert provider.get_git_branch() == "main"
            git.branch = "foo"
            provider.on_branch_change(lambda: changes.append(1))

            Path(reftable, "tables.list").write_text("1\n")
            _wait_for(lambda: provider.get_git_branch() == "foo")
            assert changes == [1]
        finally:
            provider.dispose()

    def test_a_burst_of_updates_collapses_into_one_refresh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        git = _GitStub(monkeypatch)
        worktree, reftable = _reftable_worktree(tmp_path)
        provider = FooterDataProvider(worktree)
        try:
            assert provider.get_git_branch() == "main"
            git.off_thread_calls = 0

            for value in ("1\n", "2\n", "3\n"):
                Path(reftable, "tables.list").write_text(value)

            _wait_for(lambda: git.off_thread_calls == 1)
            time.sleep(0.2)
            assert git.off_thread_calls == 1
        finally:
            provider.dispose()

    def test_watchers_are_rebuilt_after_the_watch_dies(self, tmp_path: Path):
        repo = _plain_repo(tmp_path)
        provider = FooterDataProvider(repo)
        try:
            original = _head_watcher(provider)
            assert original is not None

            # Losing the watched directory is `fs.watch`'s `error` event.
            os.remove(os.path.join(repo, ".git", "HEAD"))
            os.rmdir(os.path.join(repo, ".git"))
            _wait_for(lambda: _head_watcher(provider) is None)

            # It comes back when the directory does, without anyone asking.
            os.makedirs(os.path.join(repo, ".git"))
            Path(repo, ".git", "HEAD").write_text("ref: refs/heads/main\n")
            _wait_for(lambda: _head_watcher(provider) is not None)
            assert _head_watcher(provider) is not original
        finally:
            provider.dispose()

    def test_dispose_stops_the_watcher(self, tmp_path: Path):
        repo = _plain_repo(tmp_path)
        provider = FooterDataProvider(repo)
        assert provider.get_git_branch() == "main"
        watcher = _head_watcher(provider)
        assert watcher is not None
        provider.dispose()
        assert watcher.closed

        # And a change after disposal reaches nobody.
        changes: list[int] = []
        provider.on_branch_change(lambda: changes.append(1))
        Path(repo, ".git", "HEAD").write_text("ref: refs/heads/topic\n")
        time.sleep(0.15)
        assert changes == []


class TestCwd:
    def test_moving_to_another_repo_re_reads_the_branch(self, tmp_path: Path):
        first = _plain_repo(tmp_path / "a")
        second = _plain_repo(tmp_path / "b", head="ref: refs/heads/second\n")
        provider = FooterDataProvider(first)
        changes: list[int] = []
        try:
            assert provider.get_git_branch() == "main"
            provider.on_branch_change(lambda: changes.append(1))
            provider.set_cwd(second)
            assert provider.get_git_branch() == "second"
            assert changes == [1]
        finally:
            provider.dispose()

    def test_setting_the_same_cwd_changes_nothing(self, tmp_path: Path):
        repo = _plain_repo(tmp_path)
        provider = FooterDataProvider(repo)
        changes: list[int] = []
        try:
            provider.on_branch_change(lambda: changes.append(1))
            provider.set_cwd(repo)
            assert changes == []
        finally:
            provider.dispose()


class TestExtensionData:
    def test_statuses_are_set_replaced_and_cleared(self, tmp_path: Path):
        provider = FooterDataProvider(str(tmp_path))
        try:
            provider.set_extension_status("a", "first")
            provider.set_extension_status("b", "second")
            assert dict(provider.get_extension_statuses()) == {"a": "first", "b": "second"}

            provider.set_extension_status("a", "replaced")
            assert provider.get_extension_statuses()["a"] == "replaced"

            # `None` is how an extension takes its status back down.
            provider.set_extension_status("a", None)
            assert "a" not in provider.get_extension_statuses()

            provider.clear_extension_statuses()
            assert dict(provider.get_extension_statuses()) == {}
        finally:
            provider.dispose()

    def test_mode_provider_count_and_subagent_flag_round_trip(self, tmp_path: Path):
        provider = FooterDataProvider(str(tmp_path))
        try:
            assert provider.get_active_mode() == "build"
            assert provider.get_available_provider_count() == 0
            assert provider.get_subagent_enabled() is False

            provider.set_active_mode("plan")
            provider.set_available_provider_count(3)
            provider.set_subagent_enabled(True)

            assert provider.get_active_mode() == "plan"
            assert provider.get_available_provider_count() == 3
            assert provider.get_subagent_enabled() is True
        finally:
            provider.dispose()


class TestFindGitPaths:
    def test_a_worktree_resolves_its_common_directory(self, tmp_path: Path):
        worktree, _ = _reftable_worktree(tmp_path)
        paths = find_git_paths(worktree)
        assert paths is not None
        assert paths.repo_dir == worktree
        assert paths.head_path == str(tmp_path / "repo" / ".git" / "worktrees" / "src" / "HEAD")
        # `commondir` points back at the main repo, which is where reftable lives.
        assert paths.common_git_dir == str(tmp_path / "repo" / ".git")

    def test_a_gitdir_pointer_with_no_head_is_not_a_repo(self, tmp_path: Path):
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {tmp_path / 'nowhere'}\n")
        assert find_git_paths(str(worktree)) is None

    def test_nothing_above_the_root_is_searched_forever(self, tmp_path: Path):
        assert find_git_paths(str(tmp_path)) is None

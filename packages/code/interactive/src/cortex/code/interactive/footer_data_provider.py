"""What the footer knows that the session does not.

Port of ``core/footer-data-provider.ts``.

Token counts, the model and the cwd all come off the session; the git branch and
the extension status texts do not, and neither does the active mode. This is
where they live — with the branch *cached*, because the footer re-reads it on
every frame and shelling out to git per frame would be absurd, and *watched*, so
switching branches in another terminal updates the line without a keystroke.

**Threads, where the TS has an event loop.** ``fs.watch`` and ``setTimeout`` both
land on node's loop, so every callback there is already on the UI thread. Here
the watcher (see :mod:`cortex.code.interactive.fs_watch`) and the debounce timer
are threads, so ``on_branch_change`` subscribers are invoked *off* the UI thread
— the interactive mode hops back with ``call_soon_threadsafe`` before touching
the TUI, the same way the bash executor does.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from cortex.code.interactive.fs_watch import (
    FS_WATCH_RETRY_DELAY_MS,
    POLL_INTERVAL_MS,
    PathWatcher,
    close_watcher,
    watch_with_error_handler,
)

__all__ = [
    "FooterDataProvider",
    "GitPaths",
    "ReadonlyFooterDataProvider",
    "find_git_paths",
]

_GIT_BRANCH_ARGV = ["git", "--no-optional-locks", "symbolic-ref", "--quiet", "--short", "HEAD"]


@dataclass(frozen=True)
class GitPaths:
    repo_dir: str
    common_git_dir: str
    head_path: str


def find_git_paths(cwd: str) -> GitPaths | None:
    """Find git metadata paths by walking up from ``cwd``.

    Handles both regular repos (``.git`` is a directory) and worktrees (``.git``
    is a file holding a ``gitdir:`` pointer).
    """
    directory = cwd
    while True:
        git_path = os.path.join(directory, ".git")
        if os.path.exists(git_path):
            try:
                if os.path.isfile(git_path):
                    with open(git_path, encoding="utf8") as handle:
                        content = handle.read().strip()
                    if content.startswith("gitdir: "):
                        git_dir = os.path.abspath(os.path.join(directory, content[8:].strip()))
                        head_path = os.path.join(git_dir, "HEAD")
                        if not os.path.exists(head_path):
                            return None
                        common_dir_path = os.path.join(git_dir, "commondir")
                        if os.path.exists(common_dir_path):
                            with open(common_dir_path, encoding="utf8") as handle:
                                common_git_dir = os.path.abspath(
                                    os.path.join(git_dir, handle.read().strip())
                                )
                        else:
                            common_git_dir = git_dir
                        return GitPaths(
                            repo_dir=directory, common_git_dir=common_git_dir, head_path=head_path
                        )
                elif os.path.isdir(git_path):
                    head_path = os.path.join(git_path, "HEAD")
                    if not os.path.exists(head_path):
                        return None
                    return GitPaths(
                        repo_dir=directory, common_git_dir=git_path, head_path=head_path
                    )
            except OSError:
                return None
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def _resolve_branch_with_git_sync(repo_dir: str) -> str | None:
    """Ask git for the current branch. ``None`` on detached HEAD or no git."""
    try:
        result = subprocess.run(  # noqa: S603
            _GIT_BRANCH_ARGV,
            cwd=repo_dir,
            capture_output=True,
            encoding="utf8",
            check=False,
        )
    except OSError:
        return None
    branch = result.stdout.strip() if result.returncode == 0 else ""
    return branch or None


def _resolve_branch_with_git_off_thread(repo_dir: str) -> str | None:
    """The same question, asked from the watcher's thread.

    The TS splits these two the same way — ``spawnSync`` for the first, blocking
    read of the branch and ``execFile`` for every refresh after it — so that a
    branch switch never stalls the render loop. Here the refresh is already off
    the UI thread; the split is kept because *which* of the two ran is the thing
    the provider's tests hold it to.
    """
    return _resolve_branch_with_git_sync(repo_dir)


class ReadonlyFooterDataProvider(Protocol):
    """Read-only view for extensions and the footer.

    Excludes ``set_extension_status``, ``set_available_provider_count`` and
    ``dispose``.
    """

    def get_git_branch(self) -> str | None: ...

    def get_extension_statuses(self) -> Mapping[str, str]: ...

    def get_available_provider_count(self) -> int: ...

    def on_branch_change(self, callback: Callable[[], None]) -> Callable[[], None]: ...

    def get_active_mode(self) -> str: ...

    def get_subagent_enabled(self) -> bool: ...


class FooterDataProvider:
    """Git branch and extension statuses — data not otherwise reachable.

    Token stats and model info are available through the session.
    """

    #: How long a burst of filesystem events is collapsed into one git call.
    WATCH_DEBOUNCE_MS = 500
    #: How often the watchers sample; see :mod:`cortex.code.interactive.fs_watch`.
    WATCH_POLL_INTERVAL_MS = POLL_INTERVAL_MS

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd
        self._extension_statuses: dict[str, str] = {}
        #: ``None`` is a real answer ("not a repo"); *unset* is "never asked".
        self._cached_branch: str | None = None
        self._branch_cached = False
        self._git_paths: GitPaths | None = find_git_paths(cwd)
        self._head_watcher: PathWatcher | None = None
        self._reftable_watcher: PathWatcher | None = None
        self._reftable_tables_list_watcher: PathWatcher | None = None
        self._branch_change_callbacks: list[Callable[[], None]] = []
        self._available_provider_count = 0
        self._refresh_timer: threading.Timer | None = None
        self._git_watcher_retry_timer: threading.Timer | None = None
        self._refresh_in_flight = False
        self._refresh_pending = False
        self._disposed = False
        self._active_mode = "build"
        self._subagent_enabled = False
        self._lock = threading.RLock()
        self._setup_git_watcher()

    # ------------------------------------------------------------------
    # Read-only surface
    # ------------------------------------------------------------------

    def get_git_branch(self) -> str | None:
        """Current git branch, ``None`` outside a repo, ``"detached"`` on a detached HEAD."""
        with self._lock:
            if not self._branch_cached:
                self._cached_branch = self._resolve_git_branch_sync()
                self._branch_cached = True
            return self._cached_branch

    def get_extension_statuses(self) -> Mapping[str, str]:
        """Extension status texts set via ``ctx.ui.set_status()``."""
        return self._extension_statuses

    def on_branch_change(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to git branch changes. Returns the unsubscribe."""
        self._branch_change_callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._branch_change_callbacks:
                self._branch_change_callbacks.remove(callback)

        return unsubscribe

    def get_available_provider_count(self) -> int:
        """Number of unique providers with available models (for footer display)."""
        return self._available_provider_count

    def get_active_mode(self) -> str:
        """Current active mode (e.g. ask, plan, build, debug)."""
        return self._active_mode

    def get_subagent_enabled(self) -> bool:
        """Whether the subagent system prompt is active."""
        return self._subagent_enabled

    # ------------------------------------------------------------------
    # Internal setters — not part of the read-only view
    # ------------------------------------------------------------------

    def set_extension_status(self, key: str, text: str | None) -> None:
        if text is None:
            self._extension_statuses.pop(key, None)
        else:
            self._extension_statuses[key] = text

    def clear_extension_statuses(self) -> None:
        self._extension_statuses.clear()

    def set_available_provider_count(self, count: int) -> None:
        self._available_provider_count = count

    def set_active_mode(self, mode: str) -> None:
        self._active_mode = mode

    def set_subagent_enabled(self, enabled: bool) -> None:
        self._subagent_enabled = enabled

    def set_cwd(self, cwd: str) -> None:
        if self._cwd == cwd:
            return

        self._cwd = cwd
        with self._lock:
            if self._refresh_timer is not None:
                self._refresh_timer.cancel()
                self._refresh_timer = None
            self._clear_git_watchers()
            self._cached_branch = None
            self._branch_cached = False
            self._git_paths = find_git_paths(cwd)
        self._setup_git_watcher()
        self._notify_branch_change()

    def dispose(self) -> None:
        with self._lock:
            self._disposed = True
            if self._refresh_timer is not None:
                self._refresh_timer.cancel()
                self._refresh_timer = None
            self._clear_git_watchers()
        self._branch_change_callbacks.clear()

    # ------------------------------------------------------------------
    # Branch resolution
    # ------------------------------------------------------------------

    def _notify_branch_change(self) -> None:
        for callback in list(self._branch_change_callbacks):
            callback()

    def _schedule_refresh(self) -> None:
        with self._lock:
            if self._disposed or self._refresh_timer is not None:
                return
            if self._refresh_in_flight:
                self._refresh_pending = True
                return
            timer = threading.Timer(self.WATCH_DEBOUNCE_MS / 1000, self._on_refresh_timer)
            timer.daemon = True
            self._refresh_timer = timer
        timer.start()

    def _on_refresh_timer(self) -> None:
        with self._lock:
            self._refresh_timer = None
        self._refresh_git_branch()

    def _refresh_git_branch(self) -> None:
        """Re-read the branch off the watcher's thread and announce a change."""
        with self._lock:
            if self._disposed:
                return
            if self._refresh_in_flight:
                self._refresh_pending = True
                return
            self._refresh_in_flight = True

        try:
            next_branch = self._resolve_git_branch_off_thread()
            with self._lock:
                if self._disposed:
                    return
                changed = self._branch_cached and self._cached_branch != next_branch
                self._cached_branch = next_branch
                self._branch_cached = True
            if changed:
                self._notify_branch_change()
                return
        finally:
            with self._lock:
                self._refresh_in_flight = False
                pending = self._refresh_pending and not self._disposed
                if pending:
                    self._refresh_pending = False
            if pending:
                self._schedule_refresh()

    def _read_head(self) -> str | None:
        """The branch name HEAD names directly, or a sentinel this cannot answer."""
        paths = self._git_paths
        if paths is None:
            return None
        try:
            with open(paths.head_path, encoding="utf8") as handle:
                return handle.read().strip()
        except OSError:
            return None

    def _resolve_git_branch_sync(self) -> str | None:
        paths = self._git_paths
        content = self._read_head()
        if paths is None or content is None:
            return None
        if content.startswith("ref: refs/heads/"):
            branch = content[16:]
            if branch == ".invalid":
                return _resolve_branch_with_git_sync(paths.repo_dir) or "detached"
            return branch
        return "detached"

    def _resolve_git_branch_off_thread(self) -> str | None:
        paths = self._git_paths
        content = self._read_head()
        if paths is None or content is None:
            return None
        if content.startswith("ref: refs/heads/"):
            branch = content[16:]
            if branch == ".invalid":
                return _resolve_branch_with_git_off_thread(paths.repo_dir) or "detached"
            return branch
        return "detached"

    # ------------------------------------------------------------------
    # Watchers
    # ------------------------------------------------------------------

    def _clear_git_watchers(self) -> None:
        close_watcher(self._head_watcher)
        self._head_watcher = None
        close_watcher(self._reftable_watcher)
        self._reftable_watcher = None
        close_watcher(self._reftable_tables_list_watcher)
        self._reftable_tables_list_watcher = None
        if self._git_watcher_retry_timer is not None:
            self._git_watcher_retry_timer.cancel()
            self._git_watcher_retry_timer = None

    def _schedule_git_watcher_retry(self) -> None:
        with self._lock:
            if self._disposed or self._git_watcher_retry_timer is not None:
                return
            timer = threading.Timer(FS_WATCH_RETRY_DELAY_MS / 1000, self._on_git_watcher_retry)
            timer.daemon = True
            self._git_watcher_retry_timer = timer
        timer.start()

    def _on_git_watcher_retry(self) -> None:
        with self._lock:
            self._git_watcher_retry_timer = None
        self._setup_git_watcher()

    def _handle_git_watcher_error(self) -> None:
        with self._lock:
            self._clear_git_watchers()
        self._schedule_git_watcher_retry()

    def _setup_git_watcher(self) -> None:
        with self._lock:
            self._clear_git_watchers()
            paths = self._git_paths
            if paths is None or self._disposed:
                return
            interval = self.WATCH_POLL_INTERVAL_MS

            # Watch the directory containing HEAD, not HEAD itself: git writes
            # atomically (write temp, rename over HEAD), which changes the inode.
            self._head_watcher = watch_with_error_handler(
                os.path.dirname(paths.head_path),
                lambda _event, filename: (
                    self._schedule_refresh() if not filename or filename == "HEAD" else None
                ),
                self._handle_git_watcher_error,
                interval,
            )
            if self._head_watcher is None:
                return

            # In reftable repos, branch switches update files in the reftable
            # directory instead of HEAD. Watch it separately so the footer picks
            # up those changes.
            reftable_dir = os.path.join(paths.common_git_dir, "reftable")
            if not os.path.exists(reftable_dir):
                return
            self._reftable_watcher = watch_with_error_handler(
                reftable_dir,
                lambda _event, _filename: self._schedule_refresh(),
                self._handle_git_watcher_error,
                interval,
            )
            if self._reftable_watcher is None:
                return

            tables_list_path = os.path.join(reftable_dir, "tables.list")
            if not os.path.exists(tables_list_path):
                return
            # The TS watches this file twice — `fs.watch` for the event and
            # `watchFile` at a 250 ms interval for the platforms where the event
            # never arrives. Both are polls here, so the second collapses into
            # the first.
            self._reftable_tables_list_watcher = watch_with_error_handler(
                tables_list_path,
                lambda _event, _filename: self._schedule_refresh(),
                self._handle_git_watcher_error,
                interval,
            )

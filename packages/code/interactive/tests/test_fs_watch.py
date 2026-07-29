"""Tests for the polling path watcher.

`utils/fs-watch.ts` is three lines of node glue around `fs.watch`; the port is
the watch itself, so it is the part that needs holding to the contract: report a
change under the watched path, survive the atomic rename git does to HEAD, and
hand the caller an error rather than dying quietly when the path goes away.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from cortex.code.interactive.fs_watch import PathWatcher, close_watcher, watch_with_error_handler

INTERVAL_MS = 10


def _wait_for(condition: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        time.sleep(0.005)


class TestPathWatcher:
    def test_a_changed_file_is_reported_by_name(self, tmp_path: Path):
        (tmp_path / "HEAD").write_text("one\n")
        seen: list[str | None] = []
        watcher = PathWatcher(
            str(tmp_path), lambda _event, name: seen.append(name), lambda: None, INTERVAL_MS
        )
        try:
            (tmp_path / "HEAD").write_text("two\n")
            _wait_for(lambda: "HEAD" in seen)
        finally:
            watcher.close()

    def test_a_created_file_is_reported(self, tmp_path: Path):
        seen: list[str | None] = []
        watcher = PathWatcher(
            str(tmp_path), lambda _event, name: seen.append(name), lambda: None, INTERVAL_MS
        )
        try:
            (tmp_path / "new").write_text("x")
            _wait_for(lambda: "new" in seen)
        finally:
            watcher.close()

    def test_an_atomic_rename_over_the_target_is_seen(self, tmp_path: Path):
        (tmp_path / "HEAD").write_text("one\n")
        seen: list[str | None] = []
        watcher = PathWatcher(
            str(tmp_path), lambda _event, name: seen.append(name), lambda: None, INTERVAL_MS
        )
        try:
            # What git does: write a temp file, rename it over HEAD. The inode
            # changes, which is why the directory is watched rather than the file.
            (tmp_path / "HEAD.lock").write_text("two\n")
            os.replace(tmp_path / "HEAD.lock", tmp_path / "HEAD")
            _wait_for(lambda: "HEAD" in seen)
        finally:
            watcher.close()

    def test_a_rename_with_the_same_size_and_mtime_is_still_seen(self, tmp_path: Path):
        head = tmp_path / "HEAD"
        head.write_text("one\n")
        stat = os.stat(head)
        seen: list[str | None] = []
        watcher = PathWatcher(
            str(tmp_path), lambda _event, name: seen.append(name), lambda: None, INTERVAL_MS
        )
        try:
            # A git write that lands inside the filesystem's timestamp
            # granularity looks identical by mtime and size. The inode is the
            # only thing that moved, and it is why the stamp carries one.
            replacement = tmp_path / "HEAD.lock"
            replacement.write_text("two\n")
            os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            os.replace(replacement, head)
            _wait_for(lambda: "HEAD" in seen)
        finally:
            watcher.close()

    def test_a_watched_file_reports_under_its_own_name(self, tmp_path: Path):
        target = tmp_path / "tables.list"
        target.write_text("0\n")
        seen: list[str | None] = []
        watcher = PathWatcher(
            str(target), lambda _event, name: seen.append(name), lambda: None, INTERVAL_MS
        )
        try:
            target.write_text("1\n")
            _wait_for(lambda: seen == ["tables.list"])
        finally:
            watcher.close()

    def test_an_unchanged_path_reports_nothing(self, tmp_path: Path):
        (tmp_path / "HEAD").write_text("one\n")
        seen: list[str | None] = []
        watcher = PathWatcher(
            str(tmp_path), lambda _event, name: seen.append(name), lambda: None, INTERVAL_MS
        )
        try:
            time.sleep(0.1)
            assert seen == []
        finally:
            watcher.close()

    def test_losing_the_path_calls_the_error_hook(self, tmp_path: Path):
        target = tmp_path / "watched"
        target.mkdir()
        errors: list[int] = []
        watcher = PathWatcher(
            str(target), lambda _event, _name: None, lambda: errors.append(1), INTERVAL_MS
        )
        try:
            target.rmdir()
            _wait_for(lambda: errors == [1])
            # One error, then it stops: a dead watch does not spin.
            time.sleep(0.1)
            assert errors == [1]
        finally:
            watcher.close()

    def test_closing_stops_the_listener(self, tmp_path: Path):
        (tmp_path / "HEAD").write_text("one\n")
        seen: list[str | None] = []
        watcher = PathWatcher(
            str(tmp_path), lambda _event, name: seen.append(name), lambda: None, INTERVAL_MS
        )
        watcher.close()
        assert watcher.closed
        (tmp_path / "HEAD").write_text("two\n")
        time.sleep(0.1)
        assert seen == []


class TestHelpers:
    def test_a_path_that_cannot_be_watched_reports_and_returns_none(self, tmp_path: Path):
        errors: list[int] = []
        watcher = watch_with_error_handler(
            str(tmp_path / "absent"),
            lambda _event, _name: None,
            lambda: errors.append(1),
            INTERVAL_MS,
        )
        assert watcher is None
        assert errors == [1]

    def test_closing_nothing_is_not_an_error(self):
        close_watcher(None)

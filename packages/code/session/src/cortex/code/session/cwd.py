"""Session cwd validation utilities.

Port of ``session-cwd.ts`` from ``packages/coding-agent/src/core/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SessionCwdIssue:
    """Issue when session working directory is missing."""

    session_cwd: str
    fallback_cwd: str
    session_file: str | None = None


class MissingSessionCwdError(Exception):
    """Error raised when session working directory doesn't exist."""

    def __init__(self, issue: SessionCwdIssue) -> None:
        self.issue = issue
        super().__init__(format_missing_session_cwd_error(issue))


def get_missing_session_cwd_issue(
    session_cwd: str | None,
    session_file: str | None,
    fallback_cwd: str,
) -> SessionCwdIssue | None:
    """Check if session working directory is missing."""
    if not session_file:
        return None

    if not session_cwd or Path(session_cwd).exists():
        return None

    return SessionCwdIssue(
        session_cwd=session_cwd,
        fallback_cwd=fallback_cwd,
        session_file=session_file,
    )


def format_missing_session_cwd_error(issue: SessionCwdIssue) -> str:
    """Format error message for missing session cwd."""
    session_file = f"\nSession file: {issue.session_file}" if issue.session_file else ""
    return (
        f"Stored session working directory does not exist: {issue.session_cwd}"
        f"{session_file}\nCurrent working directory: {issue.fallback_cwd}"
    )


def format_missing_session_cwd_prompt(issue: SessionCwdIssue) -> str:
    """Format prompt message for missing session cwd."""
    return (
        f"cwd from session file does not exist\n{issue.session_cwd}\n\n"
        f"continue in current cwd\n{issue.fallback_cwd}"
    )


def assert_session_cwd_exists(
    session_cwd: str | None,
    session_file: str | None,
    fallback_cwd: str,
) -> None:
    """Assert that session working directory exists.

    Raises:
        MissingSessionCwdError: If session cwd is missing.
    """
    issue = get_missing_session_cwd_issue(session_cwd, session_file, fallback_cwd)
    if issue:
        raise MissingSessionCwdError(issue)

"""Tests for the login dialog component (step 7.11).

The dialog is a *content area an OAuth flow writes into*, and the rule that makes
it work is which steps clear it and which append: the URL from ``show_auth`` has
to survive the prompt that asks for the code you got by visiting it.

Written after mutation testing found that nothing covered the append/clear split —
`test_login_controller.py` drives one prompt on its own, where clearing and
appending look identical.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from cortex.code.interactive.components.login_dialog import (
    LOGIN_CANCELLED,
    LoginDialogComponent,
)
from cortex.code.interactive.keybindings import KeybindingsManager
from cortex.tui.keys import set_keybindings

ENTER = "\r"
ESCAPE = "\x1b"

AUTH_URL = "https://provider.example.com/authorize?code=abc"


@pytest.fixture(autouse=True)
def _app_keybindings() -> None:  # pyright: ignore[reportUnusedFunction]
    set_keybindings(KeybindingsManager())


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """`show_auth` tries to open a browser; a test must not launch one."""
    import cortex.code.interactive.components.login_dialog as module

    def no_browser(_url: str) -> None:
        return None

    monkeypatch.setattr(module, "_open_in_browser", no_browser)


class FakeUI:
    def __init__(self) -> None:
        self.renders = 0

    def request_render(self) -> None:
        self.renders += 1


def _dialog(provider_id: str = "anthropic", **kwargs: Any) -> LoginDialogComponent:
    from typing import cast

    return LoginDialogComponent(
        cast(Any, FakeUI()), provider_id, lambda _success, _message: None, **kwargs
    )


def _text(component: Any, width: int = 120) -> str:
    return "\n".join(re.sub(r"\x1b\[[0-9;]*m", "", line) for line in component.render(width))


class TestTheCancelSentinel:
    def test_it_is_the_constant_the_oauth_providers_raise(self):
        """One constant, not three literals. The dialog raises it, the providers
        in ``cortex.ai.oauth`` raise it, and ``login_controller`` compares — a
        drifted copy would report a deliberate cancel as an error."""
        from cortex.ai.oauth import LOGIN_CANCELLED as oauth_sentinel

        assert LOGIN_CANCELLED is oauth_sentinel
        assert LOGIN_CANCELLED == "Login cancelled"


class TestTitle:
    def test_the_title_names_the_provider(self):
        assert "Login to Anthropic (Claude Pro/Max)" in _text(_dialog("anthropic"))

    def test_an_override_wins_over_the_providers_name(self):
        assert "Login to My Name" in _text(_dialog("anthropic", provider_name_override="My Name"))

    def test_an_unknown_provider_falls_back_to_its_id(self):
        assert "Login to some-unknown-provider" in _text(_dialog("some-unknown-provider"))

    def test_a_title_override_replaces_the_whole_line(self):
        assert "Set up Bedrock" in _text(_dialog("anthropic", title_override="Set up Bedrock"))


class TestContentAreaRules:
    """Which steps clear the content, and which append to it."""

    async def test_a_prompt_keeps_the_url_above_it(self):
        """The whole reason `show_prompt` appends: you cannot read the code off a
        page whose address has been wiped off the screen."""
        dialog = _dialog()
        dialog.show_auth(AUTH_URL)
        assert AUTH_URL in _text(dialog)

        future = dialog.show_prompt("Enter the code:")
        output = _text(dialog)
        assert AUTH_URL in output
        assert "Enter the code:" in output

        dialog.cancel()
        with pytest.raises(RuntimeError):
            await future

    async def test_progress_lines_accumulate_under_the_url(self):
        dialog = _dialog()
        dialog.show_auth(AUTH_URL)
        dialog.show_progress("Exchanging code...")
        dialog.show_progress("Fetching account...")

        output = _text(dialog)
        assert AUTH_URL in output
        assert "Exchanging code..." in output
        assert "Fetching account..." in output

    async def test_waiting_keeps_the_url_too(self):
        dialog = _dialog()
        dialog.show_auth(AUTH_URL)
        dialog.show_waiting("Waiting for browser authentication...")

        output = _text(dialog)
        assert AUTH_URL in output
        assert "Waiting for browser authentication..." in output

    def test_a_second_auth_step_replaces_the_first(self):
        """`show_auth` is the one step that clears: a re-issued URL means the
        first one is stale, and showing both would be showing a wrong one."""
        dialog = _dialog()
        dialog.show_auth(AUTH_URL)
        dialog.show_auth("https://provider.example.com/second")

        output = _text(dialog)
        assert AUTH_URL not in output
        assert "https://provider.example.com/second" in output

    def test_show_info_clears_and_stands_alone(self):
        dialog = _dialog()
        dialog.show_auth(AUTH_URL)
        dialog.show_info(["Bedrock uses your AWS credentials.", "Nothing to enter here."])

        output = _text(dialog)
        assert AUTH_URL not in output
        assert "Bedrock uses your AWS credentials." in output

    def test_the_url_is_rendered_as_a_terminal_hyperlink(self):
        """A URL long enough to wrap is unusable as text, so it is emitted as an
        OSC-8 link with a click hint."""
        dialog = _dialog()
        dialog.show_auth(AUTH_URL)
        raw = "\n".join(dialog.render(120))

        assert f"\x1b]8;;{AUTH_URL}\x07" in raw
        assert "click to open" in _text(dialog)

    def test_instructions_are_shown_when_a_provider_sends_them(self):
        dialog = _dialog()
        dialog.show_auth(AUTH_URL, "Approve the request, then come back.")
        assert "Approve the request, then come back." in _text(dialog)


class TestPendingInput:
    async def test_submitting_resolves_the_pending_prompt(self):
        dialog = _dialog()
        future = dialog.show_prompt("Enter the code:")

        for char in "the-code":
            dialog.handle_input(char)
        dialog.handle_input(ENTER)

        assert await asyncio.wait_for(future, timeout=5) == "the-code"

    async def test_escape_fails_the_pending_prompt_with_the_sentinel(self):
        """The sentinel is what every caller compares against so that a
        deliberate cancel is not reported as an error."""
        dialog = _dialog()
        future = dialog.show_prompt("Enter the code:")

        dialog.handle_input(ESCAPE)

        with pytest.raises(RuntimeError, match=LOGIN_CANCELLED):
            await asyncio.wait_for(future, timeout=5)

    async def test_a_prompt_starts_from_an_empty_field(self):
        """Two prompts in one flow: the second must not arrive pre-filled with the
        answer to the first."""
        dialog = _dialog()
        first = dialog.show_prompt("First:")
        for char in "first-answer":
            dialog.handle_input(char)
        dialog.handle_input(ENTER)
        assert await asyncio.wait_for(first, timeout=5) == "first-answer"

        second = dialog.show_prompt("Second:")
        dialog.handle_input(ENTER)
        assert await asyncio.wait_for(second, timeout=5) == ""

    async def test_the_manual_input_box_resolves_the_same_way(self):
        dialog = _dialog()
        future = dialog.show_manual_input("Paste redirect URL:")

        for char in "https://cb/x":
            dialog.handle_input(char)
        dialog.handle_input(ENTER)

        assert await asyncio.wait_for(future, timeout=5) == "https://cb/x"

    async def test_cancelling_trips_the_abort_signal(self):
        """The provider's flow watches the signal, so a cancel has to reach it as
        well as failing the await."""
        dialog = _dialog()
        assert dialog.signal.aborted is False

        dialog.cancel()
        assert dialog.signal.aborted is True

    async def test_a_second_cancel_does_not_raise_on_a_settled_future(self):
        dialog = _dialog()
        future = dialog.show_prompt("Enter:")
        dialog.handle_input(ENTER)
        assert await asyncio.wait_for(future, timeout=5) == ""

        dialog.cancel()  # must not blow up on the already-resolved future

    def test_completion_is_reported_once_with_the_sentinel(self):
        from typing import cast

        seen: list[tuple[bool, str | None]] = []
        dialog = LoginDialogComponent(
            cast(Any, FakeUI()),
            "anthropic",
            lambda success, message: seen.append((success, message)),
        )
        dialog.cancel()
        assert seen == [(False, LOGIN_CANCELLED)]

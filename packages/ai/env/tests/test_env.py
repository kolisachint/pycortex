"""Port of hoocode's packages/ai/test/env-api-keys.test.ts."""

import pytest
from cortex.ai.env import find_env_keys, get_env_api_key

# github-copilot must NOT be auto-detected from ambient GitHub tokens
# (GH_TOKEN / GITHUB_TOKEN), which exist for repository access in CI and
# GitHub-integrated environments. Only the explicit COPILOT_GITHUB_TOKEN
# opts a GitHub token into Copilot inference.

COPILOT_VARS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")


def _clear_copilot_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in COPILOT_VARS:
        monkeypatch.delenv(key, raising=False)


def test_does_not_detect_copilot_from_gh_token_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_copilot_vars(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "gh-repo-token")
    assert find_env_keys("github-copilot") is None
    assert get_env_api_key("github-copilot") is None


def test_does_not_detect_copilot_from_github_token_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_copilot_vars(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "ci-token")
    assert find_env_keys("github-copilot") is None


def test_detects_copilot_from_the_explicit_copilot_github_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_copilot_vars(monkeypatch)
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "copilot-token")
    assert find_env_keys("github-copilot") == ["COPILOT_GITHUB_TOKEN"]
    assert get_env_api_key("github-copilot") == "copilot-token"


# A whitespace-only key is the same as no key. Treating `" "` as configured let
# the Anthropic provider look authenticated, win auto-selection, and send
# `x-api-key: " "` — which h11 rejects with `Illegal header value b' '`.


@pytest.mark.parametrize("value", ["", " ", "   ", "\t", "\n", " \t\n "])
def test_blank_anthropic_key_is_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", value)
    assert find_env_keys("anthropic") is None
    assert get_env_api_key("anthropic") is None


def test_real_anthropic_key_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert find_env_keys("anthropic") == ["ANTHROPIC_API_KEY"]
    assert get_env_api_key("anthropic") == "sk-x"


def test_surrounding_whitespace_is_stripped_off_a_real_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-x\n")
    assert find_env_keys("anthropic") == ["ANTHROPIC_API_KEY"]
    assert get_env_api_key("anthropic") == "sk-x"


def test_blank_oauth_token_falls_through_to_the_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The precedence list skips a blank entry rather than stopping on it."""
    monkeypatch.setenv("ANTHROPIC_OAUTH_TOKEN", " ")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert find_env_keys("anthropic") == ["ANTHROPIC_API_KEY"]
    assert get_env_api_key("anthropic") == "sk-x"


@pytest.mark.parametrize("value", ["", " "])
def test_blank_copilot_token_is_treated_as_absent(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    _clear_copilot_vars(monkeypatch)
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", value)
    assert find_env_keys("github-copilot") is None
    assert get_env_api_key("github-copilot") is None

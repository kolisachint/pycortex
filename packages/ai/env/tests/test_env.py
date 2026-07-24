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

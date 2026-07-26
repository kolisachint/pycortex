"""Tests for the config module."""

import os
from pathlib import Path
from unittest.mock import patch

from cortex.code.config.config import (
    APP_NAME,
    CONFIG_DIR_NAME,
    detect_install_method,
    expand_tilde_path,
    get_agent_dir,
    get_package_dir,
    get_settings_path,
)


class TestExpandTildePath:
    def test_expand_home(self):
        result = expand_tilde_path("~")
        assert result == str(Path.home())

    def test_expand_home_with_slash(self):
        result = expand_tilde_path("~/test")
        assert result == str(Path.home() / "test")

    def test_no_expansion(self):
        result = expand_tilde_path("/test/path")
        assert result == "/test/path"


class TestGetPackageDir:
    def test_returns_string(self):
        result = get_package_dir()
        assert isinstance(result, str)

    def test_env_override(self):
        with patch.dict(os.environ, {"HOOCODE_PACKAGE_DIR": "/custom/path"}):
            result = get_package_dir()
            assert result == "/custom/path"

    def test_env_override_tilde(self):
        with patch.dict(os.environ, {"HOOCODE_PACKAGE_DIR": "~/custom"}):
            result = get_package_dir()
            assert result == str(Path.home() / "custom")


class TestGetAgentDir:
    def test_returns_string(self):
        result = get_agent_dir()
        assert isinstance(result, str)

    def test_env_override(self):
        with patch.dict(os.environ, {"HOOCODE_CODING_AGENT_DIR": "/custom/agent"}):
            result = get_agent_dir()
            assert result == "/custom/agent"

    def test_default_contains_config_dir(self):
        result = get_agent_dir()
        assert CONFIG_DIR_NAME in result


class TestGetSettingsPath:
    def test_returns_string(self):
        result = get_settings_path()
        assert isinstance(result, str)

    def test_ends_with_settings_json(self):
        result = get_settings_path()
        assert result.endswith("settings.json")


class TestConstants:
    def test_app_name(self):
        assert APP_NAME == "hoocode"

    def test_config_dir_name(self):
        assert CONFIG_DIR_NAME == ".hoocode"


class TestDetectInstallMethod:
    def test_returns_string(self):
        result = detect_install_method()
        assert isinstance(result, str)
        assert result in ["bun-binary", "npm", "pnpm", "yarn", "bun", "unknown"]

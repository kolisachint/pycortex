"""Tests for ``resolve_config_value``.

The TS has no test file of its own for ``resolve-config-value.ts`` — it is covered
indirectly through ``auth-storage.test.ts`` and ``model-registry.test.ts``, both
of which are ported. These are the direct ones, for the three-way rule itself and
for the two behaviours only reachable from here: ``resolve_headers`` dropping what
does not resolve where ``resolve_headers_or_throw`` raises on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cortex.code.config.resolve_config_value import (
    clear_config_value_cache,
    resolve_config_value,
    resolve_config_value_or_throw,
    resolve_config_value_uncached,
    resolve_headers,
    resolve_headers_or_throw,
)


@pytest.fixture(autouse=True)
def _clear_cache():  # pyright: ignore[reportUnusedFunction]
    clear_config_value_cache()
    yield
    clear_config_value_cache()


class TestTheThreeWayRule:
    def test_a_bang_prefix_is_a_shell_command(self):
        assert resolve_config_value("!echo hello") == "hello"

    def test_an_env_var_name_resolves_to_its_value(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CORTEX_TEST_RCV", "from-env")
        assert resolve_config_value("CORTEX_TEST_RCV") == "from-env"

    def test_anything_else_is_itself(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("sk-not-an-env-var", raising=False)
        assert resolve_config_value("sk-not-an-env-var") == "sk-not-an-env-var"

    def test_an_unset_env_var_name_resolves_to_the_name(self, monkeypatch: pytest.MonkeyPatch):
        """The literal fallback is what makes an inline key work, and it is why an
        unset variable cannot be an error: ``FOO`` becomes the string ``"FOO"``."""
        monkeypatch.delenv("CORTEX_TEST_RCV_UNSET", raising=False)
        assert resolve_config_value("CORTEX_TEST_RCV_UNSET") == "CORTEX_TEST_RCV_UNSET"

    def test_an_empty_env_var_falls_through_to_the_literal(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CORTEX_TEST_RCV_EMPTY", "")
        assert resolve_config_value("CORTEX_TEST_RCV_EMPTY") == "CORTEX_TEST_RCV_EMPTY"

    def test_a_failing_command_resolves_to_nothing(self):
        assert resolve_config_value("!exit 3") is None

    def test_an_empty_command_output_resolves_to_nothing(self):
        assert resolve_config_value("!printf ''") is None

    def test_output_is_trimmed_at_the_ends_only(self):
        assert resolve_config_value("!printf '  a\\nb  '") == "a\nb"


class TestCaching:
    def test_the_cached_form_runs_a_command_once(self, tmp_path: Path):
        counter = tmp_path / "n"
        counter.write_text("0")
        path = str(counter)
        command = f'!sh -c \'n=$(cat "{path}"); echo $((n + 1)) > "{path}"; echo v\''

        assert resolve_config_value(command) == "v"
        assert resolve_config_value(command) == "v"
        assert int(counter.read_text().strip()) == 1

    def test_the_uncached_form_runs_it_every_time(self, tmp_path: Path):
        counter = tmp_path / "n"
        counter.write_text("0")
        path = str(counter)
        command = f'!sh -c \'n=$(cat "{path}"); echo $((n + 1)) > "{path}"; echo v\''

        assert resolve_config_value_uncached(command) == "v"
        assert resolve_config_value_uncached(command) == "v"
        assert int(counter.read_text().strip()) == 2

    def test_the_uncached_form_does_not_populate_the_cache(self, tmp_path: Path):
        counter = tmp_path / "n"
        counter.write_text("0")
        path = str(counter)
        command = f'!sh -c \'n=$(cat "{path}"); echo $((n + 1)) > "{path}"; echo v\''

        resolve_config_value_uncached(command)
        resolve_config_value(command)
        assert int(counter.read_text().strip()) == 2


class TestOrThrow:
    def test_returns_the_value_when_there_is_one(self):
        assert resolve_config_value_or_throw("!echo v", "a key") == "v"

    def test_raises_naming_the_command_when_one_fails(self):
        with pytest.raises(RuntimeError, match="from shell command: exit 1"):
            resolve_config_value_or_throw("!exit 1", "a key")

    def test_raises_naming_the_description(self):
        with pytest.raises(RuntimeError, match="a key"):
            resolve_config_value_or_throw("!exit 1", "a key")


class TestHeaders:
    def test_none_stays_none(self):
        assert resolve_headers(None) is None
        assert resolve_headers_or_throw(None, "x") is None

    def test_empty_stays_none(self):
        assert resolve_headers({}) is None

    def test_values_resolve(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CORTEX_TEST_HDR", "hv")
        assert resolve_headers({"A": "CORTEX_TEST_HDR", "B": "!echo bv"}) == {
            "A": "hv",
            "B": "bv",
        }

    def test_the_lenient_form_drops_what_cannot_resolve(self):
        assert resolve_headers({"A": "!exit 1", "B": "literal"}) == {"B": "literal"}

    def test_all_dropped_becomes_none(self):
        assert resolve_headers({"A": "!exit 1"}) is None

    def test_the_strict_form_raises_naming_the_header(self):
        with pytest.raises(RuntimeError, match='header "A"'):
            resolve_headers_or_throw({"A": "!exit 1"}, 'provider "demo"')


class TestCachingBoundaries:
    """Added after mutation testing: the tests above never had the cached and
    uncached forms meet a *populated* cache in that order."""

    def test_the_uncached_form_re_runs_even_when_the_cache_is_warm(self, tmp_path: Path):
        counter = tmp_path / "n"
        counter.write_text("0")
        path = str(counter)
        command = f'!sh -c \'n=$(cat "{path}"); echo $((n + 1)) > "{path}"; echo v\''

        assert resolve_config_value(command) == "v"  # populates the cache
        assert int(counter.read_text().strip()) == 1

        assert resolve_config_value_uncached(command) == "v"
        assert int(counter.read_text().strip()) == 2


class TestExitStatus:
    """A command that fails *and* prints must still resolve to nothing.

    Added after mutation testing: `!exit 1` prints nothing, so the empty-output
    rule alone made the failure look handled and deleting the status check
    changed nothing.
    """

    def test_output_from_a_failing_command_is_not_used(self):
        assert resolve_config_value("!sh -c 'echo partial-key; exit 1'") is None

    def test_output_from_a_succeeding_command_is_used(self):
        assert resolve_config_value("!sh -c 'echo whole-key; exit 0'") == "whole-key"

"""Every sequence the harness can send must parse back to the key it claims.

Without this the harness could send bytes the app does not recognise, and the
resulting scenario failure would point at the app instead of at the table.
"""

from __future__ import annotations

import pytest
from cortex.code.e2e import KEY_SEQUENCES, resolve_key
from cortex.tui.keys import parse_key


@pytest.mark.parametrize("name", sorted(KEY_SEQUENCES))
def test_sequence_round_trips_through_the_real_parser(name: str) -> None:
    parsed = parse_key(KEY_SEQUENCES[name])
    assert parsed == name, f"{name!r} sends bytes the app parses as {parsed!r}"


def test_unknown_key_raises_rather_than_typing_its_name() -> None:
    with pytest.raises(KeyError, match="unknown key"):
        resolve_key("ctrl+shift+meta+nope")

"""The corpus is Phase 7's ledger, so its shape is itself under test."""

from __future__ import annotations

import pytest
from cortex.code.e2e import SCENARIOS, Scenario, run_scenario


def test_ids_are_unique() -> None:
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids)), "duplicate scenario ids would collide in the report"


def test_every_scenario_is_attributed_to_a_phase_7_step() -> None:
    for s in SCENARIOS:
        assert s.blocked_by.startswith("7."), f"{s.id} is attributed to {s.blocked_by}"


def test_every_scenario_has_a_human_readable_title() -> None:
    for s in SCENARIOS:
        assert len(s.title) > 15, f"{s.id} needs a title a user can check against"


@pytest.mark.parametrize("target", [s for s in SCENARIOS if not s.pending], ids=lambda s: s.id)
def test_implemented_scenarios_pass(target: Scenario) -> None:
    result = run_scenario(target)
    assert result.status == "passing", result.detail


def test_pending_scenarios_report_pending_rather_than_raising() -> None:
    pending = next(s for s in SCENARIOS if s.pending)
    result = run_scenario(pending)
    assert result.status == "pending"


def test_a_raising_scenario_is_failing_not_an_error() -> None:
    def boom() -> None:
        raise RuntimeError("nope")

    result = run_scenario(Scenario("x/boom", "a scenario that raises on purpose", "7.1", boom))
    assert result.status == "failing"
    assert "nope" in result.detail


def test_step_7_1_is_fully_covered() -> None:
    """7.1 delivers the harness, so its own scenarios must already be green."""
    own = [s for s in SCENARIOS if s.blocked_by == "7.1"]
    assert own, "7.1 must own at least one scenario"
    assert all(not s.pending for s in own)

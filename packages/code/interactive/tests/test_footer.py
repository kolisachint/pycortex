"""Tests for the footer.

The width invariant is the one that matters: `tui.ts` crashes on a rendered line
wider than the terminal, so every line the footer returns must fit whatever it
was handed — including when the content on its own does not.
"""

from __future__ import annotations

import pytest
from cortex.code.interactive.components.footer import (
    FooterComponent,
    FooterState,
    assemble_line,
    context_gauge,
    format_tokens,
)
from cortex.tui.util import visible_width


def _plain(state: FooterState, width: int = 80) -> list[str]:
    """Rendered footer with the colour stripped, for width and content checks."""
    import re

    return [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in FooterComponent(state).render(width)]


class TestAssembleLine:
    def test_right_is_flush_right_when_it_fits(self):
        assert assemble_line(20, "left", "left", "right", "right") == "left           right"

    def test_right_is_dropped_when_it_would_not_fit(self):
        line = assemble_line(10, "left", "left", "much too long", "much too long")
        assert line == "left      "

    def test_left_is_padded_to_the_full_width(self):
        assert assemble_line(8, "ab", "ab") == "ab      "

    def test_overlong_left_is_truncated_to_width(self):
        line = assemble_line(6, "abcdefghij", "abcdefghij")
        assert visible_width(line) <= 6

    def test_gap_of_at_least_two_columns_is_required(self):
        # left(4) + 2 + right(4) == 10 fits; one column narrower does not.
        assert assemble_line(10, "left", "left", "rite", "rite").endswith("rite")
        assert assemble_line(9, "left", "left", "rite", "rite") == "left     "


class TestContextGauge:
    @pytest.mark.parametrize(
        ("percent", "filled"),
        [(0, 0), (12.5, 1), (50, 4), (99, 8), (100, 8), (150, 8), (-10, 0)],
    )
    def test_fill_tracks_the_percentage(self, percent: float, filled: int):
        plain, _ = context_gauge(percent, 90, 70)
        assert plain == "▰" * filled + "▱" * (8 - filled)

    def test_rounds_half_up_like_the_ts(self):
        # 6.25% of 8 cells is 0.5 — JS `Math.round` gives 1, Python's `round` 0.
        plain, _ = context_gauge(6.25, 90, 70)
        assert plain.startswith("▰")

    def test_colour_escalates_at_the_warn_and_error_levels(self):
        _, ok = context_gauge(10, 90, 70)
        _, warn = context_gauge(75, 90, 70)
        _, err = context_gauge(95, 90, 70)
        assert ok != warn != err
        assert ok != err


class TestFormatTokens:
    @pytest.mark.parametrize(
        ("count", "expected"),
        [
            (0, "0"),
            (999, "999"),
            (1000, "1.0k"),
            # 1.25 is exactly representable, so this is a real fork in the road:
            # JS `toFixed` rounds it away from zero, Python's `%.1f` to even.
            (1250, "1.3k"),
            (9999, "10.0k"),
            (10000, "10k"),
            (128000, "128k"),
            (999999, "1000k"),
            (1000000, "1.0M"),
            (9999999, "10.0M"),
            (10000000, "10M"),
        ],
    )
    def test_thresholds(self, count: int, expected: str):
        assert format_tokens(count) == expected


class TestRender:
    def test_two_lines_by_default(self):
        assert len(_plain(FooterState(cwd="~/p"))) == 2

    def test_first_line_leads_with_the_brand_mark_and_mode(self):
        line1 = _plain(FooterState(cwd="~/p"))[0]
        assert line1.startswith("⬢ BUILD  ~/p")

    def test_mode_is_upper_cased(self):
        assert _plain(FooterState(cwd="~/p", mode="plan"))[0].startswith("⬢ PLAN")

    def test_branch_and_session_name_follow_the_path(self):
        line1 = _plain(FooterState(cwd="~/p", git_branch="main", session_name="fix"))[0]
        assert line1.startswith("⬢ BUILD  ~/p ⑂ main • fix")

    def test_subagent_count_sits_flush_right(self):
        line1 = _plain(FooterState(cwd="~/p", active_subagents=3))[0]
        assert line1.rstrip().endswith("◇3 running")

    def test_no_subagent_cue_when_nothing_is_delegated(self):
        assert "running" not in _plain(FooterState(cwd="~/p"))[0]

    def test_second_line_shows_the_gauge_and_the_model(self):
        line2 = _plain(FooterState(cwd="~/p", model_id="gpt-5", context_window=200000))[1]
        assert line2.startswith("▱▱▱▱▱▱▱▱ 0.0% 200k auto@100%")
        assert line2.rstrip().endswith("gpt-5")

    def test_no_model_is_named_as_such(self):
        assert _plain(FooterState(cwd="~/p"))[1].rstrip().endswith("no-model")

    def test_context_percent_rounds_half_away_from_zero(self):
        # (2.25).toFixed(1) is "2.3" in JS and "2.2" under Python's `%.1f`.
        line2 = _plain(FooterState(cwd="~/p", context_percent=2.25))[1]
        assert "2.3%" in line2

    def test_unknown_context_percent_renders_as_a_question_mark(self):
        line2 = _plain(FooterState(cwd="~/p", context_percent=None))[1]
        assert " ? " in line2

    def test_auto_compact_threshold_accounts_for_the_reserve(self):
        state = FooterState(cwd="~/p", context_window=200000, compaction_reserve_tokens=20000)
        assert "auto@90%" in _plain(state)[1]

    def test_auto_indicator_disappears_when_auto_compaction_is_off(self):
        state = FooterState(cwd="~/p", context_window=200000, auto_compact_enabled=False)
        line2 = _plain(state)[1]
        assert "auto" not in line2

    def test_usage_arrows_appear_only_for_non_zero_counters(self):
        line2 = _plain(FooterState(cwd="~/p", total_input=1200, total_cache_read=64000))[1]
        assert "↑1.2k" in line2
        assert "R64k" in line2
        assert "↓" not in line2
        assert "W" not in line2

    def test_cost_is_shown_to_three_decimals(self):
        assert "$0.125" in _plain(FooterState(cwd="~/p", total_cost=0.125))[1]

    def test_subscription_use_is_flagged_even_at_zero_cost(self):
        assert "$0.000 (sub)" in _plain(FooterState(cwd="~/p", using_subscription=True))[1]

    def test_thinking_level_follows_a_reasoning_model(self):
        state = FooterState(cwd="~/p", model_id="o3", model_reasoning=True, thinking_level="high")
        assert _plain(state)[1].rstrip().endswith("o3 • high")

    def test_reasoning_model_with_no_level_reads_as_off(self):
        state = FooterState(cwd="~/p", model_id="o3", model_reasoning=True)
        assert _plain(state)[1].rstrip().endswith("o3 • thinking off")

    def test_provider_is_prepended_when_several_are_configured(self):
        state = FooterState(
            cwd="~/p", model_id="gpt-5", model_provider="openai", available_provider_count=2
        )
        assert _plain(state)[1].rstrip().endswith("(openai) gpt-5")

    def test_provider_is_dropped_rather_than_overflow(self):
        state = FooterState(
            cwd="~/p",
            model_id="gpt-5",
            model_provider="openai",
            available_provider_count=2,
            context_window=200000,
            total_input=123456,
            total_output=98765,
            total_cache_read=456789,
            total_cache_write=12345,
            total_cost=12.5,
        )
        assert "(openai)" not in _plain(state, 60)[1]

    @pytest.mark.parametrize("width", [10, 20, 40, 80, 200])
    def test_every_line_fits_the_terminal(self, width: int):
        state = FooterState(
            cwd="~/some/deeply/nested/working/directory",
            git_branch="feature/a-rather-long-branch-name",
            session_name="a session with a name",
            model_id="a-model-with-a-long-identifier",
            model_provider="provider",
            available_provider_count=4,
            active_subagents=7,
            context_window=1000000,
            context_percent=87.5,
            total_input=1234567,
            total_output=234567,
            total_cost=3.25,
        )
        for line in _plain(state, width):
            assert visible_width(line) <= width, f"{line!r} exceeds {width}"

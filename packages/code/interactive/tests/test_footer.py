"""Tests for the footer.

The width invariant is the one that matters: `tui.ts` crashes on a rendered line
wider than the terminal, so every line the footer returns must fit whatever it
was handed — including when the content on its own does not.

Everything the footer draws is re-derived from a session and a data provider on
every frame (7.7), so the fixtures here are the two of them: `_session()` is the
shape `AgentSession` presents to the component, `_provider()` the read-only slice
of `FooterDataProvider`. Both mirror `test/footer-width.test.ts`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from cortex.code.interactive.components.footer import (
    FooterComponent,
    active_subagent_count,
    assemble_line,
    context_gauge,
    format_tokens,
    sanitize_status_text,
    set_task_source,
)
from cortex.code.interactive.startup_progress import (
    DownloadProgress,
    ErrorProgress,
    WorkProgress,
    startup_progress,
)
from cortex.tui.util import visible_width


def _usage(
    *, input: int = 0, output: int = 0, cache_read: int = 0, cache_write: int = 0, cost: float = 0.0
) -> dict[str, Any]:
    return {
        "input": input,
        "output": output,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "cost": {"total": cost},
    }


def _session(
    *,
    cwd: str = "/w/p",
    session_name: str | None = None,
    model_id: str | None = "gpt-5",
    provider: str = "openai",
    reasoning: bool = False,
    thinking_level: str = "off",
    context_window: int = 0,
    context_percent: float | None = 0.0,
    reserve_tokens: int = 0,
    entries: list[dict[str, Any]] | None = None,
    using_oauth: bool = False,
) -> Any:
    """The slice of `AgentSession` the footer reads."""
    model = (
        SimpleNamespace(
            id=model_id, provider=provider, reasoning=reasoning, context_window=context_window
        )
        if model_id is not None
        else None
    )
    context_usage = (
        SimpleNamespace(context_window=context_window, percent=context_percent)
        if context_window > 0
        else None
    )
    return SimpleNamespace(
        state=SimpleNamespace(model=model, thinking_level=thinking_level),
        session_manager=SimpleNamespace(
            get_entries=lambda: entries or [],
            get_session_name=lambda: session_name,
            get_cwd=lambda: cwd,
        ),
        settings_manager=SimpleNamespace(get_compaction_reserve_tokens=lambda: reserve_tokens),
        get_context_usage=lambda: context_usage,
        model_registry=SimpleNamespace(
            is_using_oauth=lambda _model: using_oauth  # pyright: ignore[reportUnknownLambdaType]
        ),
    )


def _never_changes(_callback: Callable[[], None]) -> Callable[[], None]:
    """`on_branch_change` for a provider whose branch never moves under it."""
    return lambda: None


def _provider(
    *,
    branch: str | None = None,
    mode: str = "build",
    provider_count: int = 1,
    statuses: dict[str, str] | None = None,
) -> Any:
    """The read-only slice of `FooterDataProvider` the footer reads."""
    return SimpleNamespace(
        get_git_branch=lambda: branch,
        get_active_mode=lambda: mode,
        get_available_provider_count=lambda: provider_count,
        get_extension_statuses=lambda: statuses or {},
        get_subagent_enabled=lambda: False,
        on_branch_change=_never_changes,
    )


def _plain(session: Any = None, provider: Any = None, width: int = 80) -> list[str]:
    """Rendered footer with the colour stripped, for width and content checks."""
    footer = FooterComponent(session or _session(), provider or _provider())
    return [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in footer.render(width)]


@pytest.fixture(autouse=True)
def _empty_stores() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Both stores the footer reads are process-wide; no test may leak into the next."""
    startup_progress.clear()
    set_task_source(list)
    yield
    startup_progress.clear()
    set_task_source(list)


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
        assert len(_plain()) == 2

    def test_first_line_leads_with_the_brand_mark_and_mode(self):
        assert _plain()[0].startswith("⬢ BUILD  /w/p")

    def test_mode_is_upper_cased(self):
        assert _plain(provider=_provider(mode="plan"))[0].startswith("⬢ PLAN")

    def test_the_home_directory_is_shortened(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HOME", "/home/ada")
        assert _plain(_session(cwd="/home/ada/work"))[0].startswith("⬢ BUILD  ~/work")

    def test_branch_and_session_name_follow_the_path(self):
        line1 = _plain(_session(session_name="fix"), _provider(branch="main"))[0]
        assert line1.startswith("⬢ BUILD  /w/p ⑂ main • fix")

    def test_subagent_count_sits_flush_right(self):
        set_task_source(lambda: [{"source": "subagent", "status": "in_progress"}] * 3)
        assert _plain()[0].rstrip().endswith("◇3 running")

    def test_no_subagent_cue_when_nothing_is_delegated(self):
        assert "running" not in _plain()[0]

    def test_only_running_subagent_tasks_are_counted(self):
        set_task_source(
            lambda: [
                {"source": "subagent", "status": "in_progress"},
                {"source": "subagent", "status": "completed"},
                {"source": "user", "status": "in_progress"},
            ]
        )
        assert active_subagent_count() == 1

    def test_second_line_shows_the_gauge_and_the_model(self):
        line2 = _plain(_session(context_window=200000))[1]
        assert line2.startswith("▱▱▱▱▱▱▱▱ 0.0% 200k auto@100%")
        assert line2.rstrip().endswith("gpt-5")

    def test_no_model_is_named_as_such(self):
        assert _plain(_session(model_id=None))[1].rstrip().endswith("no-model")

    def test_context_percent_rounds_half_away_from_zero(self):
        # (2.25).toFixed(1) is "2.3" in JS and "2.2" under Python's `%.1f`.
        line2 = _plain(_session(context_window=200000, context_percent=2.25))[1]
        assert "2.3%" in line2

    def test_unknown_context_percent_renders_as_a_question_mark(self):
        # What the session reports between a compaction and the next response.
        line2 = _plain(_session(context_window=200000, context_percent=None))[1]
        assert " ? " in line2

    def test_the_window_falls_back_to_the_model_when_usage_is_unavailable(self):
        session = _session(context_window=0)
        session.state.model.context_window = 200000
        assert "200k" in _plain(session)[1]

    def test_auto_compact_threshold_accounts_for_the_reserve(self):
        assert "auto@90%" in _plain(_session(context_window=200000, reserve_tokens=20000))[1]

    def test_auto_indicator_disappears_when_auto_compaction_is_off(self):
        footer = FooterComponent(_session(context_window=200000), _provider())
        footer.set_auto_compact_enabled(False)
        line2 = re.sub(r"\x1b\[[0-9;]*m", "", footer.render(80)[1])
        assert "auto" not in line2

    def test_usage_arrows_appear_only_for_non_zero_counters(self):
        entries = [
            {"type": "message", "message": {"role": "assistant", "usage": _usage(input=1200)}},
            {
                "type": "message",
                "message": {"role": "assistant", "usage": _usage(cache_read=64000)},
            },
        ]
        line2 = _plain(_session(entries=entries))[1]
        assert "↑1.2k" in line2
        assert "R64k" in line2
        assert "↓" not in line2
        assert "W" not in line2

    def test_usage_accumulates_over_every_assistant_entry(self):
        entries = [
            {"type": "message", "message": {"role": "assistant", "usage": _usage(input=100)}},
            {"type": "message", "message": {"role": "user", "usage": _usage(input=7)}},
            {"type": "compaction", "message": {"role": "assistant", "usage": _usage(input=999)}},
            {"type": "message", "message": {"role": "assistant", "usage": _usage(input=400)}},
        ]
        # Only assistant messages are billed — a `user` entry carrying a usage
        # block is the echo of the request, not a second charge — and a
        # compaction entry is not a message at all. The two assistants are
        # summed, rather than the last one winning.
        assert "↑500" in _plain(_session(entries=entries))[1]

    def test_cost_is_shown_to_three_decimals(self):
        entries = [
            {"type": "message", "message": {"role": "assistant", "usage": _usage(cost=0.125)}}
        ]
        assert "$0.125" in _plain(_session(entries=entries))[1]

    def test_cost_rounds_half_away_from_zero(self):
        # 0.0625 is exactly representable, so the two roundings really do part
        # ways: JS `toFixed(3)` gives "0.063", Python's `%.3f` "0.062".
        entries = [
            {"type": "message", "message": {"role": "assistant", "usage": _usage(cost=0.0625)}}
        ]
        assert "$0.063" in _plain(_session(entries=entries))[1]

    def test_subscription_use_is_flagged_even_at_zero_cost(self):
        assert "$0.000 (sub)" in _plain(_session(using_oauth=True))[1]

    def test_thinking_level_follows_a_reasoning_model(self):
        session = _session(model_id="o3", reasoning=True, thinking_level="high")
        assert _plain(session)[1].rstrip().endswith("o3 • high")

    def test_reasoning_model_with_no_level_reads_as_off(self):
        session = _session(model_id="o3", reasoning=True, thinking_level="off")
        assert _plain(session)[1].rstrip().endswith("o3 • thinking off")

    def test_provider_is_prepended_when_several_are_configured(self):
        line2 = _plain(provider=_provider(provider_count=2))[1]
        assert line2.rstrip().endswith("(openai) gpt-5")

    def test_provider_is_dropped_rather_than_overflow(self):
        entries = [
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "usage": _usage(
                        input=123456, output=98765, cache_read=456789, cache_write=12345, cost=12.5
                    ),
                },
            }
        ]
        session = _session(context_window=200000, entries=entries)
        assert "(openai)" not in _plain(session, _provider(provider_count=2), 60)[1]

    @pytest.mark.parametrize("width", [10, 20, 40, 80, 200])
    def test_every_line_fits_the_terminal(self, width: int):
        set_task_source(lambda: [{"source": "subagent", "status": "in_progress"}] * 7)
        startup_progress.set(
            DownloadProgress(key="rg", label="ripgrep", received_bytes=1 << 20, total_bytes=1 << 22)
        )
        entries = [
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "usage": _usage(input=1234567, output=234567, cost=3.25),
                },
            }
        ]
        session = _session(
            cwd="/w/some/deeply/nested/working/directory",
            session_name="a session with a name",
            model_id="a-model-with-a-long-identifier",
            context_window=1000000,
            context_percent=87.5,
            entries=entries,
        )
        provider = _provider(
            branch="feature/a-rather-long-branch-name",
            provider_count=4,
            statuses={"ext": "a status line that goes on and on and on"},
        )
        for line in _plain(session, provider, width):
            assert visible_width(line) <= width, f"{line!r} exceeds {width}"

    @pytest.mark.parametrize(
        ("width", "session", "provider"),
        [
            # Both cases from `footer-width.test.ts`: a session name and a model
            # id made of wide (two-cell) characters, which a `len()`-based width
            # would under-count by half and overflow the terminal with.
            (93, _session(session_name="한글" * 30, context_window=200000), _provider()),
            (
                60,
                _session(
                    model_id="模" * 30,
                    provider="공급자",
                    reasoning=True,
                    thinking_level="high",
                    context_window=200000,
                    entries=[
                        {
                            "type": "message",
                            "message": {
                                "role": "assistant",
                                "usage": _usage(input=12345, output=6789, cost=1.234),
                            },
                        }
                    ],
                ),
                _provider(provider_count=2),
            ),
        ],
    )
    def test_wide_characters_do_not_overflow(self, width: int, session: Any, provider: Any):
        for line in _plain(session, provider, width):
            assert visible_width(line) <= width, f"{line!r} exceeds {width}"


class TestExtensionStatuses:
    def test_statuses_land_on_a_third_line_sorted_by_key(self):
        provider = _provider(statuses={"b": "second", "a": "first"})
        lines = _plain(provider=provider)
        assert len(lines) == 3
        assert lines[2].startswith("first second")

    def test_control_characters_are_flattened(self):
        assert sanitize_status_text("a\nb\tc  d\r\n") == "a b c d"

    def test_a_multi_line_status_stays_one_line(self):
        provider = _provider(statuses={"a": "indexing\n42 files"})
        lines = _plain(provider=provider)
        assert len(lines) == 3
        assert lines[2].startswith("indexing 42 files")

    def test_no_line_when_no_extension_says_anything(self):
        assert len(_plain()) == 2


class TestStartupProgress:
    def test_a_download_shows_a_bar_and_the_transferred_size(self):
        startup_progress.set(
            DownloadProgress(
                key="fd", label="fd", received_bytes=1 << 20, total_bytes=4 * (1 << 20)
            )
        )
        line = _plain()[2]
        assert line.startswith("fd ····")
        assert "25%" in line
        assert "1.0 MB / 4.0 MB" in line

    def test_a_download_of_unknown_size_drops_the_bar(self):
        startup_progress.set(
            DownloadProgress(key="fd", label="fd", received_bytes=1 << 21, total_bytes=None)
        )
        line = _plain()[2]
        assert line == "fd 2.0 MB…"

    def test_counted_work_shows_its_units(self):
        startup_progress.set(
            WorkProgress(key="index", label="index", done=3, total=4, unit="files")
        )
        line = _plain()[2]
        assert "75%" in line
        assert "3/4 files" in line

    def test_the_bar_rounds_half_up_like_the_ts(self):
        from cortex.code.interactive.theme import get_theme

        # 3 of 8 over 12 cells is 4.5 filled: `Math.round` gives 5, truncation 4.
        # Both halves of the bar are `·`, and only the colour tells them apart —
        # so this reads the styled line rather than the plain one.
        startup_progress.set(
            WorkProgress(key="index", label="index", done=3, total=8, unit="files")
        )
        footer = FooterComponent(_session(), _provider())
        line = footer.render(80)[2]
        assert get_theme().fg("accent", "·" * 5) in line

    def test_work_with_no_total_does_not_divide_by_zero(self):
        startup_progress.set(
            WorkProgress(key="index", label="index", done=0, total=0, unit="files")
        )
        assert "0%" in _plain()[2]

    def test_an_error_entry_is_a_plain_message(self):
        startup_progress.set(ErrorProgress(key="rg", label="ripgrep", message="download failed"))
        assert _plain()[2] == "ripgrep: download failed"

    def test_one_line_per_entry_in_insertion_order(self):
        startup_progress.set(ErrorProgress(key="a", label="first", message="x"))
        startup_progress.set(ErrorProgress(key="b", label="second", message="y"))
        lines = _plain()
        assert lines[2].startswith("first") and lines[3].startswith("second")

    def test_a_settled_entry_leaves_the_footer(self):
        startup_progress.set(ErrorProgress(key="a", label="first", message="x"))
        startup_progress.remove("a")
        assert len(_plain()) == 2

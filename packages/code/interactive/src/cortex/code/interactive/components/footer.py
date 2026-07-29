"""The footer: where you are, and what the session is costing.

Port of ``modes/interactive/components/footer.ts``.

Two lines always, and up to two kinds of transient line under them: identity and
location on the first (brand mark, mode, path, branch, session name, live
subagent count), session vitals on the second (context gauge, token and cost
deltas, model and thinking level), then the extension status line and one line
per in-flight startup download or index build.

Step 7.2 shipped the line *assembly* over a ``FooterState`` projection, because
neither the session nor the data provider had been ported. Both exist now, so the
component reads them directly as the TS does: everything on screen is derived at
render time, which is what makes the footer follow a turn, a branch switch or a
model change without anyone telling it to.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from cortex.code.interactive.brand import BRAND_MARK, GIT_BRANCH_GLYPH
from cortex.code.interactive.footer_data_provider import ReadonlyFooterDataProvider
from cortex.code.interactive.startup_progress import (
    DownloadProgress,
    ErrorProgress,
    StartupProgress,
    startup_progress,
)
from cortex.code.interactive.theme import get_theme
from cortex.tui.util import truncate_to_width, visible_width

__all__ = [
    "FooterComponent",
    "active_subagent_count",
    "assemble_line",
    "context_gauge",
    "format_tokens",
    "render_startup_line",
    "sanitize_status_text",
    "set_task_source",
]


def _to_fixed(value: float, digits: int) -> str:
    """JavaScript ``Number.prototype.toFixed``.

    Not ``f"{value:.Nf}"``: that rounds half to even, so a context fill of
    ``2.5%`` would render as ``2%`` where the TS renders ``3%``. Every number in
    this footer is one a user could sit and watch tick over, so the two must
    agree digit for digit.
    """
    return str(Decimal(repr(value)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def _js_round(value: float) -> int:
    """JavaScript ``Math.round`` — half away from zero (upwards), not to even."""
    return math.floor(value + 0.5)


def assemble_line(
    width: int,
    left_plain: str,
    left_styled: str,
    right_plain: str = "",
    right_styled: str = "",
) -> str:
    """Assemble one footer line.

    ``left`` flush left, ``right`` flush right when it fits (≥2 cols between),
    padded to the full width. When it doesn't fit, drop ``right`` and pad; when
    even ``left`` overflows, truncate it. Width math runs on the plain strings;
    the styled strings carry the colour. Every returned line is exactly ``width``
    cells or fewer — the invariant the footer-width tests hold us to.
    """
    lw = visible_width(left_plain)
    if right_plain and lw + 2 + visible_width(right_plain) <= width:
        return left_styled + " " * (width - lw - visible_width(right_plain)) + right_styled
    if lw <= width:
        return left_styled + " " * (width - lw)
    return truncate_to_width(left_styled, width, get_theme().fg("dim", "…"))


def context_gauge(percent: float, error_level: float, warn_level: float) -> tuple[str, str]:
    """A compact context-fill gauge, coloured by proximity to the auto-compact trip point.

    Returns ``(plain, styled)``.
    """
    cells = 8
    filled = max(0, min(cells, _js_round((percent / 100) * cells)))
    fill = "▰" * filled
    track = "▱" * (cells - filled)
    color = "error" if percent >= error_level else "warning" if percent >= warn_level else "accent"
    theme = get_theme()
    return fill + track, theme.fg(color, fill) + theme.fg("dim", track)


#: Where the live task list comes from. The TS reads the process-wide
#: ``taskStore``; ``core/task-store.ts`` is not in this step's file list and is
#: not ported, so the footer counts an empty list until it is. This is the seam
#: it arrives through — the *filter* below is the ported part, and it is what
#: decides which task is a running delegation rather than a plan row.
_task_source: Callable[[], Sequence[Any]] = list


def set_task_source(source: Callable[[], Sequence[Any]]) -> None:
    """Point the subagent counter at a task list."""
    global _task_source
    _task_source = source


def active_subagent_count() -> int:
    """Count subagent runs currently in flight, for the footer's live delegation cue."""
    return sum(
        1
        for task in _task_source()
        if _attr(task, "source") == "subagent" and _attr(task, "status") == "in_progress"
    )


def _attr(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def sanitize_status_text(text: str) -> str:
    """Sanitize text for display in a single-line status.

    Removes newlines, tabs, carriage returns, and collapses runs of spaces.
    """
    collapsed = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    while "  " in collapsed:
        collapsed = collapsed.replace("  ", " ")
    return collapsed.strip()


def format_tokens(count: int) -> str:
    if count < 1000:
        return str(count)
    if count < 10000:
        return f"{_to_fixed(count / 1000, 1)}k"
    if count < 1000000:
        return f"{_js_round(count / 1000)}k"
    if count < 10000000:
        return f"{_to_fixed(count / 1000000, 1)}M"
    return f"{_js_round(count / 1000000)}M"


#: Cells in a startup-progress bar; compact so several tools fit the footer.
STARTUP_BAR_CELLS = 12


def _format_mb(size_bytes: float) -> str:
    return f"{_to_fixed(size_bytes / (1024 * 1024), 1)} MB"


def _determinate_bar(ratio: float, detail: str) -> str:
    """``·``-fill bar + percent + trailing detail, matching the voice download bar."""
    theme = get_theme()
    clamped = max(0.0, min(1.0, ratio))
    filled = _js_round(clamped * STARTUP_BAR_CELLS)
    bar = theme.fg("accent", "·" * filled) + theme.fg("dim", "·" * (STARTUP_BAR_CELLS - filled))
    pct = f"{_js_round(clamped * 100)}%"
    return f"{bar} {theme.fg('muted', pct)} {theme.fg('dim', f'· {detail}')}"


def render_startup_line(entry: StartupProgress) -> str:
    """One footer line for a transient startup-progress entry.

    Styled like the voice download bar: a ``·`` fill over a dim track with
    percent and a ``received / total`` (or ``done/total``) detail. An
    indeterminate download (no Content-Length) drops the bar for a running byte
    count; an error entry renders as a dim message. Returns a styled string; the
    caller width-clamps it.
    """
    theme = get_theme()
    if isinstance(entry, ErrorProgress):
        return theme.fg("dim", f"{entry.label}: {entry.message}")
    label = theme.fg("text", entry.label)
    if isinstance(entry, DownloadProgress):
        if entry.total_bytes is None or entry.total_bytes <= 0:
            return f"{label} {theme.fg('dim', f'{_format_mb(entry.received_bytes)}…')}"
        detail = f"{_format_mb(entry.received_bytes)} / {_format_mb(entry.total_bytes)}"
        return f"{label} {_determinate_bar(entry.received_bytes / entry.total_bytes, detail)}"
    detail = f"{entry.done}/{entry.total} {entry.unit}"
    ratio = entry.done / entry.total if entry.total > 0 else 0.0
    return f"{label} {_determinate_bar(ratio, detail)}"


class FooterComponent:
    """Footer component that shows pwd, token stats, and context usage.

    Computes token/context stats from the session; gets the git branch, the
    active mode and the extension statuses from the provider.
    """

    def __init__(self, session: Any, footer_data: ReadonlyFooterDataProvider) -> None:
        self._session = session
        self._footer_data = footer_data
        self._auto_compact_enabled = True

    def set_session(self, session: Any) -> None:
        self._session = session

    @property
    def session(self) -> Any:
        return self._session

    def set_auto_compact_enabled(self, enabled: bool) -> None:
        self._auto_compact_enabled = enabled

    def invalidate(self) -> None:
        """No-op: git branch is cached/invalidated by the provider.

        Kept for the call sites in the interactive mode, as in the TS.
        """

    def dispose(self) -> None:
        """No-op: git watcher cleanup is handled by the provider."""

    def render(self, width: int) -> list[str]:
        session = self._session
        theme = get_theme()
        state = session.state

        # Cumulative usage over ALL session entries, not just the messages that
        # survived the last compaction: what the turn cost is what it cost.
        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_cache_write = 0
        total_cost = 0.0
        for entry in session.session_manager.get_entries():
            if _attr(entry, "type") != "message":
                continue
            message = _attr(entry, "message")
            if message is None or _attr(message, "role") != "assistant":
                continue
            usage = _attr(message, "usage")
            if usage is None:
                continue
            total_input += _attr(usage, "input") or 0
            total_output += _attr(usage, "output") or 0
            total_cache_read += _attr(usage, "cache_read") or 0
            total_cache_write += _attr(usage, "cache_write") or 0
            cost = _attr(usage, "cost")
            total_cost += (_attr(cost, "total") or 0.0) if cost is not None else 0.0

        # Context usage comes off the session, which handles compaction: after
        # one, tokens are unknown until the next LLM response.
        context_usage = session.get_context_usage()
        model = _attr(state, "model")
        context_window = (
            _attr(context_usage, "context_window")
            if context_usage is not None
            else _attr(model, "context_window")
        ) or 0
        usage_percent = _attr(context_usage, "percent") if context_usage is not None else None
        context_percent_value = usage_percent if usage_percent is not None else 0.0
        context_percent = _to_fixed(context_percent_value, 1) if usage_percent is not None else "?"

        # Replace home directory with ~
        pwd = session.session_manager.get_cwd()
        home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
        if home and pwd.startswith(home):
            pwd = f"~{pwd[len(home) :]}"
        branch = self._footer_data.get_git_branch()
        session_name = session.session_manager.get_session_name()
        mode_label = self._footer_data.get_active_mode()

        # ── Line 1 — identity & location ────────────────────────────────────
        # Lead with the brand mark + MODE (the agent's guardrail: Ask/Plan/Build/
        # Debug) in bold accent so it is the first thing the eye lands on, then
        # the path, git branch, and session name in descending emphasis. The live
        # subagent count sits flush right — present only while work is delegated.
        brand = f"{BRAND_MARK} {mode_label.upper()}"
        l1_plain = f"{brand}  {pwd}"
        l1_styled = f"{theme.bold(theme.fg('accent', brand))}  {theme.fg('muted', pwd)}"
        if branch:
            l1_plain += f" {GIT_BRANCH_GLYPH} {branch}"
            l1_styled += f" {theme.fg('dim', GIT_BRANCH_GLYPH)} {theme.fg('muted', branch)}"
        if session_name:
            l1_plain += f" • {session_name}"
            l1_styled += theme.fg("dim", f" • {session_name}")
        n_sub = active_subagent_count()
        l1_right_plain = f"◇{n_sub} running" if n_sub > 0 else ""
        l1_right_styled = (
            theme.fg("accent", f"◇{n_sub}") + theme.fg("dim", " running") if n_sub > 0 else ""
        )
        line1 = assemble_line(width, l1_plain, l1_styled, l1_right_plain, l1_right_styled)

        # ── Line 2 — session vitals ─────────────────────────────────────────
        # A context-fill gauge (coloured by proximity to the auto-compact trip
        # point) leads, then token/cost deltas, with the model + thinking level
        # flush right. Numbers read in muted, labels/arrows in dim.
        threshold_percent: float | None = None
        if self._auto_compact_enabled and context_window > 0:
            reserve_tokens = session.settings_manager.get_compaction_reserve_tokens()
            effective = context_window - reserve_tokens
            if effective > 0:
                threshold_percent = (effective / context_window) * 100
        error_level = threshold_percent - 3 if threshold_percent is not None else 90.0
        warn_level = threshold_percent - 10 if threshold_percent is not None else 70.0
        if threshold_percent is not None:
            auto_indicator = f" auto@{_to_fixed(threshold_percent, 0)}%"
        elif self._auto_compact_enabled:
            auto_indicator = " auto"
        else:
            auto_indicator = ""

        gauge_plain, gauge_styled = context_gauge(context_percent_value, error_level, warn_level)
        pct_text = "?" if context_percent == "?" else f"{context_percent}%"
        pct_color = (
            "error"
            if context_percent_value >= error_level
            else "warning"
            if context_percent_value >= warn_level
            else "muted"
        )
        win_text = f"{format_tokens(context_window)}{auto_indicator}"

        segs: list[tuple[str, str]] = [
            (
                f"{gauge_plain} {pct_text} {win_text}",
                f"{gauge_styled} {theme.fg(pct_color, pct_text)} {theme.fg('dim', win_text)}",
            )
        ]

        def arrow(symbol: str, n: int) -> tuple[str, str]:
            return (
                f"{symbol}{format_tokens(n)}",
                theme.fg("dim", symbol) + theme.fg("muted", format_tokens(n)),
            )

        if total_input:
            segs.append(arrow("↑", total_input))
        if total_output:
            segs.append(arrow("↓", total_output))
        if total_cache_read:
            segs.append(arrow("R", total_cache_read))
        if total_cache_write:
            segs.append(arrow("W", total_cache_write))
        registry = getattr(session, "model_registry", None)
        using_subscription = bool(registry.is_using_oauth(model)) if registry and model else False
        if total_cost or using_subscription:
            cost_str = f"${_to_fixed(total_cost, 3)}"
            if using_subscription:
                cost_str += " (sub)"
            segs.append((cost_str, theme.fg("muted", cost_str)))
        l2_plain = "  ".join(plain for plain, _ in segs)
        l2_styled = "  ".join(styled for _, styled in segs)

        # Right: model, thinking level, and provider (when several are configured).
        model_name = (_attr(model, "id") if model else None) or "no-model"
        r2_plain = model_name
        r2_styled = theme.fg("muted", model_name)
        if model is not None and _attr(model, "reasoning"):
            tl = _attr(state, "thinking_level") or "off"
            tstr = "thinking off" if tl == "off" else tl
            r2_plain += f" • {tstr}"
            r2_styled += theme.fg("dim", f" • {tstr}")
        if self._footer_data.get_available_provider_count() > 1 and model is not None:
            # Prepend the provider only when the whole right cluster still fits.
            provider = _attr(model, "provider")
            with_prov = f"({provider}) {r2_plain}"
            if visible_width(l2_plain) + 2 + visible_width(with_prov) <= width:
                r2_plain = with_prov
                r2_styled = theme.fg("dim", f"({provider}) ") + r2_styled
        line2 = assemble_line(width, l2_plain, l2_styled, r2_plain, r2_styled)

        lines = [line1, line2]

        # Extension statuses on a single line, sorted by key.
        extension_statuses = self._footer_data.get_extension_statuses()
        if extension_statuses:
            status_line = " ".join(
                sanitize_status_text(extension_statuses[key]) for key in sorted(extension_statuses)
            )
            # Truncated with a dim ellipsis, for consistency with the footer style.
            lines.append(truncate_to_width(status_line, width, theme.fg("dim", "...")))

        # Transient startup progress (first-run tool downloads, index build): one
        # determinate bar per entry, cleared as each settles. Width-clamped like
        # the status line so the footer never overflows.
        for entry in startup_progress.list():
            lines.append(truncate_to_width(render_startup_line(entry), width, theme.fg("dim", "…")))

        return lines

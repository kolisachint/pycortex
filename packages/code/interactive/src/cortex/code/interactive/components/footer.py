"""The footer: where you are, and what the session is costing.

Port of ``modes/interactive/components/footer.ts``.

**Shell, not the whole thing.** The TS footer reads an `AgentSession` and a
`ReadonlyFooterDataProvider`; neither exists in the port yet (7.4 and 7.7). The
line *assembly* does not depend on either, so all of it is ported here — the
width maths, the context gauge, the token formatting and both footer lines — and
the data arrives as a :class:`FooterState`, a plain projection of what the two
TS sources supply. Step 7.7 replaces that projection with the real provider and
adds the two trailing sections this shell leaves out: extension statuses and the
transient startup-progress bars, both of which need stores nothing has ported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from cortex.code.interactive.brand import BRAND_MARK, GIT_BRANCH_GLYPH
from cortex.code.interactive.theme import get_theme
from cortex.tui.util import truncate_to_width, visible_width

__all__ = ["FooterComponent", "FooterState", "assemble_line", "context_gauge", "format_tokens"]


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


@dataclass
class FooterState:
    """Everything the footer draws, projected out of the session.

    The TS reads these off `AgentSession` and `FooterDataProvider` directly. This
    stands in until 7.4/7.7 land those; the field names deliberately mirror the
    accessors they will come from, so the swap is a rewiring rather than a
    rewrite.
    """

    #: Working directory, already ``~``-shortened (`FooterDataProvider` does not
    #: do this — the TS footer does it inline, and so does :meth:`display_cwd`).
    cwd: str
    mode: str = "build"
    git_branch: str | None = None
    session_name: str | None = None
    model_id: str | None = None
    model_provider: str | None = None
    model_reasoning: bool = False
    thinking_level: str | None = None
    using_subscription: bool = False
    available_provider_count: int = 0
    active_subagents: int = 0
    context_window: int = 0
    #: Percentage of the context window in use; ``None`` renders as ``?``, which
    #: is what the TS shows between a compaction and the next response.
    context_percent: float | None = 0.0
    total_input: int = 0
    total_output: int = 0
    total_cache_read: int = 0
    total_cache_write: int = 0
    total_cost: float = 0.0
    auto_compact_enabled: bool = True
    compaction_reserve_tokens: int = 0


class FooterComponent:
    """Footer component that shows pwd, token stats, and context usage."""

    def __init__(self, state: FooterState) -> None:
        self._state = state

    def set_state(self, state: FooterState) -> None:
        self._state = state

    @property
    def state(self) -> FooterState:
        return self._state

    def set_auto_compact_enabled(self, enabled: bool) -> None:
        self._state.auto_compact_enabled = enabled

    def invalidate(self) -> None:
        """No-op: git branch is cached/invalidated by the provider."""

    def dispose(self) -> None:
        """No-op: git watcher cleanup is handled by the provider."""

    def render(self, width: int) -> list[str]:
        state = self._state
        theme = get_theme()

        context_window = state.context_window
        context_percent_value = state.context_percent if state.context_percent is not None else 0.0
        context_percent = (
            _to_fixed(context_percent_value, 1) if state.context_percent is not None else "?"
        )

        # ── Line 1 — identity & location ────────────────────────────────────
        # Lead with the brand mark + MODE (the agent's guardrail: Ask/Plan/Build/
        # Debug) in bold accent so it is the first thing the eye lands on, then
        # the path, git branch, and session name in descending emphasis. The live
        # subagent count sits flush right — present only while work is delegated.
        brand = f"{BRAND_MARK} {state.mode.upper()}"
        l1_plain = f"{brand}  {state.cwd}"
        l1_styled = f"{theme.bold(theme.fg('accent', brand))}  {theme.fg('muted', state.cwd)}"
        if state.git_branch:
            l1_plain += f" {GIT_BRANCH_GLYPH} {state.git_branch}"
            l1_styled += (
                f" {theme.fg('dim', GIT_BRANCH_GLYPH)} {theme.fg('muted', state.git_branch)}"
            )
        if state.session_name:
            l1_plain += f" • {state.session_name}"
            l1_styled += theme.fg("dim", f" • {state.session_name}")
        n_sub = state.active_subagents
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
        if state.auto_compact_enabled and context_window > 0:
            effective = context_window - state.compaction_reserve_tokens
            if effective > 0:
                threshold_percent = (effective / context_window) * 100
        error_level = threshold_percent - 3 if threshold_percent is not None else 90.0
        warn_level = threshold_percent - 10 if threshold_percent is not None else 70.0
        if threshold_percent is not None:
            auto_indicator = f" auto@{_to_fixed(threshold_percent, 0)}%"
        elif state.auto_compact_enabled:
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

        if state.total_input:
            segs.append(arrow("↑", state.total_input))
        if state.total_output:
            segs.append(arrow("↓", state.total_output))
        if state.total_cache_read:
            segs.append(arrow("R", state.total_cache_read))
        if state.total_cache_write:
            segs.append(arrow("W", state.total_cache_write))
        if state.total_cost or state.using_subscription:
            cost_str = f"${_to_fixed(state.total_cost, 3)}"
            if state.using_subscription:
                cost_str += " (sub)"
            segs.append((cost_str, theme.fg("muted", cost_str)))
        l2_plain = "  ".join(plain for plain, _ in segs)
        l2_styled = "  ".join(styled for _, styled in segs)

        # Right: model, thinking level, and provider (when several are configured).
        model_name = state.model_id or "no-model"
        r2_plain = model_name
        r2_styled = theme.fg("muted", model_name)
        if state.model_reasoning:
            tl = state.thinking_level or "off"
            tstr = "thinking off" if tl == "off" else tl
            r2_plain += f" • {tstr}"
            r2_styled += theme.fg("dim", f" • {tstr}")
        if state.available_provider_count > 1 and state.model_id:
            # Prepend the provider only when the whole right cluster still fits.
            with_prov = f"({state.model_provider}) {r2_plain}"
            if visible_width(l2_plain) + 2 + visible_width(with_prov) <= width:
                r2_plain = with_prov
                r2_styled = theme.fg("dim", f"({state.model_provider}) ") + r2_styled
        line2 = assemble_line(width, l2_plain, l2_styled, r2_plain, r2_styled)

        return [line1, line2]

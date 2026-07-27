"""End-to-end harness for the `pycortex` TUI — the app-level `tui/testkit`.

Boots the real interactive mode against a fake terminal, presses keys, and reads
the resulting cell grid. Never published, never imported by shipped code.

See `docs/04-migration-plan.md` Phase 7 for the steps this corpus tracks, and
`.hoocode/MIGRATION_NOTES.md` for why the audit needs it.
"""

from cortex.code.e2e._harness import AppHarness
from cortex.code.e2e._keys import KEY_SEQUENCES, resolve_key
from cortex.code.e2e._scenarios import (
    SCENARIOS,
    Scenario,
    ScenarioResult,
    boot_app,
    run_all,
    run_scenario,
)
from cortex.code.e2e._terminal import HarnessTerminal

__all__ = [
    "KEY_SEQUENCES",
    "SCENARIOS",
    "AppHarness",
    "HarnessTerminal",
    "Scenario",
    "ScenarioResult",
    "boot_app",
    "resolve_key",
    "run_all",
    "run_scenario",
]

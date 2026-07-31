#!/usr/bin/env bash
# Run pycortex from this checkout, always against the latest source.
#
# Why this exists: `uv run cortex` does NOT run this project. The workspace's
# only console script is `pycortex` (packages/code/_meta/pyproject.toml), so
# `cortex` falls through to whatever is on PATH — on this machine a stale Rust
# binary in ~/.cargo/bin. This wrapper always reaches the workspace entry point.
#
# Usage:
#   scripts/cortex.sh [args...]        # sync workspace, then run
#   scripts/cortex.sh --rebuild-env    # nuke .venv + caches first (slow, sure)
#   CORTEX_SKIP_SYNC=1 scripts/cortex.sh ...   # skip the sync (fast path)
#
# Runs in *your* current directory, not the repo root.

set -euo pipefail

# Resolve the repo root from this script's real location (symlink-safe).
src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd -P "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  [[ $src != /* ]] && src="$dir/$src"
done
SCRIPT_DIR="$(cd -P "$(dirname "$src")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

if ! command -v uv >/dev/null 2>&1; then
  echo "cortex: 'uv' is not on PATH. Install it: https://docs.astral.sh/uv/" >&2
  exit 127
fi

rebuild=0
args=()
for a in "$@"; do
  case "$a" in
    --rebuild-env) rebuild=1 ;;
    *) args+=("$a") ;;
  esac
done

if [ "$rebuild" = 1 ]; then
  echo "cortex: rebuilding environment in $ROOT" >&2
  rm -rf "$ROOT/.venv"
  # Stale bytecode and leftover *.egg-info can shadow a renamed/moved module.
  find "$ROOT/packages" "$ROOT/scripts" -name '__pycache__' -type d -prune \
    -exec rm -rf {} + 2>/dev/null || true
  find "$ROOT/packages" -name '*.egg-info' -type d -prune \
    -exec rm -rf {} + 2>/dev/null || true
fi

# `--all-packages` is mandatory: a bare `uv sync` collapses the venv to the root
# project's deps and breaks the `cortex.*` namespace-package merge (AGENTS.md).
# It is a ~50ms no-op when nothing changed, so it runs every time.
if [ "${CORTEX_SKIP_SYNC:-0}" != "1" ]; then
  uv sync --all-packages --project "$ROOT" --quiet
fi

# `--no-sync` stops `uv run` from doing its own narrower sync on top of ours.
# Every leaf is installed editable, so this always picks up the latest source.
exec uv run --no-sync --project "$ROOT" pycortex "${args[@]}"

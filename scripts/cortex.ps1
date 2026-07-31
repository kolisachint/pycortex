<#
.SYNOPSIS
    Run pycortex from this checkout, always against the latest source.

.DESCRIPTION
    Why this exists: `uv run cortex` does NOT run this project. The workspace's
    only console script is `pycortex` (packages/code/_meta/pyproject.toml), so
    `cortex` falls through to whatever is on PATH. This wrapper always reaches
    the workspace entry point.

    Runs in *your* current directory, not the repo root.

.PARAMETER RebuildEnv
    Delete .venv and stale caches before syncing (slow, but certain).

.EXAMPLE
    ./scripts/cortex.ps1
.EXAMPLE
    ./scripts/cortex.ps1 --version
.EXAMPLE
    ./scripts/cortex.ps1 -RebuildEnv
#>
[CmdletBinding()]
param(
    [switch]$RebuildEnv,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = 'Stop'

# Resolve the repo root from this script's own location.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "cortex: 'uv' is not on PATH. Install it: https://docs.astral.sh/uv/"
    exit 127
}

if ($RebuildEnv) {
    Write-Host "cortex: rebuilding environment in $root" -ForegroundColor Yellow
    Remove-Item -Recurse -Force (Join-Path $root '.venv') -ErrorAction SilentlyContinue
    # Stale bytecode and leftover *.egg-info can shadow a renamed/moved module.
    foreach ($dir in 'packages', 'scripts') {
        $target = Join-Path $root $dir
        if (Test-Path $target) {
            Get-ChildItem -Path $target -Recurse -Force -Directory `
                -Include '__pycache__', '*.egg-info' -ErrorAction SilentlyContinue |
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# `--all-packages` is mandatory: a bare `uv sync` collapses the venv to the root
# project's deps and breaks the `cortex.*` namespace-package merge (AGENTS.md).
# It is a ~50ms no-op when nothing changed, so it runs every time.
if ($env:CORTEX_SKIP_SYNC -ne '1') {
    & uv sync --all-packages --project $root --quiet
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# `--no-sync` stops `uv run` from doing its own narrower sync on top of ours.
# Every leaf is installed editable, so this always picks up the latest source.
$forward = if ($Args) { $Args } else { @() }
& uv run --no-sync --project $root pycortex @forward
exit $LASTEXITCODE

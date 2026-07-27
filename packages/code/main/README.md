# cortexcode-cli-main

CLI entry point for the Cortex CLI. Port of `packages/coding-agent/src/main.ts`.

`cortex.code.main:main` is what the `pycortex` console script runs — the script
is declared on the `cortexcode-code` umbrella, which is the distribution that
guarantees every leaf `main` reaches is installed.

With no flags it starts interactive mode against the real terminal. Flags whose
subsystem this port has not reached yet name the migration step that delivers
them and exit non-zero.

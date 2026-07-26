"""Cortex Code Main - CLI entry point.

Port of ``main.ts`` from ``packages/coding-agent/src/``.

This module provides the main CLI entry point for the hoocode coding assistant.
"""

from __future__ import annotations

import sys

from .args import Args as Args
from .args import parse_args, print_help


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the CLI.

    Args:
        argv: Command line arguments. Defaults to sys.argv[1:].

    Returns:
        Exit code (0 for success, non-zero for error).
    """
    if argv is None:
        argv = sys.argv[1:]

    args = parse_args(argv)

    # Handle help
    if args.help:
        print_help()
        return 0

    # Handle version
    if args.version:
        print("hoocode 0.1.0")
        return 0

    # Handle list-models
    if args.list_models is not None:
        print("Model listing not yet implemented")
        return 0

    # Handle export
    if args.export:
        print("Export not yet implemented")
        return 0

    # Handle print token surface
    if args.print_token_surface:
        print("Token surface printing not yet implemented")
        return 0

    # For now, just print a message about what would happen
    if args.print:
        # Non-interactive mode
        if args.messages:
            print(f"Would process message: {args.messages[0]}")
            if args.model:
                print(f"Using model: {args.model}")
            if args.provider:
                print(f"Using provider: {args.provider}")
        else:
            print("No message provided for print mode")
            return 1
    else:
        # Interactive mode
        print("Interactive mode not yet implemented")
        print("Use --print flag for non-interactive mode")

    return 0


if __name__ == "__main__":
    sys.exit(main())

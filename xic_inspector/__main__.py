"""Command-line entry point for python -m xic_inspector."""

from __future__ import annotations

import sys


def main() -> int:
    from xic_inspector.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    sys.exit(main())

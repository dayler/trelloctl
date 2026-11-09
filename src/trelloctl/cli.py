"""Command-line entry point.

Minimal bootstrap version: it only answers ``--version``. The full
argparse-based interface generated from the operation registry replaces it.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from trelloctl import __version__

EXIT_OK = 0
EXIT_USAGE = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--version"]:
        sys.stdout.write(f"trelloctl {__version__}\n")
        return EXIT_OK
    sys.stderr.write("trelloctl: unsupported arguments; only --version is available yet\n")
    return EXIT_USAGE

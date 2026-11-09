"""Entry point for ``python -m trelloctl``."""

import sys

from trelloctl.cli import main

if __name__ == "__main__":
    sys.exit(main())

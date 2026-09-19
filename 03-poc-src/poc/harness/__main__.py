"""``python -m poc.harness`` entry point."""

from __future__ import annotations

import sys

from poc.harness.cli import main

if __name__ == "__main__":
    sys.exit(main())

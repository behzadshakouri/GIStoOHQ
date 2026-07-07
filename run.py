#!/usr/bin/env python3
"""GIStoOHQ config-driven pipeline entry point."""

import sys

REQUIRED = (3, 9)

if sys.version_info < REQUIRED:
    sys.stderr.write(
        "GIStoOHQ requires Python 3.9 or newer. "
        "Run this entry point with `python3 run.py config.json` instead of `python run.py`.\n"
    )
    raise SystemExit(2)

from ohqbuilder.app_runner import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

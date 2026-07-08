#!/usr/bin/env python3
"""GIStoOHQ config-driven pipeline entry point."""

import os
import sys

REQUIRED = (3, 9)


def _which(name):
    try:
        from shutil import which
    except ImportError:  # pragma: no cover - Python 2 compatibility path
        from distutils.spawn import find_executable as which
    return which(name)


def _exec_supported_python():
    for candidate in ("python3.12", "python3.11", "python3.10", "python3.9", "python3"):
        executable = _which(candidate)
        if executable and os.path.abspath(executable) != os.path.abspath(sys.executable):
            os.execv(executable, [executable] + sys.argv)


if sys.version_info < REQUIRED:
    _exec_supported_python()
    sys.stderr.write(
        "GIStoOHQ requires Python 3.9 or newer. "
        "Install Python 3.9+ or run this entry point with `python3 run.py config.json`.\n"
    )
    raise SystemExit(2)

from ohqbuilder.app_runner import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Download all Python-supported GIStoOHQ source inputs for one site."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ohqbuilder.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["download-inputs", *sys.argv[1:]]))

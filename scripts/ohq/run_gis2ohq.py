#!/usr/bin/env python3
import sys
from ohqbuilder.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["build", *sys.argv[1:]]))

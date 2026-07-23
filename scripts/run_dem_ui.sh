#!/usr/bin/env bash
set -euo pipefail
if command -v ohqbuild >/dev/null 2>&1; then
  exec ohqbuild ui "$@"
fi
exec python -m ohqbuilder.cli ui "$@"

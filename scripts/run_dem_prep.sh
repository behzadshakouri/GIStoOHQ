#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  cat >&2 <<'USAGE'
Usage: scripts/run_dem_prep.sh CONFIG [--download] [--materialize] [--validate]

Runs the direct DEM preparation path through ohqbuild run-dem-prep.
Example:
  scripts/run_dem_prep.sh configs/SligoCreek.yaml --download --materialize
USAGE
  exit 2
fi

CONFIG=$1
shift
if command -v ohqbuild >/dev/null 2>&1; then
  exec ohqbuild run-dem-prep --config "$CONFIG" "$@"
fi
exec python -m ohqbuilder.cli run-dem-prep --config "$CONFIG" "$@"

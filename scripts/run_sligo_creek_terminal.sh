#!/usr/bin/env bash
# Terminal runner for the Sligo Creek GIStoOHQ workflow.
#
# Usage from the repository root:
#   ./scripts/run_sligo_creek_terminal.sh
#
# Optional overrides:
#   ROOT=/mnt/3rd900/Projects/GIStoOHQ/runs ./scripts/run_sligo_creek_terminal.sh
#   LOG_DIR=/tmp/gistoohq-logs ./scripts/run_sligo_creek_terminal.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_DIR="${LOG_DIR:-${REPO_ROOT}/runs/logs}"
LOG_FILE="${LOG_FILE:-${LOG_DIR}/sligo_creek_$(date -u +%Y%m%dT%H%M%SZ).log}"

mkdir -p "${LOG_DIR}"
cd "${REPO_ROOT}"

# Keep the Sligo defaults explicit here so this file can be copied into a
# terminal session or adjusted independently of the generic watershed runner.
export CSV_PATH="${CSV_PATH:-${REPO_ROOT}/sligo_creek.csv}"
export ROW_ID="${ROW_ID:-SligoCreek_Mouth}"
export SITE="${SITE:-SligoCreek}"
export PROJECT_NAME="${PROJECT_NAME:-SligoCreek}"
export ROOT="${ROOT:-${REPO_ROOT}/runs}"
export RUN_MODE="${RUN_MODE:-download-then-three-step}"
export PRODUCTS="${PRODUCTS:-demlr,hydro,roads,landcover,atlas14}"
export DEM_RESOLUTION="${DEM_RESOLUTION:-1/3}"
export BUFFER="${BUFFER:-20000}"
export TARGET_CRS="${TARGET_CRS:-EPSG:26918}"
export MAX_TILES="${MAX_TILES:-50}"
export MAX_FILE_SIZE_MB="${MAX_FILE_SIZE_MB:-512}"
export SOIL_PIXEL_SIZE="${SOIL_PIXEL_SIZE:-0.0003}"
export SOIL_TOP_DEPTH="${SOIL_TOP_DEPTH:-30.0}"

{
  printf 'GIStoOHQ Sligo Creek terminal run started at %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'Repository: %s\n' "${REPO_ROOT}"
  printf 'Run root  : %s\n' "${ROOT}"
  printf 'Log file  : %s\n\n' "${LOG_FILE}"

  "${PYTHON_BIN}" -m pip install -e '.[gis]'
  "${PYTHON_BIN}" -m ohqbuilder.cli doctor --strict-gis
  "${SCRIPT_DIR}/run_sligo_creek_100.sh"

  printf '\nGIStoOHQ Sligo Creek terminal run finished at %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} 2>&1 | tee "${LOG_FILE}"

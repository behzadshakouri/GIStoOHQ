#!/usr/bin/env bash
# One-command Sligo Creek runner from raw downloads through OHQ build.
#
# Usage:
#   ./scripts/run_sligo_creek_100.sh
#   DRY_RUN=1 ./scripts/run_sligo_creek_100.sh
#
# Override any exported value below if your run needs a different location or
# processing setting, for example:
#   ROOT=/mnt/3rd900/Projects/GIStoOHQ/runs ./scripts/run_sligo_creek_100.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

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

"${SCRIPT_DIR}/run_watershed_full.sh"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  exit 0
fi

SITE_ROOT="${ROOT}/${SITE}"
printf '\nVerifying Sligo Creek outputs under %s\n' "${SITE_ROOT}"

required_outputs=(
  "${SITE_ROOT}/demlr/cliped_utm.tif"
  "${SITE_ROOT}/outputs/NHDFlowline_clip.gpkg"
  "${SITE_ROOT}/outputs/flow_dir.tif"
  "${SITE_ROOT}/outputs/flow_acc.tif"
  "${SITE_ROOT}/outputs/watershed_boundary.gpkg"
)

for output in "${required_outputs[@]}"; do
  if [[ ! -e "${output}" ]]; then
    printf 'Missing expected output: %s\n' "${output}" >&2
    exit 2
  fi
  printf 'OK: %s\n' "${output}"
done

printf '\nOHQ outputs:\n'
find "${SITE_ROOT}" -maxdepth 4 -type f -iname '*.ohq' -print
printf 'Sligo Creek run complete.\n'

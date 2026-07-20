#!/usr/bin/env bash
# Run the complete GIStoOHQ workflow for the uploaded Sligo Creek CSV.
#
# Defaults assume this script is launched from the repository root and uses:
#   sligo_creek.csv -> id,latitude,longitude
# Override paths/settings with environment variables as needed, for example:
#   ROOT=/data/GIStoOHQ-runs SITE=sites/SligoCreek ./scripts/run_sligo_creek_full.sh
#   RUN_MODE=full-run ./scripts/run_sligo_creek_full.sh
#   DRY_RUN=1 ./scripts/run_sligo_creek_full.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

CSV_PATH="${CSV_PATH:-${REPO_ROOT}/sligo_creek.csv}"
ROW_ID="${ROW_ID:-SligoCreek_Mouth}"
ROOT="${ROOT:-${REPO_ROOT}/runs}"
SITE="${SITE:-SligoCreek}"
PROJECT_NAME="${PROJECT_NAME:-SligoCreek}"
BUFFER="${BUFFER:-20000}"
TARGET_CRS="${TARGET_CRS:-EPSG:26918}"
MAX_FILE_SIZE_MB="${MAX_FILE_SIZE_MB:-512}"
MAX_TILES="${MAX_TILES:-50}"
SOIL_PIXEL_SIZE="${SOIL_PIXEL_SIZE:-0.0003}"
SOIL_TOP_DEPTH="${SOIL_TOP_DEPTH:-30.0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RUN_MODE="${RUN_MODE:-download-then-three-step}"
PRODUCTS="${PRODUCTS:-all}"
RAW_DOWNLOAD_DIR="${RAW_DOWNLOAD_DIR:-${ROOT}/${SITE}/source_downloads}"
DOWNLOAD_SUMMARY="${DOWNLOAD_SUMMARY:-${ROOT}/${SITE}/source_downloads_summary.csv}"
POINTS_DIR="${POINTS_DIR:-${RAW_DOWNLOAD_DIR}}"
TIGER_YEAR="${TIGER_YEAR:-2025}"
NLCD_YEAR="${NLCD_YEAR:-2023}"

mkdir -p "${ROOT}/${SITE}"

read -r SITE_ID LAT LON < <("${PYTHON_BIN}" - "${CSV_PATH}" "${ROW_ID}" <<'PY'
import csv
import re
import sys
from pathlib import Path

csv_path = Path(sys.argv[1])
row_id = sys.argv[2]

if not csv_path.is_file():
    raise SystemExit(f"CSV not found: {csv_path}")

with csv_path.open(newline="", encoding="utf-8-sig") as handle:
    reader = csv.DictReader(handle)
    if not reader.fieldnames:
        raise SystemExit(f"CSV has no header: {csv_path}")
    rows = list(reader)

if not rows:
    raise SystemExit(f"CSV has no data rows: {csv_path}")

id_field = "id" if "id" in reader.fieldnames else reader.fieldnames[0]
lat_field = next((name for name in reader.fieldnames if name.lower() in {"lat", "latitude"}), None)
lon_field = next((name for name in reader.fieldnames if name.lower() in {"lon", "lng", "long", "longitude"}), None)
if not lat_field or not lon_field:
    raise SystemExit("CSV must include latitude and longitude columns")

match = next((row for row in rows if row.get(id_field) == row_id), None)
if match is None:
    if len(rows) == 1:
        match = rows[0]
    else:
        raise SystemExit(f"No row with {id_field}={row_id!r} in {csv_path}")

site_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", match.get(id_field, row_id)).strip("_") or row_id
lat = float(match[lat_field])
lon = float(match[lon_field])
print(site_id, lat, lon)
PY
)

cd "${REPO_ROOT}"

DOCTOR_CMD=("${PYTHON_BIN}" -m ohqbuilder.cli doctor --strict-gis)
DOWNLOAD_CMD=(
  "${PYTHON_BIN}" -m ohqbuilder.cli download-data
  "${CSV_PATH}"
  "${DOWNLOAD_SUMMARY}"
  --id-col id
  --products "${PRODUCTS}"
  --download "${RAW_DOWNLOAD_DIR}"
  --buffer "${BUFFER}"
  --make-points
  --points-dir "${POINTS_DIR}"
  --max-tiles "${MAX_TILES}"
  --max-file-size-mb "${MAX_FILE_SIZE_MB}"
  --tiger-year "${TIGER_YEAR}"
  --nlcd-year "${NLCD_YEAR}"
)
CHECK_DOWNLOAD_CMD=(
  "${PYTHON_BIN}" - "${DOWNLOAD_SUMMARY}"
)
HSG_CMD=(
  "${PYTHON_BIN}" -m ohqbuilder.cli download-hsg
  --root "${ROOT}"
  --site "${SITE}"
  --buffer "${BUFFER}"
  --pixel-size "${SOIL_PIXEL_SIZE}"
)
TEXTURE_CMD=(
  "${PYTHON_BIN}" -m ohqbuilder.cli download-texture
  --root "${ROOT}"
  --site "${SITE}"
  --buffer "${BUFFER}"
  --pixel-size "${SOIL_PIXEL_SIZE}"
  --top-depth "${SOIL_TOP_DEPTH}"
)
MATERIALIZE_CMD=(
  "${PYTHON_BIN}" -m ohqbuilder.cli materialize-inputs
  --root "${ROOT}"
  --site "${SITE}"
  --source-dir "${RAW_DOWNLOAD_DIR}"
  --target-crs "${TARGET_CRS}"
)
PREPARE_CMD=(
  "${PYTHON_BIN}" -m ohqbuilder.cli prepare-inputs
  --root "${ROOT}"
  --site "${SITE}"
  --phase all
)
BUILD_CMD=(
  "${PYTHON_BIN}" -m ohqbuilder.cli build
  --root "${ROOT}"
  --site "${SITE}"
  --project-name "${PROJECT_NAME}"
)
FULL_RUN_CMD=(
  "${PYTHON_BIN}" -m ohqbuilder.cli full-run
  --root "${ROOT}"
  --site "${SITE}"
  --lat "${LAT}"
  --lon "${LON}"
  --site-id "${SITE_ID}"
  --project-name "${PROJECT_NAME}"
  --buffer "${BUFFER}"
  --target-crs "${TARGET_CRS}"
  --max-tiles "${MAX_TILES}"
  --max-file-size-mb "${MAX_FILE_SIZE_MB}"
  --soil-pixel-size "${SOIL_PIXEL_SIZE}"
  --soil-top-depth "${SOIL_TOP_DEPTH}"
)

printf 'Sligo Creek row: id=%s lat=%s lon=%s\n' "${SITE_ID}" "${LAT}" "${LON}"
printf 'Project root: %s\nSite: %s\nRun mode: %s\nRaw downloads: %s\nSummary CSV: %s\n' "${ROOT}" "${SITE}" "${RUN_MODE}" "${RAW_DOWNLOAD_DIR}" "${DOWNLOAD_SUMMARY}"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  printf 'Dry run; commands that would run:\n'
  printf '  %q' "${PYTHON_BIN}" -m pip install -e '.[gis]'; printf '\n'
  printf '  %q' "${DOCTOR_CMD[@]}"; printf '\n'
  if [[ "${RUN_MODE}" == "full-run" ]]; then
    printf '  %q' "${FULL_RUN_CMD[@]}"; printf '\n'
  elif [[ "${RUN_MODE}" == "download-then-three-step" ]]; then
    printf '  %q' "${DOWNLOAD_CMD[@]}"; printf '\n'
    printf '  %q' "${CHECK_DOWNLOAD_CMD[@]}"; printf ' <<PY ...\n'
    printf '  %q' "${HSG_CMD[@]}"; printf '\n'
    printf '  %q' "${TEXTURE_CMD[@]}"; printf '\n'
    printf '  %q' "${MATERIALIZE_CMD[@]}"; printf '\n'
    printf '  %q' "${PREPARE_CMD[@]}"; printf '\n'
    printf '  %q' "${BUILD_CMD[@]}"; printf '\n'
  else
    printf 'Unknown RUN_MODE: %s\n' "${RUN_MODE}" >&2
    exit 2
  fi
  exit 0
fi

"${PYTHON_BIN}" -m pip install -e '.[gis]'
"${DOCTOR_CMD[@]}"

if [[ "${RUN_MODE}" == "full-run" ]]; then
  "${FULL_RUN_CMD[@]}"
elif [[ "${RUN_MODE}" == "download-then-three-step" ]]; then
  "${DOWNLOAD_CMD[@]}"
  "${CHECK_DOWNLOAD_CMD[@]}" <<'PY'
import csv
import sys
from pathlib import Path

summary = Path(sys.argv[1])
required = {"dem", "demlr", "hydro"}
errors = []
with summary.open(newline="", encoding="utf-8") as handle:
    for row in csv.DictReader(handle):
        for product in required:
            status = row.get(f"{product}_status", "")
            if status != "ok":
                errors.append(f"{row.get('id') or row.get('site_id') or 'site'} {product}: {status or 'missing status'}")
if errors:
    raise SystemExit("Downloader did not produce required OK statuses:\n" + "\n".join(errors))
print("Downloader required statuses are OK: " + ", ".join(sorted(required)))
PY
  "${HSG_CMD[@]}"
  "${TEXTURE_CMD[@]}"
  "${MATERIALIZE_CMD[@]}"
  "${PREPARE_CMD[@]}"
  "${BUILD_CMD[@]}"
else
  printf 'Unknown RUN_MODE: %s\n' "${RUN_MODE}" >&2
  exit 2
fi

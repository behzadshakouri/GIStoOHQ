#!/usr/bin/env bash
# Run the complete GIStoOHQ workflow for any watershed coordinate.
#
# Provide either LAT/LON directly or CSV_PATH plus ROW_ID. Override paths/settings
# with environment variables as needed, for example:
#   LAT=39.000215 LON=-77.01081 SITE=SligoCreek ./scripts/run_watershed_full.sh
#   CSV_PATH=/data/sites.csv ROW_ID=SligoCreek_Mouth SITE=SligoCreek ./scripts/run_watershed_full.sh
#   RUN_MODE=full-run ./scripts/run_watershed_full.sh
#   DRY_RUN=1 ./scripts/run_watershed_full.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

CSV_PATH="${CSV_PATH:-}"
ROW_ID="${ROW_ID:-}"
ID_COL="${ID_COL:-id}"
LAT="${LAT:-}"
LON="${LON:-}"
SITE_ID="${SITE_ID:-${ROW_ID:-watershed}}"
ROOT="${ROOT:-${REPO_ROOT}/runs}"
SITE="${SITE:-Watershed}"
PROJECT_NAME="${PROJECT_NAME:-${SITE}}"
BUFFER="${BUFFER:-20000}"
TARGET_CRS="${TARGET_CRS:-EPSG:26918}"
MAX_FILE_SIZE_MB="${MAX_FILE_SIZE_MB:-512}"
MAX_TILES="${MAX_TILES:-50}"
DEM_RESOLUTION="${DEM_RESOLUTION:-1/3}"
SOIL_PIXEL_SIZE="${SOIL_PIXEL_SIZE:-0.0003}"
SOIL_TOP_DEPTH="${SOIL_TOP_DEPTH:-30.0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RUN_MODE="${RUN_MODE:-download-then-three-step}"
START_AT="${START_AT:-}"
PRODUCTS="${PRODUCTS:-demlr,hydro,roads,landcover,atlas14}"
RAW_DOWNLOAD_DIR="${RAW_DOWNLOAD_DIR:-${ROOT}/${SITE}/source_downloads}"
DOWNLOAD_SUMMARY="${DOWNLOAD_SUMMARY:-${ROOT}/${SITE}/source_downloads_summary.csv}"
POINTS_DIR="${POINTS_DIR:-${RAW_DOWNLOAD_DIR}}"
TIGER_YEAR="${TIGER_YEAR:-2025}"
NLCD_YEAR="${NLCD_YEAR:-2023}"
MATERIALIZE_CLIP_BOUNDS="${MATERIALIZE_CLIP_BOUNDS:-}"
MATERIALIZE_CLIP_BOUNDS_CRS="${MATERIALIZE_CLIP_BOUNDS_CRS:-EPSG:4326}"
MATERIALIZE_SAFETY_MARGIN="${MATERIALIZE_SAFETY_MARGIN:-1.2}"
MATERIALIZE_BOUNDS_SOURCE="${MATERIALIZE_BOUNDS_SOURCE:-web-or-buffer}"

mkdir -p "${ROOT}/${SITE}"
RUN_CSV_PATH="${CSV_PATH}"

if [[ -n "${LAT}" && -n "${LON}" ]]; then
  SITE_ID="$("${PYTHON_BIN}" - "${SITE_ID}" <<'PY'
import re
import sys

value = sys.argv[1] or "watershed"
print(re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "watershed")
PY
)"
  RUN_CSV_PATH="${ROOT}/${SITE}/coordinate_input.csv"
  if [[ "${DRY_RUN:-0}" != "1" ]]; then
    "${PYTHON_BIN}" - "${RUN_CSV_PATH}" "${SITE_ID}" "${LAT}" "${LON}" <<'PY'
import csv
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=["id", "latitude", "longitude"])
    writer.writeheader()
    writer.writerow({"id": sys.argv[2], "latitude": sys.argv[3], "longitude": sys.argv[4]})
PY
  fi
else
  if [[ -z "${CSV_PATH}" ]]; then
    printf 'Set LAT/LON or CSV_PATH before running.\n' >&2
    exit 2
  fi
  read -r SITE_ID LAT LON < <("${PYTHON_BIN}" - "${CSV_PATH}" "${ROW_ID}" "${ID_COL}" <<'PY'
import csv
import re
import sys
from pathlib import Path

csv_path = Path(sys.argv[1])
row_id = sys.argv[2]
requested_id_field = sys.argv[3]

if not csv_path.is_file():
    raise SystemExit(f"CSV not found: {csv_path}")

with csv_path.open(newline="", encoding="utf-8-sig") as handle:
    reader = csv.DictReader(handle)
    if not reader.fieldnames:
        raise SystemExit(f"CSV has no header: {csv_path}")
    rows = list(reader)

if not rows:
    raise SystemExit(f"CSV has no data rows: {csv_path}")

id_field = requested_id_field if requested_id_field in reader.fieldnames else reader.fieldnames[0]
lat_field = next((name for name in reader.fieldnames if name.lower() in {"lat", "latitude"}), None)
lon_field = next((name for name in reader.fieldnames if name.lower() in {"lon", "lng", "long", "longitude"}), None)
if not lat_field or not lon_field:
    raise SystemExit("CSV must include latitude and longitude columns")

match = next((row for row in rows if row_id and row.get(id_field) == row_id), None)
if match is None:
    if len(rows) == 1:
        match = rows[0]
    else:
        raise SystemExit(f"No row with {id_field}={row_id!r} in {csv_path}")

site_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", match.get(id_field, row_id)).strip("_") or row_id or "watershed"
lat = float(match[lat_field])
lon = float(match[lon_field])
print(site_id, lat, lon)
PY
)
fi

cd "${REPO_ROOT}"

phase2_step_index() {
  case "$1" in
    delineatewatershed.py) echo 1 ;;
    subtractsubwatershed.py) echo 2 ;;
    load_cn_inputs.py) echo 3 ;;
    cliptowatershed.py) echo 4 ;;
    prepcngrid.py) echo 5 ;;
    buildcnraster.py) echo 6 ;;
    zonal_cn.py) echo 7 ;;
    extract_slope.py) echo 8 ;;
    longestflowpath.py) echo 9 ;;
    compute_tc.py) echo 10 ;;
    build_topology.py) echo 11 ;;
    write_basin.py) echo 12 ;;
    write_met.py) echo 13 ;;
    write_hms_project.py) echo 14 ;;
    *) echo 0 ;;
  esac
}

if [[ "${RUN_MODE}" == "resume-phase2" ]]; then
  REQUESTED_START_AT="${START_AT}"
  AUTO_START_AT=""
  AUTO_START_REASON=""
  PHASE2_MARKER="${ROOT}/${SITE}/outputs/.phase2_failed_step"
  if [[ -s "${PHASE2_MARKER}" ]]; then
    AUTO_START_AT="$(head -n 1 "${PHASE2_MARKER}")"
    AUTO_START_REASON="failed step marker"
  elif [[ -f "${ROOT}/${SITE}/outputs/clipped/slope_pct.tif" ]]; then
    AUTO_START_AT="longestflowpath.py"
    AUTO_START_REASON="existing slope output"
  elif [[ -f "${ROOT}/${SITE}/outputs/clipped/cn.tif" ]]; then
    AUTO_START_AT="zonal_cn.py"
    AUTO_START_REASON="existing CN raster"
  elif [[ -f "${ROOT}/${SITE}/outputs/clipped/landcover_aligned.tif" && -f "${ROOT}/${SITE}/outputs/clipped/hsg_aligned.tif" ]]; then
    AUTO_START_AT="buildcnraster.py"
    AUTO_START_REASON="existing aligned CN grids"
  elif [[ -f "${ROOT}/${SITE}/outputs/clipped/nlcd_${NLCD_YEAR}_${SITE}_wsclip.tif" && -f "${ROOT}/${SITE}/outputs/clipped/hsg_wsclip.tif" ]]; then
    AUTO_START_AT="prepcngrid.py"
    AUTO_START_REASON="existing clipped CN inputs"
  elif [[ -f "${ROOT}/${SITE}/landcover/nlcd_${NLCD_YEAR}_${SITE}.tif" && -f "${ROOT}/${SITE}/soils/hsg.tif" ]]; then
    AUTO_START_AT="load_cn_inputs.py"
    AUTO_START_REASON="materialized CN inputs"
  fi

  if [[ -n "${AUTO_START_AT}" ]]; then
    requested_index="$(phase2_step_index "${REQUESTED_START_AT}")"
    auto_index="$(phase2_step_index "${AUTO_START_AT}")"
    if [[ -z "${REQUESTED_START_AT}" || "${auto_index}" -gt "${requested_index}" ]]; then
      START_AT="${AUTO_START_AT}"
      if [[ -n "${REQUESTED_START_AT}" ]]; then
        printf 'Auto-advancing Phase 2 resume from requested %s to %s (%s).\n' "${REQUESTED_START_AT}" "${START_AT}" "${AUTO_START_REASON}"
      else
        printf 'Auto-resuming Phase 2 from %s: %s\n' "${AUTO_START_REASON}" "${START_AT}"
      fi
    fi
  fi
fi

DOCTOR_CMD=("${PYTHON_BIN}" -m ohqbuilder.cli doctor --strict-gis)
DOWNLOAD_CMD=(
  "${PYTHON_BIN}" -m ohqbuilder.cli download-data
  "${RUN_CSV_PATH}"
  "${DOWNLOAD_SUMMARY}"
  --id-col "${ID_COL}"
  --products "${PRODUCTS}"
  --download "${RAW_DOWNLOAD_DIR}"
  --buffer "${BUFFER}"
  --make-points
  --points-dir "${POINTS_DIR}"
  --max-tiles "${MAX_TILES}"
  --max-file-size-mb "${MAX_FILE_SIZE_MB}"
  --dem-resolution "${DEM_RESOLUTION}"
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
if [[ -z "${MATERIALIZE_CLIP_BOUNDS}" && "${DRY_RUN:-0}" != "1" && "${MATERIALIZE_BOUNDS_SOURCE}" != "buffer" && -n "${LAT}" && -n "${LON}" ]]; then
  if RESOLVED_BOUNDS="$(${PYTHON_BIN} -m ohqbuilder.cli watershed-bounds --lat "${LAT}" --lon "${LON}" --buffer "${BUFFER}" --safety-scale "${MATERIALIZE_SAFETY_MARGIN}" 2>/dev/null)"; then
    MATERIALIZE_CLIP_BOUNDS="${RESOLVED_BOUNDS}"
    printf 'Resolved watershed materialization bounds: %s
' "${MATERIALIZE_CLIP_BOUNDS}"
  else
    printf 'WARNING: web watershed-boundary lookup failed; falling back to coordinate buffer.
' >&2
  fi
fi

MATERIALIZE_CMD=(
  "${PYTHON_BIN}" -m ohqbuilder.cli materialize-inputs
  --root "${ROOT}"
  --site "${SITE}"
  --source-dir "${RAW_DOWNLOAD_DIR}"
  --target-crs "${TARGET_CRS}"
)
if [[ -n "${MATERIALIZE_CLIP_BOUNDS}" ]]; then
  MATERIALIZE_CMD+=(
    "--clip-bounds=${MATERIALIZE_CLIP_BOUNDS}"
    "--clip-bounds-crs=${MATERIALIZE_CLIP_BOUNDS_CRS}"
  )
elif [[ -n "${LAT}" && -n "${LON}" ]]; then
  MATERIALIZE_CMD+=(
    --clip-center-lat "${LAT}"
    --clip-center-lon "${LON}"
    --clip-buffer "${BUFFER}"
    --clip-buffer-scale "${MATERIALIZE_SAFETY_MARGIN}"
  )
fi
HYDROLOGY_CMD=(
  "${PYTHON_BIN}" -m ohqbuilder.cli prepare-hydrology
  --root "${ROOT}"
  --site "${SITE}"
)
PREPARE_PHASE1_CMD=(
  "${PYTHON_BIN}" -m ohqbuilder.cli prepare-inputs
  --root "${ROOT}"
  --site "${SITE}"
  --phase phase1
)
PREPARE_PHASE2_CMD=(
  "${PYTHON_BIN}" -m ohqbuilder.cli prepare-inputs
  --root "${ROOT}"
  --site "${SITE}"
  --phase phase2
)
if [[ -n "${START_AT}" ]]; then
  PREPARE_PHASE1_CMD+=(--start-at "${START_AT}")
  PREPARE_PHASE2_CMD+=(--start-at "${START_AT}")
fi
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

printf 'Watershed coordinate: id=%s lat=%s lon=%s\n' "${SITE_ID}" "${LAT}" "${LON}"
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
    printf '  %q' "${MATERIALIZE_CMD[@]}"; printf '\n'
    printf '  %q' "${HYDROLOGY_CMD[@]}"; printf '\n'
    printf '  %q' "${PREPARE_PHASE1_CMD[@]}"; printf '\n'
    printf '  %q' "${HSG_CMD[@]}"; printf '\n'
    printf '  %q' "${TEXTURE_CMD[@]}"; printf '\n'
    printf '  %q' "${PREPARE_PHASE2_CMD[@]}"; printf '\n'
    printf '  %q' "${BUILD_CMD[@]}"; printf '\n'
  elif [[ "${RUN_MODE}" == "resume-phase2" ]]; then
    printf '  %q' "${MATERIALIZE_CMD[@]}"; printf '\n'
    printf '  %q' "${HSG_CMD[@]}"; printf '\n'
    printf '  %q' "${TEXTURE_CMD[@]}"; printf '\n'
    printf '  %q' "${PREPARE_PHASE2_CMD[@]}"; printf '\n'
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
required = {"demlr", "hydro"}
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
  "${MATERIALIZE_CMD[@]}"
  "${HYDROLOGY_CMD[@]}"
  "${PREPARE_PHASE1_CMD[@]}"
  "${HSG_CMD[@]}"
  "${TEXTURE_CMD[@]}"
  "${PREPARE_PHASE2_CMD[@]}"
  "${BUILD_CMD[@]}"
elif [[ "${RUN_MODE}" == "resume-phase2" ]]; then
  "${MATERIALIZE_CMD[@]}"
  "${HSG_CMD[@]}"
  "${TEXTURE_CMD[@]}"
  "${PREPARE_PHASE2_CMD[@]}"
  "${BUILD_CMD[@]}"
else
  printf 'Unknown RUN_MODE: %s\n' "${RUN_MODE}" >&2
  exit 2
fi

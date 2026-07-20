#!/usr/bin/env bash
# Sligo Creek convenience wrapper for the generic watershed runner.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export CSV_PATH="${CSV_PATH:-${REPO_ROOT}/sligo_creek.csv}"
export ROW_ID="${ROW_ID:-SligoCreek_Mouth}"
export SITE="${SITE:-SligoCreek}"
export PROJECT_NAME="${PROJECT_NAME:-SligoCreek}"

exec "${SCRIPT_DIR}/run_watershed_full.sh"

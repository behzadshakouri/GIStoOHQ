#!/usr/bin/env bash
set -euo pipefail

profile="${1:-${QGIS_PROFILE:-default}}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
plugin_source="$repo_root/qgis_plugin/gistoohq_dem_workflow"

# Recreate/verify the development link and remove bytecode that may still refer
# to classes from an older checkout. Source files remain authoritative.
"$repo_root/scripts/install_qgis_plugin.sh" "$profile"
find "$plugin_source" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$plugin_source" -type f -name '*.pyc' -delete

printf '\nPlugin source and Python caches refreshed for QGIS profile “%s”.\n' "$profile"
printf 'In QGIS, disable and re-enable the plugin (or use Plugin Reloader).\n'
printf 'Restart QGIS if Python class or dock-widget changes remain cached.\n'

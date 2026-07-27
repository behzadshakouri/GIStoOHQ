#!/usr/bin/env bash
set -euo pipefail

profile="${1:-${QGIS_PROFILE:-default}}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$repo_root/qgis_plugin/gistoohq_dem_workflow"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
plugin_root="$data_home/QGIS/QGIS3/profiles/$profile/python/plugins"
destination="$plugin_root/gistoohq_dem_workflow"

mkdir -p "$plugin_root"
if [[ -e "$destination" && ! -L "$destination" ]]; then
  echo "Refusing to replace non-symlink plugin directory: $destination" >&2
  exit 2
fi
ln -sfn "$source_dir" "$destination"

printf 'Installed development plugin link:\n  %s -> %s\n' "$destination" "$source_dir"
printf 'Restart QGIS, then enable “GIStoOHQ DEM Workflow” in Plugins > Manage and Install Plugins.\n'

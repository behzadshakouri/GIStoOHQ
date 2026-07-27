# GIStoOHQ QGIS plugin

The plugin provides a QGIS dock for the same DEM, hydrology, full-run, OHQ, and
HEC-HMS commands exposed by the command-line application. QGIS 3.28 or newer is
required.

## Install from a source checkout

Install GIStoOHQ into the Python environment used by QGIS, verify its GIS
dependencies, and link the plugin into the default QGIS profile:

```bash
cd /path/to/GIStoOHQ
python -m pip install -e '.[gis]'
ohqbuild doctor --strict-gis
scripts/install_qgis_plugin.sh
```

Pass a profile name when it is not `default`, for example
`scripts/install_qgis_plugin.sh field-work`.

Restart QGIS, open **Plugins > Manage and Install Plugins > Installed**, and
enable **GIStoOHQ DEM Workflow**. Open the panel from **Plugins > GIStoOHQ >
GIStoOHQ DEM Workflow** or its toolbar button. Load or browse to a workflow YAML,
pick an outlet from the active map canvas, and use **Full Run** for the end-to-end
workflow or the individual stage buttons for review between steps.

The plugin runs `ohqbuild` as a child process. If QGIS was started from a desktop
menu and cannot find it, either install GIStoOHQ into QGIS's Python environment or
start QGIS from a shell where `command -v ohqbuild` succeeds.

## Basemaps in QGIS

The plugin uses the active QGIS canvas, so any licensed or configured basemap can
be used for outlet selection. Add a provider under **Browser > XYZ Tiles > New
Connection**, or install/configure the provider's supported QGIS integration.
Satellite imagery is also available directly in the standalone Tk launcher's
basemap menu without requiring an unofficial Google tile endpoint.

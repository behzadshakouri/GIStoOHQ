# GIStoOHQ QGIS plugin

The plugin provides a QGIS dock for the same DEM, hydrology, full-run, OHQ,
HEC-HMS, and reviewed pour-point promotion workflows exposed by the command-line
application. Full-run honors the project-config keys `nhdplus_snap_distance_m`
and `use_reviewed_pour_points`; dock controls can override them for the current
run. Promotion only overwrites an existing point dataset when **Overwrite promoted
points** is checked. QGIS 3.28 or newer is required.
The **Generate Upstream Pour Points** action ranks the eight flow-accumulation
cells around every junction and writes exactly ranks two and three as upstream
points; it does not place an automatic point on the junction. Run options expose
the whole-watershed and incremental-subwatershed minimum areas and the acceptable
accumulation-area ratio range.
Choose the outlet with **Pick Outlet on Map** or enter EPSG:4326 longitude and
latitude with **Set Outlet Coordinates**. Use **Use edited outlet.shp** after
manually correcting the outlet layer on the QGIS canvas; the full run will
preserve it and derive its EPSG:4326 coordinate.
Use **Pick Pour Points on Map** to add multiple interior review candidates from
the active canvas (right-click finishes and saves), or **Add Pour Point
Coordinates** to enter EPSG:4326 longitude/latitude directly. New manual points
are intentionally saved with `review_status=pending`; inspect their placement and
attributes, change accepted points to `approved`, retain exactly one required
`watershed_outlet`, and then run **Promote Reviewed Pour Points**. Check **Use
reviewed pour points** on the subsequent full run so they are not replaced.
The promotion action remains disabled until `outputs/pour_point_candidates.gpkg`
exists, and an out-of-sequence request reports the prerequisite without a traceback.
When a provider is unavailable but the project already has complete cached source
downloads and soil products, check **Offline: reuse downloads**. Full-run then
skips TNM, USDA, NOAA Atlas 14, and WBD-service requests and rematerializes from
the configured download directory. Missing cache prerequisites are reported
before GIS processing.
Online full runs retry transient provider failures and automatically switch to
the same validated cache when all reuse prerequisites are already present.

Use **Configure Documented Watershed** to record a local polygon/ArcGIS numeric
layer URL, exact watershed name, publisher, citation URL, and license in the
project configuration. Then run **Import Documented Watershed**. A successful
import loads `outputs/DocumentedWatershed_reference.gpkg`; subsequent full runs
compare it independently with the DEM boundary, WBD, and NHDPlus references.
Map images and PDFs are documentary evidence, not polygon inputs.
The dock groups map tools, processing stages, review actions, and model writers
into **Map**, **Workflow**, **Data**, **Review**, and **Model** tabs so the panel remains
usable on laptop-sized screens. Reference metadata opens in one compact dialog
instead of expanding the permanent dock or launcher form.
The optional **Data** tab creates and validates generic watershed SiteSpecs and
can download an explicitly declared HTTPS discharge, weather, PET/ET, or other
provider product into the immutable cache and asset catalog. It does not add any
requirements to **Full Run** or change the GIS-to-OHQ pipeline.

## Install from a source checkout

Create a virtual environment that can see the system QGIS packages, install
GIStoOHQ there, verify its GIS dependencies, and link the plugin into the default
QGIS profile:

```bash
cd /path/to/GIStoOHQ
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[gis]'
ohqbuild doctor --strict-gis
scripts/install_qgis_plugin.sh
```

If `/usr/bin/python` reports `No module named pip`, install the distribution's
`python3-pip` and `python3-venv` packages first. Do not use `sudo pip`; keep the
editable GIStoOHQ installation in `.venv`. In later terminal sessions, activate
the environment again with `source .venv/bin/activate`.

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

The installer creates a symbolic development link. Repository edits therefore
become available after disabling/re-enabling the plugin or restarting QGIS; the
installer does not need to be rerun after each edit.

## Update an existing development installation

Pull the current branch, refresh the editable Python package, and clear plugin
bytecode for the QGIS profile:

```bash
cd /path/to/GIStoOHQ
git pull --ff-only
source .venv/bin/activate
python -m pip install -e '.[gis]'
scripts/update_qgis_plugin.sh
```

For another profile, pass its name, for example
`scripts/update_qgis_plugin.sh field-work`. Then, in QGIS, disable and re-enable
**GIStoOHQ DEM Workflow** under **Plugins > Manage and Install Plugins >
Installed**. The Plugin Reloader plugin can perform the same reload during UI
development. Fully restart QGIS after changes to dock classes, plugin lifecycle
code, dependencies, or when a reload still shows old behavior.

Because the installation is a symbolic link, do not copy files into the QGIS
profile manually. The update script verifies the link and removes `__pycache__`
and `.pyc` files without deleting project data or generated watershed outputs.

## Basemaps in QGIS

The plugin uses the active QGIS canvas, so any licensed or configured basemap can
be used for outlet selection. Add a provider under **Browser > XYZ Tiles > New
Connection**, or install/configure the provider's supported QGIS integration.
Satellite imagery is also available directly in the standalone Tk launcher's
basemap menu without requiring an unofficial Google tile endpoint.

# GIStoOHQ QGIS plugin

The plugin provides a QGIS dock for the same DEM, hydrology, full-run, OHQ, and
HEC-HMS commands exposed by the command-line application. QGIS 3.28 or newer is
required.

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

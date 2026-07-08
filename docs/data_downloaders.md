# Data downloaders

GIStoOHQ's legacy QGIS preparation flow expects local DEM and flowline inputs
before `prepare-inputs` starts. In particular, phase 1 needs:

```text
<ROOT>/<SITE>/demlr/cliped_utm.tif
<ROOT>/<SITE>/outputs/NHDFlowline_clip.gpkg
<ROOT>/<SITE>/outputs/outlet.shp
```

## DEMDownloader / `demcheck`

The external [`ArashMassoudieh/DEMDownloader`](https://github.com/ArashMassoudieh/DEMDownloader)
project is a C++ utility named `demcheck`. Its README says it queries the USGS
TNMAccess API for the highest-resolution available data for CSV coordinates and
can download per-site products.

Relevant products for this workflow:

- **Elevation / 3DEP DEM**: download GeoTIFF DEM tiles, then mosaic/clip/rename
  the DEM expected by the legacy scripts to `demlr/cliped_utm.tif`.
- **Hydrography**: download NHDPlus HR / NHD flowline packages, then clip/convert
  the flowlines expected by phase 1 to `outputs/NHDFlowline_clip.gpkg`.

Example DEMDownloader commands from that project use a site-coordinate CSV and an
ID column:

```bash
./demcheck WS3_Site_Coordinates.csv --id-col "Project No."
./demcheck WS3_Site_Coordinates.csv --id-col "Project No." --products all --download ./GIS --buffer 500
```

## Current integration status

DEMDownloader is not vendored into GIStoOHQ yet. For now, use it upstream to
populate the DEM/hydrography files above, then run:

```bash
python3 run.py config.json
```

Future integration can add a `download-data` step before `prepare-inputs` once we
standardize the coordinate CSV path, site ID column, output layout, and DEM
mosaic/clip behavior.

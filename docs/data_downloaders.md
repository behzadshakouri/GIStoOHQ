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

## Python `download-data` helper

GIStoOHQ includes a Python helper inspired by DEMDownloader for users who do not
want to build the C++ `demcheck` binary. It queries the USGS TNMAccess products
API for each WGS84 coordinate in a CSV, chooses the first available tier in the
same priority order as DEMDownloader, and can optionally download matching files
into per-site folders.

This Python implementation is the downloader used by `fetch-phase1-inputs` and
`full-run`. The external C++ repository is therefore optional reference tooling,
not a runtime dependency of GIStoOHQ.

```bash
ohqbuild download-data WS3_Site_Coordinates.csv download_summary.csv \
  --id-col "Project No." \
  --products all \
  --download ./GIS \
  --buffer 500
```

The helper writes a summary CSV with one row per site/product and, when
`--download` is supplied, stores files as:

```text
GIS/<SITE_ID>/dem/
GIS/<SITE_ID>/hydro/
```

The downloaded products are source data. You still need to mosaic, project, clip,
and/or convert them into the exact legacy input filenames before `prepare-inputs`:

```text
<ROOT>/<SITE>/demlr/cliped_utm.tif
<ROOT>/<SITE>/outputs/NHDFlowline_clip.gpkg
<ROOT>/<SITE>/outputs/outlet.shp
```

For direct command-line help, run:

```bash
ohqbuild download-data --help
```

## Phase-1 bootstrap helper

For the common failure where `prepare-inputs` stops because phase-1 source files
are missing, use `fetch-phase1-inputs` from the project environment. It creates
the site input folders, writes a single-feature WGS84 `outputs/outlet.shp`,
downloads source DEM/hydrography products into a staging folder, and writes a
`PHASE1_INPUTS.md` manifest explaining the remaining GIS conversion steps.

```bash
ohqbuild fetch-phase1-inputs \
  --root /mnt/3rd900/Projects/GIStoOHQ \
  --site . \
  --lat 35.1234 \
  --lon -111.1234 \
  --products all \
  --buffer 500
```

This command is the best starting point when the error mentions missing
`outputs/outlet.shp`, `demlr/cliped_utm.tif`, or
`outputs/NHDFlowline_clip.gpkg`. It can create `outputs/outlet.shp` directly; the
DEM and hydrography downloads remain source products that must be
mosaicked/reprojected/clipped or extracted into the exact legacy filenames.

## DEM materialization helper

After `download-data` or `fetch-phase1-inputs` has staged DEM GeoTIFFs or zip
archives, `materialize-dem` can mosaic and reproject those rasters into the
legacy DEM filename expected by phase 1:

```bash
ohqbuild materialize-dem \
  --root /mnt/3rd900/Projects/GIStoOHQ \
  --site . \
  --source-dir /mnt/3rd900/Projects/GIStoOHQ/source_downloads \
  --dst-crs EPSG:26912
```

If `--dst-crs` is omitted, the command infers a UTM zone from the raster center.
This command requires `rasterio`, which is included in the `gis` optional
dependencies. Install those with:

```bash
pip install -e .[gis]
```

## Current integration status

The built-in helpers cover TNM lookup, raw downloads, input-folder creation,
outlet shapefile creation, and DEM mosaic/reprojection into `demlr/cliped_utm.tif`.
They intentionally do not yet perform NHD flowline extraction or watershed
clipping; those remain explicit GIS preparation steps before running:

```bash
python3 run.py config.json
```

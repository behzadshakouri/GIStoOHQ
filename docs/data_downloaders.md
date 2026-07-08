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

## Current integration status

The built-in helper covers TNM lookup and raw downloads. It intentionally does
not yet perform DEM mosaicking/reprojection, NHD flowline extraction, watershed
clipping, or outlet placement; those remain explicit GIS preparation steps before
running:

```bash
python3 run.py config.json
```

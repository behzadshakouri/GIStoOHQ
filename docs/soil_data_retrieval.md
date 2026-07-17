# Soil Data Retrieval

GIStoOHQ includes USDA Soil Data Access (SDA) entry points for SSURGO-derived
soil layers used by hydrologic parameter generation.

## Source

- USDA Soil Data Access (SDA)

## Commands

Hydrologic soil groups:

```bash
ohqbuild download-hsg \
  --root /path/to/project \
  --site WS3_GIS/Site_A \
  --buffer 5000 \
  --pixel-size 0.0003
```

Soil texture:

```bash
ohqbuild download-texture \
  --root /path/to/project \
  --site WS3_GIS/Site_A \
  --buffer 5000 \
  --pixel-size 0.0003 \
  --top-depth 30
```

The standalone script wrappers use the same arguments:

```bash
python scripts/retrieve_hydrologic_soil_groups.py --root /path/to/project --site WS3_GIS/Site_A
python scripts/retrieve_soil_texture.py --root /path/to/project --site WS3_GIS/Site_A
```

## Outputs

```text
WS3_GIS/Site_A/soils/
    hydrologic_soil_groups.gpkg
    hsg.tif
    soil_texture.gpkg
    texture_code.tif
    sand_pct.tif
    silt_pct.tif
    clay_pct.tif
```

## Uses

- Curve Number generation
- Green-Ampt parameter estimation
- Hydraulic conductivity estimation
- Soil property assignment in OHQ inputs

## Pipeline integration

`run.py` can insert the soil retrieval steps between `prepare-inputs` and
`check-inputs` when enabled in `config.json`:

```json
{
  "download_hsg": true,
  "download_texture": true,
  "soil_buffer": 5000,
  "soil_pixel_size": 0.0003,
  "soil_top_depth": 30
}
```

Conceptually, the expanded GIS-preparation order is:

```text
prepare-inputs
  ↓
download-dem / materialize-dem
  ↓
download-landcover
  ↓
download-hsg
  ↓
download-texture
  ↓
curve-number
  ↓
subwatersheds
  ↓
build
```

`download-landcover` in this conceptual sequence is not an implemented
GIStoOHQ command yet. See the downloader inventory in
[`data_downloaders.md`](data_downloaders.md) for the distinction between
available Python commands and products that exist only in the vendored C++
reference implementation.

## Shared USDA helper

Shared SDA behavior lives in `ohqbuilder.usda`, including:

- `query_sda(sql)`
- `bbox_wkt(bounds)`
- `sanitize(value)`
- `post_json(url, payload)` with retry handling

## Future SSURGO layers

The shared SDA helper provides a foundation for adding additional SSURGO-derived
layers such as saturated hydraulic conductivity (Ksat), available water capacity
(AWC), bulk density, organic matter, soil depth, rock fragment content, and
K-factor erosion parameters.

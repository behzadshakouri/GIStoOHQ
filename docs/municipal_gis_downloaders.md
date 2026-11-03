# Generic municipal GIS downloaders

`ohqbuilder/sources/` holds generic, provider-neutral downloaders for public
municipal GIS data: ArcGIS REST feature services (MapServer/FeatureServer) and
Socrata open-data portals. They are a separate, simpler layer from the
SiteSpec/catalog-based "optional watershed data" pipeline described in
[`architecture.md`](architecture.md) -- there is no site, no content-addressed
object store, and no provenance catalog here, just: discover what a service
offers, select layers by name, page through every record, and write a
GeoPackage with a manifest recording exactly what was asked for and what came
back.

**Scope boundary**: these modules do not know about any specific
municipality's layer catalog, stormwater topology, pipe/inlet classification,
or OHQ model elements. A planned follow-up stage will take the GeoPackages and
manifests these downloaders produce and turn reviewed layers into a validated
hydraulic network, then map that network into OHQ elements -- that work is not
part of this layer and does not change `topology.gpkg`'s existing schema.

## ArcGIS REST feature services

```bash
ohqbuild download-arcgis \
  --service-url https://sampleserver6.arcgisonline.com/arcgis/rest/services/Census/MapServer \
  --names "Counties" \
  --output counties.gpkg
```

- `--names` selects one or more layers by their exact, case-insensitive name as
  reported by the service's own metadata; `--pattern` selects by regular
  expression instead. There is no layer-ID option -- IDs are an
  implementation detail of one service, not something to hardcode into a
  reusable tool. Nested group layers are discovered correctly: a layer's
  `parent_layer_id`/children are read from the service's own hierarchy, and
  group layers themselves are excluded from selection by default (a group
  layer has no features of its own to download).
- `--where` is a plain SQL predicate (default `1=1`, meaning every feature).
- `--bbox WEST SOUTH EAST NORTH` (with `--bbox-sr`, default `4326`) clips the
  query to an envelope. Omit it to download every feature in the layer.
- `--out-sr` requests a specific output EPSG code; otherwise the layer's
  native response CRS is used (ArcGIS's GeoJSON output defaults to WGS84 when
  no output SR is requested).
- `--page-size` is a *request*, not a guarantee: the downloader always honors
  the service's own `maxRecordCount` and never asks for more per page than
  the server allows. Pages are requested in ascending object-ID order when
  the layer exposes an object-ID field, and a page that fails to advance past
  records already seen raises an error instead of silently duplicating rows.
- `--manifest` is optional; it defaults to `<output>.manifest.json` next to
  the GeoPackage.

A County of Los Angeles public GIS layer is shown here only as a realistic
example invocation -- nothing above understands LA County's layer catalog or
naming conventions; the same command works against any ArcGIS Server:

```bash
ohqbuild download-arcgis \
  --service-url https://public.gis.lacounty.gov/public/rest/services/LACounty_Dynamic/Parcel/MapServer \
  --names "Parcel Boundaries" \
  --output la_parcels.gpkg
```

### Discovering layers first

Layers are often easier to select after seeing what a service actually
offers. The lower-level functions this command wraps are directly importable:

```python
from ohqbuilder.sources.arcgis_rest import discover_layers, select_layers

layers = discover_layers("https://example.gov/arcgis/rest/services/Stormwater/FeatureServer")
for layer in layers:
    print(layer.layer_id, layer.name, layer.layer_type, layer.geometry_type)

selected = select_layers(layers, pattern=r"(?i)catch.?basin")
```

## Socrata open-data portals

```bash
ohqbuild download-socrata \
  --domain data.cityofchicago.org \
  --dataset-id 22u3-xenr \
  --output chicago_dataset.gpkg
```

- `--domain` and `--dataset-id` are the portal hostname and the dataset's own
  identifier (the "four-by-four" code in the dataset's URL, for example
  `ab12-cd34`).
- Metadata (`/api/views/{id}.json`) is always fetched before any feature page,
  so the dataset's own column names/types -- including which column, if any,
  is spatial -- are known before downloading.
- `--bbox WEST SOUTH EAST NORTH` filters using the dataset's own spatial
  column via a SoQL `within_box(...)` clause. If the dataset declares no
  spatial column, a bounding box request fails loudly rather than silently
  returning an unfiltered result.
- Pagination uses SoQL `$limit`/`$offset`, ordered by `:id` by default (or
  `--order-by` for a different stable column) so pages are deterministic.
- `--app-token` sets the optional `X-App-Token` header for portals that
  throttle anonymous requests.
- Socrata's geospatial export is always WGS84 (`EPSG:4326`); there is no
  reprojection parameter to request another CRS.

## The provenance manifest

Both downloaders write the same manifest shape (`ohqbuilder/sources/manifest.py`),
by default at `<output>.manifest.json`:

```json
{
  "provider": "arcgis_rest",
  "source": "https://example.gov/arcgis/rest/services/Stormwater/FeatureServer",
  "output_path": "stormwater.gpkg",
  "downloaded_at": "2026-01-01T00:00:00Z",
  "layers": [
    {
      "output_layer": "catch_basins_1",
      "source_url": "https://example.gov/arcgis/rest/services/Stormwater/FeatureServer/1/query",
      "query_parameters": {"where": "1=1", "outFields": "*", "f": "geojson", "orderByFields": "OBJECTID ASC"},
      "feature_count": 1842,
      "crs": "EPSG:4326",
      "layer_metadata": {"layer_id": 1, "name": "Catch Basins", "geometry_type": "esriGeometryPoint"}
    }
  ]
}
```

`query_parameters` is the stable request template (not a specific page), so
re-running the same command is reproducible even though pagination itself is
internal. Review the manifest before treating any downloaded layer as an
input to further work -- it is the record of exactly what was asked for and
what came back, not a quality judgment about the source data.

## Attribute and null handling

Both downloaders pass attribute values through exactly as the provider
returned them, including `null`/`None`. Nothing in this layer fabricates a
missing value, fills a default, or otherwise repairs source data -- if a
downstream step needs to handle nulls, that is a decision for that step to
make explicitly, with the manifest and GeoPackage as the unmodified record of
what the source actually contained.

## Empty layers and layers with no matching records

A selected layer that returns zero features for the given `--where`/`--bbox`
still gets a layer in the output GeoPackage, with columns taken from the
service's own field metadata, rather than being silently skipped. The
manifest records `"feature_count": 0` for it like any other layer.

## Testing without live network access

`tests/test_arcgis_rest.py`, `tests/test_socrata.py`, and
`tests/test_sources_manifest.py` mock every HTTP response (layer discovery,
multi-page/duplicate-page/empty-page scenarios, bounding-box parameters, null
attributes, transient-failure retries, and manifest contents) and never touch
the network; they run as part of the normal `pytest` suite. For an optional,
real-network sanity check against a live service, see
`scripts/live_smoke_check_sources.py --help` -- it is not part of the
automated test suite and must be run by hand.

## What this layer does not do

- No stormwater network snapping, flow-direction assignment, pipe topology,
  DEM-based direction logic, or OHQ element generation. That is a separate,
  future stage that consumes this layer's GeoPackages/manifests.
- No change to `topology.gpkg`'s existing schema or meaning.
- No jurisdiction-specific layer classification baked into the reusable
  modules -- only generic discovery/selection/paging/writing.

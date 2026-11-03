#!/usr/bin/env python3
"""Optional live smoke check for the generic GIS source downloaders.

This makes REAL network calls against a live ArcGIS REST service or Socrata
portal. It is intentionally kept out of the pytest suite (``tests/`` never
imports or runs this file) -- run it by hand when you want to confirm the
downloaders still work against a real, currently-online service, for example
after a long gap or before relying on a specific portal.

It only discovers/lists metadata by default; add --download to also page
through features and write a small GeoPackage + manifest so you can inspect
real output.

Examples
--------
List layers on a public ArcGIS MapServer/FeatureServer, no download:

    python3 scripts/live_smoke_check_sources.py arcgis \\
        --service-url https://sampleserver6.arcgisonline.com/arcgis/rest/services/Census/MapServer

Download one named layer from a real municipal ArcGIS service (LA County's
public GIS portal, shown here only as a documentation example -- nothing in
the downloader modules themselves knows about this or any other portal):

    python3 scripts/live_smoke_check_sources.py arcgis \\
        --service-url https://public.gis.lacounty.gov/public/rest/services/LACounty_Dynamic/Parcel/MapServer \\
        --names "Parcel Boundaries" \\
        --download --output /tmp/la_parcels_smoke.gpkg

Check Socrata dataset metadata, no download:

    python3 scripts/live_smoke_check_sources.py socrata \\
        --domain data.cityofchicago.org --dataset-id 22u3-xenr
"""

from __future__ import annotations

import argparse
import sys

from ohqbuilder.sources.arcgis_rest import (
    ArcGISRestError,
    discover_layers,
    download_arcgis_layers,
)
from ohqbuilder.sources.socrata import (
    SocrataError,
    download_socrata_dataset,
    fetch_dataset_metadata,
    geo_column_name,
)


def _run_arcgis(args: argparse.Namespace) -> int:
    layers = discover_layers(args.service_url, timeout=args.timeout)
    print(f"Discovered {len(layers)} layer(s)/table(s) at {args.service_url}:")
    for layer in layers:
        parent = f" (child of {layer.parent_layer_id})" if layer.parent_layer_id is not None else ""
        print(f"  [{layer.layer_id}] {layer.name!r} type={layer.layer_type} "
              f"geometry={layer.geometry_type}{parent}")
    if not args.download:
        return 0
    if not args.names and not args.pattern:
        print("live smoke check: --download requires --names or --pattern", file=sys.stderr)
        return 2
    manifest = download_arcgis_layers(
        args.service_url, args.output, names=args.names, pattern=args.pattern,
        timeout=args.timeout,
    )
    for record in manifest["layers"]:
        print(f"  wrote {record['output_layer']}: {record['feature_count']} feature(s)")
    print(f"Wrote {args.output}")
    return 0


def _run_socrata(args: argparse.Namespace) -> int:
    metadata = fetch_dataset_metadata(args.domain, args.dataset_id, timeout=args.timeout)
    print(f"Dataset {args.dataset_id!r} on {args.domain}: {metadata.get('name')!r}")
    print(f"  spatial column: {geo_column_name(metadata)}")
    print(f"  columns: {[c.get('fieldName') for c in metadata.get('columns') or []]}")
    if not args.download:
        return 0
    manifest = download_socrata_dataset(
        args.domain, args.dataset_id, args.output, timeout=args.timeout,
    )
    for record in manifest["layers"]:
        print(f"  wrote {record['output_layer']}: {record['feature_count']} feature(s)")
    print(f"Wrote {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="provider", required=True)

    arcgis = sub.add_parser("arcgis", help="Live-check an ArcGIS REST MapServer/FeatureServer.")
    arcgis.add_argument("--service-url", required=True)
    arcgis.add_argument("--names", nargs="+", default=None)
    arcgis.add_argument("--pattern", default=None)
    arcgis.add_argument("--download", action="store_true", help="Also page through and write a GeoPackage.")
    arcgis.add_argument("--output", default="/tmp/live_smoke_check_arcgis.gpkg")
    arcgis.add_argument("--timeout", type=float, default=30.0)

    socrata = sub.add_parser("socrata", help="Live-check a Socrata domain/dataset.")
    socrata.add_argument("--domain", required=True)
    socrata.add_argument("--dataset-id", required=True)
    socrata.add_argument("--download", action="store_true", help="Also page through and write a GeoPackage.")
    socrata.add_argument("--output", default="/tmp/live_smoke_check_socrata.gpkg")
    socrata.add_argument("--timeout", type=float, default=30.0)

    args = parser.parse_args(argv)
    try:
        if args.provider == "arcgis":
            return _run_arcgis(args)
        return _run_socrata(args)
    except (ArcGISRestError, SocrataError, ValueError, OSError, ImportError) as exc:
        print(f"live smoke check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

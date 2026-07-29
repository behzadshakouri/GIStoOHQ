from __future__ import annotations

import json
from pathlib import Path


class WatershedComparisonError(RuntimeError):
    """Raised when watershed/reference geometry cannot be compared reliably."""


def compare_watersheds(
    generated_path: str | Path,
    reference_path: str | Path,
    output_path: str | Path,
) -> Path:
    """Compare a generated basin with every intersecting WBD feature.

    Areas and distances are calculated in a locally estimated UTM CRS. Reporting
    every reference feature avoids pretending that the first containing HUC12 is
    necessarily the named-stream watershed.
    """

    import geopandas as gpd

    generated = gpd.read_file(generated_path)
    reference = gpd.read_file(reference_path, layer="WBDHU12_reference")
    if generated.empty or reference.empty:
        raise WatershedComparisonError("Generated watershed and WBD reference must be non-empty")
    if generated.crs is None or reference.crs is None:
        raise WatershedComparisonError("Generated watershed and WBD reference must define a CRS")

    generated_wgs84 = generated.to_crs("EPSG:4326")
    metric_crs = generated_wgs84.estimate_utm_crs()
    if metric_crs is None:
        raise WatershedComparisonError("Could not determine a local projected CRS")
    basin = generated.to_crs(metric_crs).geometry.union_all()
    references = reference.to_crs(metric_crs)
    basin_area = basin.area
    if basin_area <= 0:
        raise WatershedComparisonError("Generated watershed has zero area")

    comparisons = []
    for index, row in references.iterrows():
        geometry = row.geometry
        intersection_area = basin.intersection(geometry).area
        union_area = basin.union(geometry).area
        reference_area = geometry.area
        huc12 = next(
            (str(row[name]) for name in row.index if str(name).lower() == "huc12"),
            str(index),
        )
        comparisons.append(
            {
                "huc12": huc12,
                "generated_area_km2": basin_area / 1_000_000.0,
                "reference_area_km2": reference_area / 1_000_000.0,
                "intersection_area_km2": intersection_area / 1_000_000.0,
                "omission_area_km2": basin.difference(geometry).area / 1_000_000.0,
                "commission_area_km2": geometry.difference(basin).area / 1_000_000.0,
                "iou": intersection_area / union_area if union_area else 0.0,
                "boundary_hausdorff_m": basin.boundary.hausdorff_distance(geometry.boundary),
            }
        )
    comparisons.sort(key=lambda item: item["iou"], reverse=True)
    payload = {
        "generated_watershed": str(Path(generated_path).expanduser().resolve()),
        "wbd_reference": str(Path(reference_path).expanduser().resolve()),
        "measurement_crs": metric_crs.to_string(),
        "interpretation": (
            "WBD HUC12 units are comparison references, not automatically the named-stream basin."
        ),
        "best_match": comparisons[0],
        "comparisons": comparisons,
    }
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target

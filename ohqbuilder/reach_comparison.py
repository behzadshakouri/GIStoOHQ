from __future__ import annotations

import json
from pathlib import Path


class ReachComparisonError(RuntimeError):
    """Raised when generated and reference reach networks cannot be compared."""


def _sampled_mean_distance(network, reference, spacing_m: float) -> float:
    """Approximate mean lateral offset with regular along-line samples."""

    import math

    geometries = list(getattr(network, "geoms", [network]))
    distances = []
    for geometry in geometries:
        if geometry.is_empty or geometry.length <= 0:
            continue
        sample_count = max(1, int(math.ceil(geometry.length / spacing_m)))
        distances.extend(
            geometry.interpolate((index + 0.5) * geometry.length / sample_count).distance(
                reference
            )
            for index in range(sample_count)
        )
    if not distances:
        raise ReachComparisonError("Reach network has no sampleable line geometry")
    return float(sum(distances) / len(distances))


def compare_reach_networks(
    generated_path: str | Path,
    reference_path: str | Path,
    output_path: str | Path,
    *,
    watershed_path: str | Path | None = None,
    tolerance_m: float = 30.0,
) -> Path:
    """Measure generated reach alignment with mapped NHD flowlines."""

    if tolerance_m <= 0:
        raise ReachComparisonError("Reach comparison tolerance must be positive")
    try:
        import geopandas as gpd
    except ImportError as exc:  # pragma: no cover - optional GIS environment
        raise ReachComparisonError(
            "Reach comparison requires `pip install -e .[gis]`."
        ) from exc

    generated = gpd.read_file(generated_path)
    reference = gpd.read_file(reference_path)
    if generated.empty or reference.empty:
        raise ReachComparisonError("Generated reaches and NHD flowlines must be non-empty")
    if generated.crs is None or reference.crs is None:
        raise ReachComparisonError("Generated reaches and NHD flowlines must define a CRS")

    metric_crs = generated.to_crs("EPSG:4326").estimate_utm_crs()
    if metric_crs is None:
        raise ReachComparisonError("Could not determine a local projected CRS")
    generated_metric = generated.to_crs(metric_crs)
    reference_metric = reference.to_crs(metric_crs)
    if watershed_path is not None:
        watershed = gpd.read_file(watershed_path)
        if watershed.empty or watershed.crs is None:
            raise ReachComparisonError("Watershed clip must be non-empty and define a CRS")
        basin = watershed.to_crs(metric_crs).geometry.union_all()
        reference_metric = reference_metric[reference_metric.intersects(basin)].copy()
        reference_metric.geometry = reference_metric.geometry.intersection(basin)
        reference_metric = reference_metric[~reference_metric.geometry.is_empty]
    if reference_metric.empty:
        raise ReachComparisonError("No NHD flowlines intersect the generated watershed")

    generated_network = generated_metric.geometry.union_all()
    reference_network = reference_metric.geometry.union_all()
    generated_length = float(generated_network.length)
    reference_length = float(reference_network.length)
    if generated_length <= 0 or reference_length <= 0:
        raise ReachComparisonError("Reach networks must have positive length")

    generated_overlap = generated_network.intersection(
        reference_network.buffer(tolerance_m)
    ).length
    reference_overlap = reference_network.intersection(
        generated_network.buffer(tolerance_m)
    ).length
    generated_to_reference_mean = _sampled_mean_distance(
        generated_network, reference_network, tolerance_m
    )
    reference_to_generated_mean = _sampled_mean_distance(
        reference_network, generated_network, tolerance_m
    )
    payload = {
        "generated_reaches": str(Path(generated_path).expanduser().resolve()),
        "reference_flowlines": str(Path(reference_path).expanduser().resolve()),
        "watershed_clip": (
            str(Path(watershed_path).expanduser().resolve()) if watershed_path else None
        ),
        "measurement_crs": metric_crs.to_string(),
        "tolerance_m": tolerance_m,
        "generated_length_km": generated_length / 1000.0,
        "reference_length_km": reference_length / 1000.0,
        "generated_within_tolerance_pct": 100.0 * generated_overlap / generated_length,
        "reference_within_tolerance_pct": 100.0 * reference_overlap / reference_length,
        "generated_to_reference_mean_offset_m": generated_to_reference_mean,
        "reference_to_generated_mean_offset_m": reference_to_generated_mean,
        "mean_lateral_offset_m": (
            generated_to_reference_mean + reference_to_generated_mean
        )
        / 2.0,
        "hausdorff_distance_m": generated_network.hausdorff_distance(reference_network),
    }
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target

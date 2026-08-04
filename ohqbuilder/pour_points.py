from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class PourPointGenerationError(RuntimeError):
    """Raised when automatic pour-point generation cannot be completed."""


@dataclass(frozen=True)
class PourPointResult:
    output_path: Path
    count: int

    @property
    def report_path(self) -> Path:
        return self.output_path.with_name("pour_points_generation_report.json")


def generate_pour_points(
    junctions_path: str | Path,
    output_path: str | Path,
    *,
    fallback_outlet_path: str | Path | None = None,
    overwrite: bool = False,
) -> PourPointResult:
    """Create Phase 2 pour points from Phase 1 junctions or a lone outlet.

    Junctions are the natural drainage locations used by the retained topology
    scripts.  Keeping their numeric IDs gives Phase 2 a deterministic mapping
    between delineated subbasins and the generated reach network. A valid
    single-reach watershed has no interior junctions, so its outlet becomes the
    sole pour point when ``fallback_outlet_path`` is supplied.
    """

    try:
        import geopandas as gpd
    except ImportError as exc:  # pragma: no cover - depends on optional environment
        raise PourPointGenerationError(
            "Automatic pour-point generation requires GIS dependencies; "
            "install them with `pip install -e .[gis]`."
        ) from exc

    source = Path(junctions_path).expanduser().resolve()
    destination = Path(output_path).expanduser().resolve()
    if not source.is_file():
        raise PourPointGenerationError(f"Phase 1 junctions file not found: {source}")
    if destination.exists() and not overwrite:
        raise PourPointGenerationError(
            f"Pour-points output already exists: {destination}; pass --overwrite to replace it."
        )

    try:
        junctions = gpd.read_file(source, layer="junctions")
    except Exception as exc:
        raise PourPointGenerationError(f"Could not read junctions from {source}: {exc}") from exc

    source_method = "phase1_junctions"
    if junctions.empty:
        if fallback_outlet_path is None:
            raise PourPointGenerationError(
                f"No junctions were found in {source}; provide a fallback outlet for a "
                "single-reach watershed."
            )
        outlet_source = Path(fallback_outlet_path).expanduser().resolve()
        if not outlet_source.is_file():
            raise PourPointGenerationError(f"Fallback outlet file not found: {outlet_source}")
        try:
            outlet = gpd.read_file(outlet_source)
        except Exception as exc:
            raise PourPointGenerationError(
                f"Could not read fallback outlet from {outlet_source}: {exc}"
            ) from exc
        if len(outlet) != 1:
            raise PourPointGenerationError(
                f"Fallback outlet must contain exactly one feature: {outlet_source}"
            )
        if outlet.crs is None:
            raise PourPointGenerationError(
                f"Fallback outlet has no coordinate reference system: {outlet_source}"
            )
        if outlet.geometry.isna().any() or not outlet.geometry.geom_type.eq("Point").all():
            raise PourPointGenerationError(
                "Fallback outlet must have one non-empty point geometry."
            )
        junctions = gpd.GeoDataFrame(
            {"junction_id": [1]}, geometry=outlet.geometry.copy(), crs=outlet.crs
        )
        source_method = "fallback_watershed_outlet"
    if "junction_id" not in junctions.columns:
        raise PourPointGenerationError(f"Missing required field 'junction_id' in {source}")
    if junctions.crs is None:
        raise PourPointGenerationError(
            f"Junctions layer has no coordinate reference system: {source}"
        )
    if not junctions.geometry.notna().all() or not junctions.geometry.geom_type.eq("Point").all():
        raise PourPointGenerationError("Every junction must have a non-empty point geometry.")

    ids = junctions["junction_id"]
    if ids.isna().any() or ids.duplicated().any():
        raise PourPointGenerationError("Junction IDs must be populated and unique.")
    try:
        numeric_ids = ids.astype(int)
    except (TypeError, ValueError) as exc:
        raise PourPointGenerationError("Junction IDs must be integers.") from exc

    pour_points = gpd.GeoDataFrame(
        {
            "id": numeric_ids,
            "name": [f"P{junction_id}" for junction_id in numeric_ids],
        },
        geometry=junctions.geometry.copy(),
        crs=junctions.crs,
    ).sort_values("id")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if overwrite and destination.suffix.lower() == ".shp":
        for component in destination.parent.glob(f"{destination.stem}.*"):
            component.unlink()
    try:
        pour_points.to_file(destination)
    except Exception as exc:
        raise PourPointGenerationError(
            f"Could not write pour points to {destination}: {exc}"
        ) from exc
    report_path = destination.with_name("pour_points_generation_report.json")
    report = {
        "method": source_method,
        "description": (
            "Each Phase 1 reach-network junction becomes a Phase 2 pour point. "
            "A watershed with no interior junctions uses its single outlet instead."
        ),
        "source_path": str(source),
        "output_path": str(destination),
        "crs": str(pour_points.crs),
        "count": len(pour_points),
        "points": [
            {
                "id": int(row.id),
                "name": str(row.name),
                "x": float(row.geometry.x),
                "y": float(row.geometry.y),
            }
            for row in pour_points.itertuples()
        ],
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return PourPointResult(destination, len(pour_points))

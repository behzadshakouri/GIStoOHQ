from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class PourPointGenerationError(RuntimeError):
    """Raised when automatic pour-point generation cannot be completed."""


@dataclass(frozen=True)
class PourPointResult:
    output_path: Path
    count: int


def generate_pour_points(
    junctions_path: str | Path,
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> PourPointResult:
    """Create Phase 2 pour points from every Phase 1 junction.

    Junctions are the natural drainage locations used by the retained topology
    scripts.  Keeping their numeric IDs gives Phase 2 a deterministic mapping
    between delineated subbasins and the generated reach network.
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

    if junctions.empty:
        raise PourPointGenerationError(f"No junctions were found in {source}")
    if "junction_id" not in junctions.columns:
        raise PourPointGenerationError(f"Missing required field 'junction_id' in {source}")
    if junctions.crs is None:
        raise PourPointGenerationError(f"Junctions layer has no coordinate reference system: {source}")
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
        raise PourPointGenerationError(f"Could not write pour points to {destination}: {exc}") from exc
    return PourPointResult(destination, len(pour_points))

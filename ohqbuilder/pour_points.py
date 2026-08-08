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
    flow_accumulation_path: str | Path | None = None,
    fallback_outlet_path: str | Path | None = None,
    overwrite: bool = False,
) -> PourPointResult:
    """Create Phase 2 pour points on the two upstream cells at each junction.

    The eight raster cells surrounding a junction are ranked by absolute flow
    accumulation.  The strongest cell is the downstream reach, so ranks two
    and three are emitted as the two upstream pour points. A valid
    watershed outlet is always included so the incremental subwatersheds cover
    the reach between the last interior junction and the modeled outlet. A
    single-reach watershed therefore contains only its outlet point.
    """

    try:
        import geopandas as gpd
        import numpy as np
        import rasterio
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
        raise PourPointGenerationError(
            f"Could not read junctions from {source}: {exc}"
        ) from exc

    if fallback_outlet_path is None:
        raise PourPointGenerationError(
            "Automatic pour-point generation requires the modeled watershed outlet."
        )
    outlet_source = Path(fallback_outlet_path).expanduser().resolve()
    if not outlet_source.is_file():
        raise PourPointGenerationError(
            f"Watershed outlet file not found: {outlet_source}"
        )
    try:
        outlet = gpd.read_file(outlet_source)
    except Exception as exc:
        raise PourPointGenerationError(
            f"Could not read watershed outlet from {outlet_source}: {exc}"
        ) from exc
    if len(outlet) != 1:
        raise PourPointGenerationError(
            f"Watershed outlet must contain exactly one feature: {outlet_source}"
        )
    if outlet.crs is None:
        raise PourPointGenerationError(
            f"Watershed outlet has no coordinate reference system: {outlet_source}"
        )
    if outlet.geometry.isna().any() or not outlet.geometry.geom_type.eq("Point").all():
        raise PourPointGenerationError(
            "Watershed outlet must have one non-empty point geometry."
        )

    if junctions.empty:
        junctions = gpd.GeoDataFrame({"junction_id": []}, geometry=[], crs=outlet.crs)
    if "junction_id" not in junctions.columns:
        raise PourPointGenerationError(
            f"Missing required field 'junction_id' in {source}"
        )
    if junctions.crs is None:
        raise PourPointGenerationError(
            f"Junctions layer has no coordinate reference system: {source}"
        )
    if (
        not junctions.geometry.notna().all()
        or not junctions.geometry.geom_type.eq("Point").all()
    ):
        raise PourPointGenerationError(
            "Every junction must have a non-empty point geometry."
        )

    ids = junctions["junction_id"]
    if ids.isna().any() or ids.duplicated().any():
        raise PourPointGenerationError("Junction IDs must be populated and unique.")
    try:
        numeric_ids = ids.astype(int)
    except (TypeError, ValueError) as exc:
        raise PourPointGenerationError("Junction IDs must be integers.") from exc

    if outlet.crs != junctions.crs:
        outlet = outlet.to_crs(junctions.crs)

    point_geometries = []
    point_names = []
    point_roles = []
    point_junction_ids = []
    point_ranks = []
    if len(junctions):
        if flow_accumulation_path is None:
            raise PourPointGenerationError(
                "Junction-based pour-point generation requires flow_acc.tif."
            )
        raster_path = Path(flow_accumulation_path).expanduser().resolve()
        if not raster_path.is_file():
            raise PourPointGenerationError(
                f"Flow-accumulation raster not found: {raster_path}"
            )
        from shapely.geometry import Point

        try:
            with rasterio.open(raster_path) as raster:
                if raster.crs is None:
                    raise PourPointGenerationError(
                        f"Flow-accumulation raster has no CRS: {raster_path}"
                    )
                raster_junctions = junctions.to_crs(raster.crs)
                band = raster.read(1, masked=True)
                for junction_id, geometry in zip(
                    numeric_ids, raster_junctions.geometry
                ):
                    row, col = raster.index(geometry.x, geometry.y)
                    candidates = []
                    for row_offset in (-1, 0, 1):
                        for col_offset in (-1, 0, 1):
                            if row_offset == 0 and col_offset == 0:
                                continue
                            candidate_row = row + row_offset
                            candidate_col = col + col_offset
                            if not (
                                0 <= candidate_row < raster.height
                                and 0 <= candidate_col < raster.width
                            ):
                                continue
                            value = band[candidate_row, candidate_col]
                            if np.ma.is_masked(value) or not np.isfinite(value):
                                continue
                            candidates.append(
                                (abs(float(value)), candidate_row, candidate_col)
                            )
                    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
                    if len(candidates) < 3:
                        raise PourPointGenerationError(
                            f"Junction {int(junction_id)} has fewer than three valid surrounding "
                            "flow-accumulation cells."
                        )
                    # Rank one follows the downstream reach. Ranks two and three
                    # occupy the tributaries immediately upstream of the junction.
                    for rank, (_, candidate_row, candidate_col) in enumerate(
                        candidates[1:3], start=2
                    ):
                        x, y = raster.xy(candidate_row, candidate_col)
                        point_geometries.append(Point(x, y))
                        point_names.append(f"J{int(junction_id)}_U{rank - 1}")
                        point_roles.append("upstream_junction")
                        point_junction_ids.append(int(junction_id))
                        point_ranks.append(rank)
        except PourPointGenerationError:
            raise
        except Exception as exc:
            raise PourPointGenerationError(
                f"Could not rank cells around junctions using {raster_path}: {exc}"
            ) from exc

        ranked = gpd.GeoDataFrame(
            {
                "name": point_names,
                "role": point_roles,
                "junction": point_junction_ids,
                "acc_rank": point_ranks,
            },
            geometry=point_geometries,
            crs=raster.crs,
        ).to_crs(junctions.crs)
    else:
        ranked = gpd.GeoDataFrame(
            {"name": [], "role": [], "junction": [], "acc_rank": []},
            geometry=[],
            crs=junctions.crs,
        )

    outlet_id = len(ranked) + 1
    pour_points = gpd.GeoDataFrame(
        {
            "id": [*range(1, outlet_id), outlet_id],
            "name": [*ranked["name"].tolist(), "WatershedOutlet"],
            "role": [*ranked["role"].tolist(), "watershed_outlet"],
            "junction": [*ranked["junction"].tolist(), None],
            "acc_rank": [*ranked["acc_rank"].tolist(), None],
        },
        geometry=[*ranked.geometry.tolist(), outlet.geometry.iloc[0]],
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
        "method": "ranked_eight_cell_junction_neighborhood_plus_watershed_outlet",
        "description": (
            "For each junction, the eight neighboring flow-accumulation cells are "
            "ranked; ranks two and three are upstream pour points and rank one is "
            "the excluded downstream cell. "
            "The modeled watershed outlet is always included so the partition covers "
            "the complete downstream drainage area."
        ),
        "source_path": str(source),
        "outlet_source_path": str(outlet_source),
        "output_path": str(destination),
        "crs": str(pour_points.crs),
        "count": len(pour_points),
        "points": [
            {
                "id": int(row.id),
                "name": str(row.name),
                "role": str(row.role),
                "x": float(row.geometry.x),
                "y": float(row.geometry.y),
            }
            for row in pour_points.itertuples()
        ],
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return PourPointResult(destination, len(pour_points))

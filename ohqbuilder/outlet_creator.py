from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class OutletCreationError(RuntimeError):
    """Raised when an outlet cannot be derived from a flow-accumulation raster."""


@dataclass(frozen=True)
class OutletCreationResult:
    output_path: Path
    x: float
    y: float
    accumulation: float


def create_outlet_from_flow_accumulation(
    flow_accumulation_path: str | Path,
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> OutletCreationResult:
    """Write an outlet at the center of the largest valid accumulation cell."""

    try:
        import geopandas as gpd
        import numpy as np
        import rasterio
        from shapely.geometry import Point
    except ImportError as exc:  # pragma: no cover - optional GIS environment
        raise OutletCreationError(
            "Automatic outlet creation requires GIS dependencies; "
            "install them with `pip install -e .[gis]`."
        ) from exc

    source = Path(flow_accumulation_path).expanduser().resolve()
    destination = Path(output_path).expanduser().resolve()
    if not source.is_file():
        raise OutletCreationError(f"Flow-accumulation raster not found: {source}")
    if destination.exists() and not overwrite:
        raise OutletCreationError(
            f"Outlet output already exists: {destination}; pass --overwrite to replace it."
        )

    try:
        with rasterio.open(source) as dataset:
            if dataset.crs is None:
                raise OutletCreationError(f"Flow-accumulation raster has no CRS: {source}")
            values = dataset.read(1, masked=True)
            valid = np.ma.masked_invalid(np.ma.abs(values))
            if valid.count() == 0:
                raise OutletCreationError(f"Flow-accumulation raster has no valid cells: {source}")
            flat_index = int(valid.argmax())
            row, column = np.unravel_index(flat_index, valid.shape)
            x, y = dataset.xy(row, column, offset="center")
            accumulation = float(valid[row, column])
            crs = dataset.crs
    except OutletCreationError:
        raise
    except Exception as exc:
        raise OutletCreationError(f"Could not read {source}: {exc}") from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    if overwrite and destination.suffix.lower() == ".shp":
        for component in destination.parent.glob(f"{destination.stem}.*"):
            component.unlink()
    outlet = gpd.GeoDataFrame(
        {"id": [1], "name": ["outlet"], "flow_acc": [accumulation]},
        geometry=[Point(x, y)],
        crs=crs,
    )
    try:
        outlet.to_file(destination)
    except Exception as exc:
        raise OutletCreationError(f"Could not write outlet to {destination}: {exc}") from exc
    return OutletCreationResult(destination, float(x), float(y), accumulation)

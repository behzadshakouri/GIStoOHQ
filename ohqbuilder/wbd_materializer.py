from __future__ import annotations

import importlib.util
import tempfile
import zipfile
from pathlib import Path


class WbdMaterializeError(RuntimeError):
    """Raised when a downloaded WBD package cannot be made review-ready."""


def _safe_extract(archive: Path, destination: Path) -> None:
    """Extract an archive without allowing members to escape the temporary directory."""

    root = destination.resolve()
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise WbdMaterializeError(
                    f"Unsafe path in WBD archive {archive.name}: {member.filename}"
                )
        zipped.extractall(destination)


def _find_huc12_source(root: Path) -> tuple[Path, str | None]:
    """Return the preferred HUC12 vector dataset and optional geodatabase layer."""

    shapefiles = sorted(root.rglob("WBDHU12.shp"))
    if shapefiles:
        return shapefiles[0], None

    import fiona

    containers = sorted(root.rglob("*.gpkg")) + sorted(
        path for path in root.rglob("*.gdb") if path.is_dir()
    )
    for path in containers:
        layers = fiona.listlayers(path)
        match = next((layer for layer in layers if layer.upper() == "WBDHU12"), None)
        if match:
            return path, match
    raise WbdMaterializeError("The downloaded WBD package does not contain a WBDHU12 layer")


def materialize_wbd_reference(
    source_dir: str | Path,
    output_path: str | Path,
    *,
    clip_bounds: tuple[float, float, float, float],
    clip_bounds_crs: str = "EPSG:4326",
) -> Path:
    """Extract and spatially subset WBD HUC12 polygons for review.

    The result is deliberately named a reference. It is never substituted for a
    DEM-delineated named-stream watershed by this function.
    """

    if importlib.util.find_spec("geopandas") is None or importlib.util.find_spec("shapely") is None:
        raise WbdMaterializeError(
            "Materializing WBD requires GIS dependencies; install with `pip install -e .[gis]`."
        )

    import geopandas as gpd
    from shapely.geometry import box

    source = Path(source_dir).expanduser().resolve()
    archives = sorted(source.glob("*.zip"))
    direct_sources = sorted(source.rglob("WBDHU12.shp"))
    direct_containers = sorted(source.glob("*.gdb")) + sorted(source.glob("*.gpkg"))
    if not archives and not direct_sources and not direct_containers:
        raise WbdMaterializeError(f"No WBD vector package found under {source}")

    with tempfile.TemporaryDirectory(prefix="gistoohq-wbd-") as temporary:
        extracted = Path(temporary)
        for index, archive in enumerate(archives):
            _safe_extract(archive, extracted / str(index))
        dataset, layer = _find_huc12_source(extracted if archives else source)
        frame = gpd.read_file(dataset, layer=layer) if layer else gpd.read_file(dataset)

    if frame.crs is None:
        raise WbdMaterializeError("WBDHU12 layer has no coordinate reference system")
    bounds_geometry = gpd.GeoSeries([box(*clip_bounds)], crs=clip_bounds_crs).to_crs(frame.crs)[0]
    selected = frame[frame.geometry.intersects(bounds_geometry)].copy()
    if selected.empty:
        raise WbdMaterializeError("No WBD HUC12 polygon intersects the materialization bounds")

    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    selected.to_file(target, layer="WBDHU12_reference", driver="GPKG")
    return target

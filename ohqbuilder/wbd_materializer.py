from __future__ import annotations

import importlib.util
import tempfile
import zipfile
from pathlib import Path
import re


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


def _normalized_layer_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _find_hu12_layer(layer_names: list[str]) -> str | None:
    preferred = {"wbdhu12", "hu12", "watershedboundarydatasethu12"}
    for name in layer_names:
        if _normalized_layer_name(name) in preferred:
            return name
    for name in layer_names:
        normalized = _normalized_layer_name(name)
        if "wbd" in normalized and "hu12" in normalized:
            return name
    return None


def _find_huc12_source(root: Path) -> tuple[Path, str | None]:
    """Return the preferred HUC12 vector dataset and optional geodatabase layer."""

    shapefiles = sorted(
        path
        for path in root.rglob("*.shp")
        if _find_hu12_layer([path.stem]) is not None
    )
    if shapefiles:
        return shapefiles[0], None

    import fiona

    containers = sorted(root.rglob("*.gpkg")) + sorted(
        path for path in root.rglob("*.gdb") if path.is_dir()
    )
    available = []
    for path in containers:
        layers = fiona.listlayers(path)
        available.extend(f"{path.name}: {layer}" for layer in layers)
        match = _find_hu12_layer(layers)
        if match:
            return path, match
    archive_names = [path.name for path in root.rglob("*.zip")]
    details = available or archive_names or ["(no vector layers found)"]
    raise WbdMaterializeError(
        "The selected package does not contain a recognizable WBD HUC12 vector layer. "
        "Available layers/products: " + ", ".join(details)
    )


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

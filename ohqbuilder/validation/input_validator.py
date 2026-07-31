from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..settings import BuilderSettings

REQUIRED_SCHEMAS: dict[str, tuple[str | None, set[str]]] = {
    "topology": (
        "topology",
        {"element_id", "element_type", "name", "ds_type", "ds_id", "ds_name"},
    ),
    "subbasins": (
        "subwatershed_params",
        {"id", "area_km2", "CN", "slope_pct", "flow_len_ft", "tc_min", "lag_min"},
    ),
    "reaches": (
        None,
        {"reach_id", "length_m", "slope_mm", "base_w_m", "side_z", "manning_n"},
    ),
    "junctions": (
        "junctions",
        {"junction_id", "x", "y"},
    ),
}


@dataclass
class InputValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}

    def raise_for_errors(self) -> None:
        if self.errors:
            raise ValueError("Input validation failed:\n" + "\n".join(self.errors))


def _try_default_reader():
    try:
        import geopandas as gpd
    except ImportError:
        return None
    return gpd.read_file


class InputValidator:
    def __init__(self, reader: Callable[..., object] | None = None):
        self.reader = reader

    def validate(self, settings: BuilderSettings, check_schema: bool = True) -> InputValidationResult:
        result = InputValidationResult()
        paths = settings.paths
        files = {
            "topology": paths.output_file(paths.topology),
            "subbasins": paths.output_file(paths.subbasins),
            "reaches": paths.output_file(paths.reaches),
            "junctions": paths.output_file(paths.junctions),
        }

        for label, path in files.items():
            if not path.is_file():
                result.errors.append(f"Missing {label} input: {path}")

        if result.errors or not check_schema:
            return result

        reader = self.reader or _try_default_reader()
        if reader is None:
            result.warnings.append(
                "Schema checks skipped because geopandas is not installed; install with pip install -e .[gis]."
            )
            return result

        for label, path in files.items():
            layer, required = REQUIRED_SCHEMAS[label]
            try:
                df = reader(path, layer=layer) if layer else reader(path)
            except Exception as exc:
                result.errors.append(f"Could not read {label} input {path}: {exc}")
                continue
            columns = set(getattr(df, "columns", []))
            missing = sorted(required - columns)
            if missing:
                result.errors.append(f"{label} input {path} is missing field(s): {', '.join(missing)}")

        self._validate_spatial_context(settings, result)

        return result

    @staticmethod
    def _validate_spatial_context(
        settings: BuilderSettings, result: InputValidationResult
    ) -> None:
        """Reject stale/placeholder hydrology products when they are present."""

        site = settings.paths.site_path
        outputs = settings.paths.outputs_path
        raster_paths = {
            "DEM": site / "demlr" / "cliped_utm.tif",
            "flow direction": outputs / "flow_dir.tif",
            "flow accumulation": outputs / "flow_acc.tif",
        }
        if not any(path.is_file() for path in raster_paths.values()):
            return
        missing = [label for label, path in raster_paths.items() if not path.is_file()]
        if missing:
            result.errors.append(
                "Incomplete hydrology raster set; missing: " + ", ".join(missing)
            )
            return
        try:
            import rasterio
        except ImportError:
            result.warnings.append(
                "Spatial consistency checks skipped because rasterio is not installed."
            )
            return

        metadata = {}
        for label, path in raster_paths.items():
            try:
                with rasterio.open(path) as dataset:
                    metadata[label] = {
                        "width": dataset.width,
                        "height": dataset.height,
                        "crs": dataset.crs,
                        "bounds": dataset.bounds,
                    }
            except Exception as exc:
                result.errors.append(f"Could not read {label} raster {path}: {exc}")
        if len(metadata) != len(raster_paths):
            return
        dem = metadata["DEM"]
        if dem["width"] < 100 or dem["height"] < 100:
            result.errors.append(
                f"DEM is only {dem['width']} x {dem['height']} cells; it appears to be "
                "a demo, placeholder, or incorrectly materialized raster."
            )
        for label in ("flow direction", "flow accumulation"):
            item = metadata[label]
            if (item["width"], item["height"]) != (dem["width"], dem["height"]):
                result.errors.append(
                    f"{label.title()} dimensions {item['width']} x {item['height']} do not "
                    f"match DEM dimensions {dem['width']} x {dem['height']}."
                )
            if item["crs"] != dem["crs"]:
                result.errors.append(
                    f"{label.title()} CRS {item['crs']} does not match DEM CRS {dem['crs']}."
                )

        reader = _try_default_reader()
        if reader is None or dem["crs"] is None:
            return
        from shapely.geometry import box

        raster_extent = box(*metadata["flow accumulation"]["bounds"])
        for label, path in (
            ("outlet", outputs / "outlet.shp"),
            ("watershed", outputs / "watershed_boundary.gpkg"),
        ):
            if not path.is_file():
                result.errors.append(f"Missing {label} spatial input required by hydrology: {path}")
                continue
            try:
                frame = reader(path)
                if frame.empty or frame.crs is None:
                    result.errors.append(f"{label.title()} input is empty or has no CRS: {path}")
                    continue
                geometry = frame.to_crs(dem["crs"]).geometry.union_all()
                if not geometry.intersects(raster_extent):
                    result.errors.append(
                        f"{label.title()} does not intersect the flow-accumulation raster extent."
                    )
            except Exception as exc:
                result.errors.append(f"Could not spatially validate {label} input {path}: {exc}")

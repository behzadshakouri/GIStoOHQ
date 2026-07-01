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

        return result

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .dem_acquisition import (
    DemAcquisitionArea,
    DemTileManifest,
    build_dem_tile_manifest,
    create_outlet_buffer_area,
)


class DemWorkflowError(RuntimeError):
    """Raised when a config-driven DEM workflow cannot be prepared."""


@dataclass(frozen=True)
class DemWorkflowPlanResult:
    config_path: Path
    acquisition_area: DemAcquisitionArea | None
    tile_manifest: DemTileManifest | None
    summary_path: Path


def _read_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise DemWorkflowError(f"Config file not found: {path}")
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DemWorkflowError("DEM workflow config must be a mapping.")
    return data


def _section(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name) or {}
    if not isinstance(value, dict):
        raise DemWorkflowError(f"Config section must be a mapping: {name}")
    return value


def _required_float(section: dict[str, Any], key: str, label: str) -> float:
    value = section.get(key)
    if value is None:
        raise DemWorkflowError(f"Missing required {label}: {key}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise DemWorkflowError(f"{label} must be numeric: {key}") from exc


def _resolve(path: str | Path, base: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def _relativize(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def prepare_dem_from_config(config_path: str | Path) -> DemWorkflowPlanResult:
    """Create DEM acquisition artifacts from one project config file.

    This is the CLI/UI orchestration layer: it reads outlet and DEM acquisition
    settings, writes the acquisition polygon when requested, selects tile-index
    intersections into a manifest when a tile index is configured, and writes a
    machine-readable summary for downstream scripts or UI status panels.
    """

    path = Path(config_path).expanduser().resolve()
    base = path.parent
    config = _read_config(path)
    outlet = _section(config, "outlet")
    dem_acquisition = _section(config, "dem_acquisition")

    method = str(dem_acquisition.get("method") or dem_acquisition.get("acquisition_mode") or "").lower()
    acquisition_path_value = dem_acquisition.get("acquisition_area")
    if not acquisition_path_value:
        raise DemWorkflowError("dem_acquisition.acquisition_area is required.")
    acquisition_path = _resolve(acquisition_path_value, base)

    acquisition_result: DemAcquisitionArea | None = None
    if method in {"outlet_buffer", "oriented_outlet_buffer"}:
        lon = _required_float(outlet, "longitude", "outlet")
        lat = _required_float(outlet, "latitude", "outlet")
        azimuth_value = dem_acquisition.get("azimuth")
        if method == "oriented_outlet_buffer" and azimuth_value is None:
            raise DemWorkflowError("dem_acquisition.azimuth is required for oriented_outlet_buffer.")
        azimuth = float(azimuth_value) if azimuth_value is not None else None
        acquisition_result = create_outlet_buffer_area(
            lon,
            lat,
            acquisition_path,
            upstream_km=float(dem_acquisition.get("upstream_km", 25.0)),
            downstream_km=float(dem_acquisition.get("downstream_km", 3.0)),
            lateral_km=float(dem_acquisition.get("lateral_km", 5.0)),
            azimuth_deg=azimuth,
        )
    elif method == "polygon":
        if not acquisition_path.exists():
            raise DemWorkflowError(f"Configured acquisition polygon does not exist: {acquisition_path}")
    else:
        raise DemWorkflowError(
            "dem_acquisition.method must be outlet_buffer, oriented_outlet_buffer, or polygon."
        )

    tile_manifest_result: DemTileManifest | None = None
    tile_index = dem_acquisition.get("tile_index")
    tile_manifest = dem_acquisition.get("tile_manifest")
    if tile_index and tile_manifest:
        tile_manifest_result = build_dem_tile_manifest(
            acquisition_path,
            _resolve(tile_index, base),
            _resolve(tile_manifest, base),
            url_field=str(dem_acquisition.get("tile_url_field", "url")),
            path_field=str(dem_acquisition.get("tile_path_field", "path")),
        )
    elif tile_index or tile_manifest:
        raise DemWorkflowError("dem_acquisition.tile_index and tile_manifest must be provided together.")

    summary_path = _resolve(
        dem_acquisition.get("summary") or "intermediate/dem_workflow_summary.json",
        base,
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "config": str(path),
        "method": method,
        "acquisition_area": _relativize(acquisition_path, base),
        "tile_manifest": _relativize(tile_manifest_result.output_path, base)
        if tile_manifest_result
        else None,
        "selected_tile_count": tile_manifest_result.selected_count if tile_manifest_result else None,
    }
    if acquisition_result:
        summary["acquisition_bounds"] = acquisition_result.bounds
        summary["acquisition_area_km2"] = acquisition_result.area_km2
    if tile_manifest_result:
        summary["acquisition_bounds"] = tile_manifest_result.acquisition_bounds
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return DemWorkflowPlanResult(path, acquisition_result, tile_manifest_result, summary_path)

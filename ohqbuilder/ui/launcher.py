from __future__ import annotations

import importlib
import importlib.util
import json
import math
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import yaml

OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_TILE_SIZE = 256
OSM_CACHE_DIR = Path(tempfile.gettempdir()) / "gistoohq_osm_tiles"
MIN_MAP_ZOOM = 1
MAX_MAP_ZOOM = 19


@dataclass(frozen=True)
class BasemapProvider:
    """Public XYZ basemap metadata used by the lightweight map picker."""

    key: str
    label: str
    url_template: str
    attribution: str
    max_zoom: int = MAX_MAP_ZOOM


BASEMAP_PROVIDERS = {
    "OpenStreetMap": BasemapProvider(
        "osm", "OpenStreetMap", OSM_TILE_URL, "© OpenStreetMap contributors"
    ),
    "Satellite": BasemapProvider(
        "esri_world_imagery",
        "Satellite",
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/"
        "tile/{z}/{y}/{x}",
        "Tiles © Esri and imagery contributors",
    ),
    "Topographic": BasemapProvider(
        "opentopomap",
        "Topographic",
        "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
        "Map data © OpenStreetMap contributors, SRTM | Map style © OpenTopoMap",
        max_zoom=17,
    ),
}

SLIGO_DEMO_SITE = "SligoCreekDemo"
SLIGO_DEMO_LON = -76.9744266065
SLIGO_DEMO_LAT = 38.9571888036
SLIGO_DEMO_CRS = "EPSG:26918"
SLIGO_DEMO_FLOWLINES = Path("hydro/NHDFlowline.demo.geojson")
SLIGO_DEMO_TILE_INDEX = Path("indexes/usgs_3dep_tiles.demo.geojson")


def osm_tile_cache_path(zoom: int, x: int, y: int, *, cache_dir: Path = OSM_CACHE_DIR) -> Path:
    """Return the cache path for a downloaded OSM tile."""

    return cache_dir / str(zoom) / str(x) / f"{y}.png"


def basemap_tile_cache_path(
    provider: BasemapProvider,
    zoom: int,
    x: int,
    y: int,
    *,
    cache_dir: Path = OSM_CACHE_DIR,
) -> Path:
    """Return a provider-isolated cache path so unlike tiles cannot collide."""

    return cache_dir / provider.key / str(zoom) / str(x) / f"{y}.png"


def basemap_tile_url(provider: BasemapProvider, zoom: int, x: int, y: int) -> str:
    """Expand an XYZ URL, including services whose path orders y before x."""

    return provider.url_template.format(z=zoom, x=x, y=y)


def _clamp_lat(lat: float) -> float:
    return max(-85.05112878, min(85.05112878, lat))


def clamp_zoom(zoom: int) -> int:
    return max(MIN_MAP_ZOOM, min(MAX_MAP_ZOOM, zoom))


def lonlat_to_tile_fraction(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """Return fractional Web Mercator tile coordinates for lon/lat."""

    lat = _clamp_lat(lat)
    n = 2**zoom
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def tile_fraction_to_lonlat(x: float, y: float, zoom: int) -> tuple[float, float]:
    """Return lon/lat for fractional Web Mercator tile coordinates."""

    n = 2**zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lon, lat


def map_click_to_lonlat(
    center_lon: float,
    center_lat: float,
    zoom: int,
    canvas_x: int,
    canvas_y: int,
    *,
    width: int = 768,
    height: int = 512,
) -> tuple[float, float]:
    """Convert a click on the Tk OSM preview canvas to lon/lat."""

    center_x, center_y = lonlat_to_tile_fraction(center_lon, center_lat, zoom)
    dx_tiles = (canvas_x - width / 2.0) / MAP_TILE_SIZE
    dy_tiles = (canvas_y - height / 2.0) / MAP_TILE_SIZE
    return tile_fraction_to_lonlat(center_x + dx_tiles, center_y + dy_tiles, zoom)


def nearest_point_on_lines(
    lon: float, lat: float, lines: list[list[list[float]]]
) -> tuple[float, float] | None:
    """Return the nearest point on GeoJSON line segments in lon/lat space."""
    nearest: tuple[float, float] | None = None
    nearest_distance = math.inf
    latitude_scale = math.cos(math.radians(lat))
    for line in lines:
        for start, end in zip(line, line[1:]):
            x1, y1 = (float(start[0]) - lon) * latitude_scale, float(start[1]) - lat
            x2, y2 = (float(end[0]) - lon) * latitude_scale, float(end[1]) - lat
            dx, dy = x2 - x1, y2 - y1
            length_squared = dx * dx + dy * dy
            fraction = (
                0.0
                if length_squared == 0
                else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / length_squared))
            )
            candidate_x = x1 + fraction * dx
            candidate_y = y1 + fraction * dy
            distance = candidate_x * candidate_x + candidate_y * candidate_y
            if distance < nearest_distance:
                nearest_distance = distance
                nearest = (lon + candidate_x / latitude_scale, lat + candidate_y)
    return nearest


def geojson_lines(path: Path) -> list[list[list[float]]]:
    """Read LineString and MultiLineString coordinates from a GeoJSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    lines: list[list[list[float]]] = []
    for feature in data.get("features", []):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") == "LineString":
            lines.append(geometry.get("coordinates", []))
        elif geometry.get("type") == "MultiLineString":
            lines.extend(geometry.get("coordinates", []))
    return lines


def write_drawn_acquisition(path: Path, points: list[tuple[float, float]]) -> Path:
    """Write a closed EPSG:4326 acquisition polygon drawn in the map picker."""
    if len(points) < 3:
        raise LauncherError("An acquisition polygon requires at least three points.")
    ring = list(points)
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    data = {
        "type": "FeatureCollection",
        "name": path.stem,
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"source": "launcher_map", "selection": "user_polygon"},
                "geometry": {"type": "Polygon", "coordinates": [[[lon, lat] for lon, lat in ring]]},
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def rectangle_from_corners(
    first: tuple[float, float], second: tuple[float, float]
) -> list[tuple[float, float]]:
    """Build four rectangle vertices from two opposite map corners."""
    x1, y1 = first
    x2, y2 = second
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def use_expanded_acquisition(config_path: Path, config: dict[str, Any]) -> Path:
    """Promote the validation-generated expanded area to the active polygon."""
    dem = config.get("dem_acquisition")
    if not isinstance(dem, dict):
        raise LauncherError("dem_acquisition must be a mapping.")
    expanded_value = dem.get("expanded_acquisition_area")
    if not isinstance(expanded_value, str) or not expanded_value:
        raise LauncherError("dem_acquisition.expanded_acquisition_area is not configured.")
    expanded = Path(expanded_value).expanduser()
    if not expanded.is_absolute():
        expanded = config_path.parent / expanded
    if not expanded.is_file():
        raise LauncherError(f"Expanded acquisition area does not exist yet: {expanded}")
    summary_value = (
        dem.get("validation_summary") or "intermediate/dem_boundary_validation_summary.json"
    )
    summary_path = Path(summary_value).expanduser()
    if not summary_path.is_absolute():
        summary_path = config_path.parent / summary_path
    if not summary_path.is_file() or summary_path.stat().st_mtime < config_path.stat().st_mtime:
        raise LauncherError(
            "No current DEM validation produced this expanded area. Run validate-dem first."
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary_expanded = summary.get("expanded_acquisition_area")
    if (
        not summary_expanded
        or (config_path.parent / summary_expanded).resolve() != expanded.resolve()
    ):
        raise LauncherError(
            "The validation summary does not reference the configured expanded area."
        )
    dem["acquisition_area"] = _path_for_config_value(expanded, config_path)
    dem["method"] = "polygon"
    return expanded


WorkflowStep = Literal[
    "init-dem-config",
    "prepare-dem",
    "run-dem-prep",
    "download-dem-manifest",
    "materialize-inputs",
    "validate-dem",
    "prepare-hydrology",
    "prepare-inputs",
    "check-inputs",
    "build-ohq",
    "run-to-ohq",
    "full-run",
    "promote-pour-points",
    "import-watershed-reference",
    "build-hms",
    "validate-hms",
]


class LauncherError(RuntimeError):
    """Raised when the lightweight UI launcher cannot be started."""


@dataclass(frozen=True)
class WorkflowCommand:
    label: str
    argv: tuple[str, ...]
    followup_argv: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class RunnerFinished:
    status: int


@dataclass(frozen=True)
class LauncherState:
    config_path: Path
    manifest_path: Path | None = None
    raw_dem_dir: Path | None = None
    root: Path | None = None
    site: str | None = None
    source_dir: Path | None = None
    target_crs: str | None = None
    lon: float | None = None
    lat: float | None = None
    method: str | None = None
    flowline_path: Path | None = None
    tile_index: Path | None = None
    acquisition_area: Path | None = None
    use_reviewed_pour_points: bool = False
    nhdplus_snap_distance_m: float = 50.0
    overwrite_promoted_pour_points: bool = False
    use_existing_outlet: bool = False
    reuse_downloads: bool = False
    overwrite_config: bool = False
    reference_source: str | None = None
    reference_layer: str | None = None
    reference_name_field: str | None = None
    reference_name: str | None = None
    reference_title: str | None = None
    reference_organization: str | None = None
    reference_url: str | None = None
    reference_license: str | None = None


def _path_for_config_value(path: Path, config_path: Path) -> str:
    """Return a path string suitable for writing into ``config_path``."""

    config_dir = config_path.expanduser().resolve().parent
    candidate = path.expanduser()
    if not candidate.is_absolute():
        cwd_candidate = candidate.resolve()
        try:
            cwd_candidate.relative_to(config_dir)
            candidate = cwd_candidate
        except ValueError:
            candidate = config_dir / candidate
    try:
        return str(candidate.resolve().relative_to(config_dir)) or "."
    except ValueError:
        return str(candidate.resolve())


def snapped_outlet(state: LauncherState) -> tuple[float, float]:
    """Snap an upstream-network outlet to its configured GeoJSON flowline."""

    assert state.lon is not None and state.lat is not None
    if state.method != "upstream_network" or state.flowline_path is None:
        return state.lon, state.lat
    flowline_path = state.flowline_path.expanduser()
    if not flowline_path.is_absolute() and not flowline_path.exists():
        flowline_path = state.config_path.expanduser().parent / flowline_path
    if flowline_path.suffix.lower() not in {".geojson", ".json"} or not flowline_path.is_file():
        return state.lon, state.lat
    try:
        nearest = nearest_point_on_lines(state.lon, state.lat, geojson_lines(flowline_path))
    except (OSError, ValueError, json.JSONDecodeError):
        return state.lon, state.lat
    return nearest or (state.lon, state.lat)


def command_for_step(step: WorkflowStep, state: LauncherState) -> WorkflowCommand:
    """Build the backend command that the UI should execute for a workflow step."""

    if step == "init-dem-config":
        if not state.site or state.lon is None or state.lat is None:
            raise LauncherError(
                "Site, outlet longitude, and outlet latitude are required for init-dem-config."
            )
        lon, lat = snapped_outlet(state)
        argv = [
            "ohqbuild",
            "init-dem-config",
            "--config",
            str(state.config_path),
            "--site",
            state.site,
            "--lon",
            str(lon),
            "--lat",
            str(lat),
        ]
        if state.flowline_path is not None:
            argv.extend(
                ("--flowlines", _path_for_config_value(state.flowline_path, state.config_path))
            )
        if state.tile_index is not None:
            argv.extend(
                ("--tile-index", _path_for_config_value(state.tile_index, state.config_path))
            )
        if state.target_crs:
            argv.extend(("--target-crs", state.target_crs))
        if state.method:
            argv.extend(("--method", state.method))
        if state.overwrite_config:
            argv.append("--force")
        return WorkflowCommand("Initialize DEM Config", tuple(argv))
    if step == "prepare-dem":
        return WorkflowCommand(
            "Prepare DEM", ("ohqbuild", "prepare-dem", "--config", str(state.config_path))
        )
    if step == "run-dem-prep":
        return WorkflowCommand(
            "Run DEM Prep", ("ohqbuild", "run-dem-prep", "--config", str(state.config_path))
        )
    if step == "validate-dem":
        return WorkflowCommand(
            "Validate DEM", ("ohqbuild", "validate-dem", "--config", str(state.config_path))
        )
    if step == "download-dem-manifest":
        if state.manifest_path is None or state.raw_dem_dir is None:
            raise LauncherError(
                "Manifest path and raw DEM directory are required for DEM download."
            )
        return WorkflowCommand(
            "Download DEM Tiles",
            (
                "ohqbuild",
                "download-dem-manifest",
                "--manifest",
                str(state.manifest_path),
                "--out-dir",
                str(state.raw_dem_dir),
            ),
        )
    if step == "materialize-inputs":
        if state.root is None or not state.site:
            raise LauncherError("Root and site are required for materialize-inputs.")
        argv = ["ohqbuild", "materialize-inputs", "--root", str(state.root), "--site", state.site]
        if state.source_dir is not None:
            argv.extend(("--source-dir", str(state.source_dir)))
        if state.target_crs:
            argv.extend(("--target-crs", state.target_crs))
        if state.manifest_path is not None:
            argv.extend(("--dem-manifest", str(state.manifest_path)))
        return WorkflowCommand("Materialize Inputs", tuple(argv))
    if step == "full-run":
        if state.root is None or not state.site or state.lon is None or state.lat is None:
            raise LauncherError(
                "Root, site, and verified outlet coordinates are required for full-run."
            )
        argv = [
            "ohqbuild",
            "full-run",
            "--root",
            str(state.root),
            "--site",
            state.site,
            "--lon",
            str(state.lon),
            "--lat",
            str(state.lat),
            "--project-name",
            state.site,
        ]
        if state.target_crs:
            argv.extend(("--target-crs", state.target_crs))
        if state.source_dir is not None:
            argv.extend(("--download-dir", str(state.source_dir)))
        argv.extend(("--nhdplus-snap-distance-m", str(state.nhdplus_snap_distance_m)))
        if state.use_reviewed_pour_points:
            argv.append("--use-reviewed-pour-points")
        if state.use_existing_outlet:
            argv.append("--use-existing-outlet")
        if state.reuse_downloads:
            argv.append("--reuse-downloads")
        if state.acquisition_area is not None and (
            state.acquisition_area.is_file()
            or state.method in {"outlet_buffer", "oriented_outlet_buffer", "upstream_network"}
        ):
            argv.extend(("--acquisition-area", str(state.acquisition_area)))
        if state.method in {"outlet_buffer", "oriented_outlet_buffer", "upstream_network"}:
            return WorkflowCommand(
                "Full Run: Generate Default Area, then Download to OHQ",
                ("ohqbuild", "prepare-dem", "--config", str(state.config_path)),
                (tuple(argv),),
            )
        return WorkflowCommand("Full Run: Download to OHQ", tuple(argv))
    if step == "promote-pour-points":
        if state.root is None or not state.site:
            raise LauncherError("Root and site are required to promote pour points.")
        argv = [
            "ohqbuild", "promote-pour-points", "--root", str(state.root),
            "--site", state.site,
        ]
        if state.overwrite_promoted_pour_points:
            argv.append("--overwrite")
        return WorkflowCommand(
            "Promote Reviewed Pour Points",
            tuple(argv),
        )
    if step == "import-watershed-reference":
        required = {
            "root": state.root,
            "site": state.site,
            "outlet longitude": state.lon,
            "outlet latitude": state.lat,
            "reference source": state.reference_source,
            "reference title": state.reference_title,
            "reference organization": state.reference_organization,
        }
        missing = [name for name, value in required.items() if value in (None, "")]
        if missing:
            raise LauncherError(
                "Documented watershed import requires: " + ", ".join(missing) + "."
            )
        argv = [
            "ohqbuild", "import-watershed-reference",
            "--root", str(state.root), "--site", str(state.site),
            "--source", str(state.reference_source),
            "--lon", str(state.lon), "--lat", str(state.lat),
            "--source-title", str(state.reference_title),
            "--source-organization", str(state.reference_organization),
        ]
        for flag, value in (
            ("--layer", state.reference_layer),
            ("--name-field", state.reference_name_field),
            ("--name", state.reference_name),
            ("--source-url", state.reference_url),
            ("--license", state.reference_license),
        ):
            if value:
                argv.extend((flag, value))
        return WorkflowCommand("Import Documented Watershed", tuple(argv))
    if step in {"build-hms", "validate-hms"}:
        if state.root is None or not state.site:
            raise LauncherError("Root and site are required for HEC-HMS commands.")
        if step == "build-hms":
            return WorkflowCommand(
                "Build HEC-HMS Project",
                (
                    "ohqbuild",
                    "build-hms",
                    "--root",
                    str(state.root),
                    "--site",
                    state.site,
                    "--project-name",
                    state.site,
                ),
            )
        project = state.root / state.site / "outputs" / "hec_hms" / f"{state.site}.hms"
        return WorkflowCommand(
            "Validate HEC-HMS Project", ("ohqbuild", "validate-hms", "--project", str(project))
        )
    if step in {"prepare-hydrology", "prepare-inputs", "check-inputs", "build-ohq", "run-to-ohq"}:
        if state.root is None or not state.site:
            raise LauncherError("Root and site are required for OHQ workflow commands.")
        command = {
            "prepare-hydrology": "prepare-hydrology",
            "prepare-inputs": "prepare-inputs",
            "check-inputs": "check-inputs",
            "build-ohq": "build",
            "run-to-ohq": "run",
        }[step]
        label = {
            "prepare-hydrology": "Prepare Hydrology",
            "prepare-inputs": "Prepare OHQ Inputs",
            "check-inputs": "Check OHQ Inputs",
            "build-ohq": "Build OHQ File",
            "run-to-ohq": "Continue to OHQ",
        }[step]
        argv = ("ohqbuild", command, "--root", str(state.root), "--site", state.site)
        if step == "run-to-ohq":
            hydrology = (
                "ohqbuild",
                "prepare-hydrology",
                "--root",
                str(state.root),
                "--site",
                state.site,
            )
            return WorkflowCommand(label, hydrology, (argv,))
        return WorkflowCommand(label, argv)
    raise LauncherError(f"Unsupported workflow step: {step}")


def default_config_path() -> str:
    """Return a useful default config path for the launcher."""

    example = Path("examples/SligoCreek/dem_workflow.example.yaml")
    return str(example) if example.exists() else "config.example.json"


def _require_tkinter():
    if importlib.util.find_spec("tkinter") is None:
        raise LauncherError("tkinter is not available in this Python environment.")
    return importlib.import_module("tkinter")


def _config_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if any(marker in text for marker in ("<<<<<<<", "=======", ">>>>>>>")):
        raise LauncherError(
            f"Config file contains unresolved merge-conflict markers: {path}. "
            "Resolve the conflict markers before loading or running workflow commands."
        )
    return text


def load_project_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path).expanduser()
    text = _config_text(path)
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise LauncherError("Project config must be a mapping.")
    return data


def save_project_config(config_path: str | Path, config: dict[str, Any]) -> None:
    path = Path(config_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _set_nested(config: dict[str, Any], section: str, key: str, value: Any) -> None:
    target = config.setdefault(section, {})
    if not isinstance(target, dict):
        raise LauncherError(f"Config section is not a mapping: {section}")
    target[key] = value


def state_from_config(config_path: str | Path, config: dict[str, Any]) -> LauncherState:
    base = Path(config_path).expanduser().resolve().parent
    dem = config.get("dem_acquisition") if isinstance(config.get("dem_acquisition"), dict) else {}
    site_config = config.get("site") if isinstance(config.get("site"), dict) else {}
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    reference = (
        config.get("documented_watershed")
        if isinstance(config.get("documented_watershed"), dict)
        else {}
    )

    def path_value(value: Any) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        cwd_candidate = path.resolve()
        try:
            cwd_candidate.relative_to(base)
            return cwd_candidate
        except ValueError:
            return base / path

    return LauncherState(
        config_path=Path(config_path).expanduser(),
        manifest_path=path_value(dem.get("tile_manifest")),
        raw_dem_dir=path_value(paths.get("raw_dem_dir") or dem.get("raw_dem_dir") or "dem/raw"),
        root=path_value(config.get("root") or "."),
        site=str(site_config.get("name") or config.get("site") or "."),
        source_dir=path_value(config.get("download_dir") or "source_downloads"),
        target_crs=str(site_config.get("target_crs") or config.get("target_crs") or "") or None,
        lon=float(config.get("outlet", {}).get("longitude"))
        if isinstance(config.get("outlet"), dict)
        and config.get("outlet", {}).get("longitude") is not None
        else None,
        lat=float(config.get("outlet", {}).get("latitude"))
        if isinstance(config.get("outlet"), dict)
        and config.get("outlet", {}).get("latitude") is not None
        else None,
        method=str(dem.get("method") or "") or None,
        flowline_path=path_value(dem.get("flowline_path")),
        tile_index=path_value(dem.get("tile_index")),
        acquisition_area=path_value(dem.get("acquisition_area")),
        use_reviewed_pour_points=bool(config.get("use_reviewed_pour_points", False)),
        nhdplus_snap_distance_m=float(config.get("nhdplus_snap_distance_m", 50.0)),
        reuse_downloads=bool(config.get("reuse_downloads", False)),
        reference_source=str(reference.get("source") or "") or None,
        reference_layer=str(reference.get("layer") or "") or None,
        reference_name_field=str(reference.get("name_field") or "") or None,
        reference_name=str(reference.get("name") or "") or None,
        reference_title=str(reference.get("title") or "") or None,
        reference_organization=str(reference.get("organization") or "") or None,
        reference_url=str(reference.get("url") or "") or None,
        reference_license=str(reference.get("license") or "") or None,
    )


def update_config_from_state(config: dict[str, Any], state: LauncherState) -> dict[str, Any]:
    updated = dict(config)
    config_path = state.config_path

    def path_text(value: Path | None, fallback: str = "") -> str:
        return _path_for_config_value(value, config_path) if value is not None else fallback

    _set_nested(updated, "dem_acquisition", "tile_manifest", path_text(state.manifest_path))
    _set_nested(updated, "paths", "raw_dem_dir", path_text(state.raw_dem_dir))
    if state.site:
        _set_nested(updated, "site", "name", state.site)
    if state.target_crs:
        _set_nested(updated, "site", "target_crs", state.target_crs)
    if state.lon is not None:
        _set_nested(updated, "outlet", "longitude", state.lon)
    if state.lat is not None:
        _set_nested(updated, "outlet", "latitude", state.lat)
    if state.method:
        _set_nested(updated, "dem_acquisition", "method", state.method)
    if state.flowline_path is not None:
        _set_nested(updated, "dem_acquisition", "flowline_path", path_text(state.flowline_path))
    if state.tile_index is not None:
        _set_nested(updated, "dem_acquisition", "tile_index", path_text(state.tile_index))
    updated["root"] = path_text(state.root, ".")
    updated["download_dir"] = path_text(state.source_dir, "source_downloads")
    updated["use_reviewed_pour_points"] = state.use_reviewed_pour_points
    updated["nhdplus_snap_distance_m"] = state.nhdplus_snap_distance_m
    updated["reuse_downloads"] = state.reuse_downloads
    for key, value in (
        ("source", state.reference_source),
        ("layer", state.reference_layer),
        ("name_field", state.reference_name_field),
        ("name", state.reference_name),
        ("title", state.reference_title),
        ("organization", state.reference_organization),
        ("url", state.reference_url),
        ("license", state.reference_license),
    ):
        if value:
            _set_nested(updated, "documented_watershed", key, value)
    return updated


def state_with_config_defaults(form_state: LauncherState, config: dict[str, Any]) -> LauncherState:
    """Merge launcher form values over config-derived defaults for command execution."""

    config_state = state_from_config(form_state.config_path, config)

    def preferred_path(form_value: Path | None, config_value: Path | None) -> Path | None:
        return form_value or config_value

    def preferred_text(form_value: str | None, config_value: str | None) -> str | None:
        if form_value and form_value != ".":
            return form_value
        return config_value or form_value

    return LauncherState(
        config_path=form_state.config_path,
        manifest_path=preferred_path(form_state.manifest_path, config_state.manifest_path),
        raw_dem_dir=preferred_path(form_state.raw_dem_dir, config_state.raw_dem_dir),
        root=preferred_path(form_state.root, config_state.root),
        site=preferred_text(form_state.site, config_state.site),
        source_dir=preferred_path(form_state.source_dir, config_state.source_dir),
        target_crs=preferred_text(form_state.target_crs, config_state.target_crs),
        lon=form_state.lon if form_state.lon is not None else config_state.lon,
        lat=form_state.lat if form_state.lat is not None else config_state.lat,
        method=preferred_text(form_state.method, config_state.method),
        flowline_path=preferred_path(form_state.flowline_path, config_state.flowline_path),
        tile_index=preferred_path(form_state.tile_index, config_state.tile_index),
        acquisition_area=preferred_path(form_state.acquisition_area, config_state.acquisition_area),
        use_reviewed_pour_points=form_state.use_reviewed_pour_points,
        nhdplus_snap_distance_m=form_state.nhdplus_snap_distance_m,
        overwrite_promoted_pour_points=form_state.overwrite_promoted_pour_points,
        use_existing_outlet=form_state.use_existing_outlet,
        reuse_downloads=form_state.reuse_downloads,
        reference_source=form_state.reference_source or config_state.reference_source,
        reference_layer=form_state.reference_layer or config_state.reference_layer,
        reference_name_field=(
            form_state.reference_name_field or config_state.reference_name_field
        ),
        reference_name=form_state.reference_name or config_state.reference_name,
        reference_title=form_state.reference_title or config_state.reference_title,
        reference_organization=(
            form_state.reference_organization or config_state.reference_organization
        ),
        reference_url=form_state.reference_url or config_state.reference_url,
        reference_license=form_state.reference_license or config_state.reference_license,
    )


def workflow_prerequisite_error(step: WorkflowStep, state: LauncherState) -> str | None:
    """Return actionable UI guidance instead of launching a predictably invalid stage."""
    if step == "download-dem-manifest" and (
        state.manifest_path is None or not state.manifest_path.is_file()
    ):
        return (
            "No DEM manifest exists. Configure a tile index and run prepare-dem, or use FULL RUN."
        )
    if step == "promote-pour-points" and state.root is not None and state.site:
        candidates = state.root / state.site / "outputs" / "pour_point_candidates.gpkg"
        if not candidates.is_file():
            return (
                "No pour-point candidate file exists. Generate candidates first, review "
                "them in QGIS, then promote them."
            )
    if step == "import-watershed-reference":
        required = (
            state.root, state.site, state.lon, state.lat,
            state.reference_source, state.reference_title,
            state.reference_organization,
        )
        if any(value in (None, "") for value in required):
            return (
                "Enter a reference source, title, publisher, and outlet coordinates "
                "before importing the documented watershed."
            )
    if step == "materialize-inputs" and state.manifest_path is None:
        source = state.source_dir
        if source is None or not source.is_dir() or not any(source.rglob("demlr")):
            return (
                "No downloaded DEM products exist. Use FULL RUN to download and materialize data."
            )
    if step == "validate-dem" and state.root is not None:
        config = load_project_config(state.config_path)
        dem = (
            config.get("dem_acquisition") if isinstance(config.get("dem_acquisition"), dict) else {}
        )
        watershed = dem.get("watershed_boundary")
        watershed_path = Path(watershed) if isinstance(watershed, str) else None
        if watershed_path is not None and not watershed_path.is_absolute():
            watershed_path = state.config_path.parent / watershed_path
        if watershed_path is None or not watershed_path.is_file():
            return "Watershed boundary is missing. Run FULL RUN or Prepare GIS inputs before validate-dem."
    if step in {"prepare-hydrology", "run-to-ohq"} and state.root is not None and state.site:
        site = state.root / state.site
        if not (site / "demlr" / "cliped_utm.tif").is_file():
            return "Materialized DEM is missing. Run materialize-inputs or FULL RUN first."
        if not (site / "outputs" / "NHDFlowline_clip.gpkg").is_file():
            return "Materialized flowlines are missing. Run materialize-inputs or FULL RUN first."
    if step in {"prepare-inputs", "check-inputs", "build-ohq", "build-hms"} and state.root and state.site:
        outputs = state.root / state.site / "outputs"
        if step == "prepare-inputs":
            required = ("flow_dir.tif", "flow_acc.tif")
            if any(not (outputs / name).is_file() for name in required):
                return "Hydrology outputs are missing. Run Prepare hydrology first."
        elif any(
            not (outputs / name).is_file()
            for name in (
                "topology.gpkg",
                "subwatershed_params.gpkg",
                "reaches.gpkg",
                "junctions.gpkg",
            )
        ):
            return "Phase 1/2 model inputs are missing. Run Prepare GIS inputs first."
    return None


def recommended_workflow_step(state: LauncherState) -> WorkflowStep:
    """Choose the next useful action from files already present in a project."""
    if state.root is None or not state.site:
        return "full-run"
    site = state.root / state.site
    outputs = site / "outputs"
    if not (site / "demlr" / "cliped_utm.tif").is_file():
        return "full-run"
    if not (outputs / "flow_dir.tif").is_file() or not (outputs / "flow_acc.tif").is_file():
        return "prepare-hydrology"
    if any(
        not (outputs / name).is_file()
        for name in ("topology.gpkg", "subwatershed_params.gpkg", "reaches.gpkg", "junctions.gpkg")
    ):
        return "prepare-inputs"
    if not (outputs / f"{state.site}.ohq").is_file():
        return "build-ohq"
    return "build-hms"


def sligo_demo_reset_args(
    config_path: str | Path, lon: float | None, lat: float | None
) -> dict[str, Any]:
    """Return arguments for rewriting the bundled Sligo Creek demo config."""

    return {
        "output_path": Path(config_path).expanduser(),
        "site": SLIGO_DEMO_SITE,
        "lon": lon if lon is not None else SLIGO_DEMO_LON,
        "lat": lat if lat is not None else SLIGO_DEMO_LAT,
        "flowline_path": SLIGO_DEMO_FLOWLINES,
        "tile_index": SLIGO_DEMO_TILE_INDEX,
        "target_crs": SLIGO_DEMO_CRS,
        "method": "upstream_network",
    }


def geojson_preview_summary(path: str | Path) -> str:
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise LauncherError("Preview file must be a GeoJSON FeatureCollection.")
    geometry_types = sorted(
        {
            feature.get("geometry", {}).get("type", "Unknown")
            for feature in features
            if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict)
        }
    )
    return f"{len(features)} feature(s); geometry: {', '.join(geometry_types) or 'none'}"


class CommandRunner(threading.Thread):
    def __init__(self, command: WorkflowCommand, messages: queue.Queue[Any]):
        super().__init__(daemon=True)
        self.command = command
        self.messages = messages
        self.process: subprocess.Popen[str] | None = None
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        """Stop the active command and its child process group."""
        self.cancelled.set()
        process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            process.terminate()

    def run(self) -> None:
        status = 0
        commands = (self.command.argv, *self.command.followup_argv)
        try:
            for argv in commands:
                if self.cancelled.is_set():
                    status = 130
                    break
                self.messages.put(f"$ {' '.join(argv)}\n")
                self.process = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                assert self.process.stdout is not None
                for line in self.process.stdout:
                    self.messages.put(line)
                status = self.process.wait()
                if self.cancelled.is_set():
                    status = 130
                if status != 0:
                    break
        except OSError as exc:
            status = 2
            self.messages.put(f"Could not start workflow command: {exc}\n")
        if self.cancelled.is_set():
            self.messages.put(f"\n[{self.command.label} cancelled by user]\n")
        else:
            self.messages.put(f"\n[{self.command.label} exited with {status}]\n")
        if self.command.label == "Validate DEM" and status == 3:
            self.messages.put(
                "DEM validation requested a larger acquisition area. This is an actionable "
                "EXPAND result, not a crash; review the generated expanded GeoJSON, then draw "
                "or select the larger area and repeat DEM preparation.\n"
            )
        self.messages.put(RunnerFinished(status))


def qgis_layer_paths(state: LauncherState) -> tuple[Path, ...]:
    """Return generated watershed rasters and vectors suitable for QGIS."""
    if state.root is None or not state.site:
        return ()
    site_path = (state.root / state.site).resolve()
    roots = (site_path / "demlr", site_path / "outputs")
    supported = {".tif", ".tiff", ".gpkg", ".shp", ".geojson"}
    paths = {
        path.resolve()
        for root in roots
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in supported
    }
    return tuple(sorted(paths, key=lambda path: str(path)))


def qgis_command(state: LauncherState, executable: str | None = None) -> tuple[str, ...]:
    """Build a QGIS command that opens all currently generated workflow layers."""
    qgis = executable or shutil.which("qgis")
    if not qgis:
        raise LauncherError("QGIS executable was not found on PATH.")
    layers = qgis_layer_paths(state)
    if not layers:
        raise LauncherError("No generated DEM, hydrology, or delineation layers exist yet.")
    return (qgis, "--nologo", *(str(path) for path in layers))


class MapPicker:
    """Small multi-basemap picker for choosing an outlet in the Tk launcher."""

    def __init__(
        self,
        app: "LauncherApp",
        *,
        mode: str = "Outlet",
        zoom: int = 14,
        width: int = 768,
        height: int = 512,
    ) -> None:
        if mode not in {"Outlet", "Rectangle", "Polygon"}:
            raise LauncherError(f"Unsupported map selection mode: {mode}")
        self.app = app
        self.tk = app.tk
        self.zoom = clamp_zoom(zoom)
        self.width = width
        self.height = height
        self.center_lon = float(app.lon_var.get() or SLIGO_DEMO_LON)
        self.center_lat = float(app.lat_var.get() or SLIGO_DEMO_LAT)
        self.images = []
        self.flowlines = self._load_flowlines()
        self.selection_points: list[tuple[float, float]] = []
        self.area_saved = False
        self.mode = self.tk.StringVar(value=mode)
        self.basemap = self.tk.StringVar(value="OpenStreetMap")
        self.window = self.tk.Toplevel(app.root)
        self.window.title("Pick Outlet or Acquisition Area")
        self.canvas = self.tk.Canvas(self.window, width=width, height=height)
        self.canvas.pack(fill="both", expand=True)
        controls = self.tk.Frame(self.window)
        controls.pack(fill="x")
        self.tk.Button(controls, text="Zoom +", command=lambda: self._zoom(1)).pack(side="left")
        self.tk.Button(controls, text="Zoom -", command=lambda: self._zoom(-1)).pack(side="left")
        self.tk.Button(
            controls, text="Reload at lon/lat fields", command=self._reload_from_fields
        ).pack(side="left")
        self.tk.Label(controls, text="Selection:").pack(side="left", padx=(12, 2))
        self.tk.OptionMenu(
            controls, self.mode, "Outlet", "Rectangle", "Polygon", command=self._mode_changed
        ).pack(side="left")
        self.tk.Label(controls, text="Basemap:").pack(side="left", padx=(12, 2))
        self.tk.OptionMenu(
            controls, self.basemap, *BASEMAP_PROVIDERS, command=self._basemap_changed
        ).pack(side="left")
        self.tk.Button(controls, text="Finish area", command=self._finish_area).pack(side="left")
        self.tk.Button(controls, text="Clear", command=self._clear_selection).pack(side="left")
        self.status = self.tk.Label(
            self.window,
            text="Left-click to set outlet; selections snap to the configured flowline.",
        )
        self.status.pack(fill="x")
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Button-3>", self._recenter)
        self._draw_tiles()

    def _load_flowlines(self) -> list[list[list[float]]]:
        try:
            config = load_project_config(self.app.config_var.get())
            state = state_from_config(self.app.config_var.get(), config)
            if state.flowline_path and state.flowline_path.suffix.lower() in {".geojson", ".json"}:
                return geojson_lines(state.flowline_path)
        except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError, LauncherError):
            pass
        return []

    def _draw_tiles(self) -> None:
        self.canvas.delete("all")
        self.images = []
        instruction = {
            "Outlet": "Click an outlet; it will snap to the configured flowline.",
            "Rectangle": "Click the first corner, then click the opposite corner.",
            "Polygon": "Click at least three vertices, then click Finish area.",
        }[self.mode.get()]
        provider = BASEMAP_PROVIDERS[self.basemap.get()]
        self.status.config(
            text=f"{instruction} Right-click recenters. zoom={self.zoom}. {provider.attribution}"
        )
        center_x, center_y = lonlat_to_tile_fraction(self.center_lon, self.center_lat, self.zoom)
        center_tile_x = math.floor(center_x)
        center_tile_y = math.floor(center_y)
        origin_x = self.width / 2.0 - (center_x - center_tile_x) * MAP_TILE_SIZE
        origin_y = self.height / 2.0 - (center_y - center_tile_y) * MAP_TILE_SIZE
        radius_x = math.ceil(self.width / MAP_TILE_SIZE / 2) + 1
        radius_y = math.ceil(self.height / MAP_TILE_SIZE / 2) + 1
        for dx in range(-radius_x, radius_x + 1):
            for dy in range(-radius_y, radius_y + 1):
                tile_x = center_tile_x + dx
                tile_y = center_tile_y + dy
                try:
                    image = self._tile_image(tile_x, tile_y)
                except Exception as exc:  # pragma: no cover - network/UI boundary
                    self.status.config(text=f"Could not load {provider.label} tile: {exc}")
                    continue
                self.images.append(image)
                self.canvas.create_image(
                    origin_x + dx * MAP_TILE_SIZE,
                    origin_y + dy * MAP_TILE_SIZE,
                    anchor="nw",
                    image=image,
                )
        self._draw_flowlines()
        self._draw_selection()

    def _mode_changed(self, _value=None) -> None:
        self.selection_points = []
        self.area_saved = False
        self._draw_tiles()

    def _basemap_changed(self, _value=None) -> None:
        provider = BASEMAP_PROVIDERS[self.basemap.get()]
        self.zoom = min(self.zoom, provider.max_zoom)
        self._draw_tiles()

    def _clear_selection(self) -> None:
        self.selection_points = []
        self.area_saved = False
        self._draw_tiles()

    def _selection_ring(self) -> list[tuple[float, float]]:
        if self.mode.get() == "Rectangle" and len(self.selection_points) == 2:
            return rectangle_from_corners(*self.selection_points)
        return list(self.selection_points)

    def _draw_selection(self) -> None:
        ring = self._selection_ring()
        if not ring:
            return
        center_x, center_y = lonlat_to_tile_fraction(self.center_lon, self.center_lat, self.zoom)
        points = []
        for lon, lat in ring:
            tile_x, tile_y = lonlat_to_tile_fraction(lon, lat, self.zoom)
            x = self.width / 2.0 + (tile_x - center_x) * MAP_TILE_SIZE
            y = self.height / 2.0 + (tile_y - center_y) * MAP_TILE_SIZE
            points.extend((x, y))
            self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#ffcc00")
        if len(points) >= 4:
            self.canvas.create_line(*points, fill="#ffcc00", width=3)
        if len(ring) >= 3:
            self.canvas.create_line(*points, points[0], points[1], fill="#ffcc00", width=3)

    def _draw_flowlines(self) -> None:
        center_x, center_y = lonlat_to_tile_fraction(self.center_lon, self.center_lat, self.zoom)
        for line in self.flowlines:
            points = []
            for lon, lat, *_ in line:
                tile_x, tile_y = lonlat_to_tile_fraction(float(lon), float(lat), self.zoom)
                points.extend(
                    (
                        self.width / 2.0 + (tile_x - center_x) * MAP_TILE_SIZE,
                        self.height / 2.0 + (tile_y - center_y) * MAP_TILE_SIZE,
                    )
                )
            if len(points) >= 4:
                self.canvas.create_line(*points, fill="#00ffff", width=4)

    def _tile_image(self, x: int, y: int):
        max_tile = 2**self.zoom
        x = x % max_tile
        if y < 0 or y >= max_tile:
            raise LauncherError("Tile row is outside the Web Mercator range.")
        provider = BASEMAP_PROVIDERS[self.basemap.get()]
        cache_path = basemap_tile_cache_path(provider, self.zoom, x, y)
        if not cache_path.exists():
            url = basemap_tile_url(provider, self.zoom, x, y)
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "GIStoOHQ DEM workflow launcher"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                payload = response.read()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(payload)
        return self.tk.PhotoImage(file=str(cache_path))

    def _event_lonlat(self, event) -> tuple[float, float]:
        return map_click_to_lonlat(
            self.center_lon,
            self.center_lat,
            self.zoom,
            event.x,
            event.y,
            width=self.width,
            height=self.height,
        )

    def _click(self, event) -> None:
        lon, lat = self._event_lonlat(event)
        if self.mode.get() != "Outlet":
            if self.mode.get() == "Rectangle" and len(self.selection_points) == 2:
                self.selection_points = []
            self.selection_points.append((lon, lat))
            self._draw_tiles()
            if self.mode.get() == "Rectangle" and len(self.selection_points) == 2:
                self.status.config(
                    text="Rectangle ready. Review the yellow boundary, then click Finish area."
                )
            return
        snapped = nearest_point_on_lines(lon, lat, self.flowlines)
        if snapped is not None:
            lon, lat = snapped
        self.app.lon_var.set(f"{lon:.8f}")
        self.app.lat_var.set(f"{lat:.8f}")
        action = (
            "Picked and snapped outlet to flowline"
            if snapped is not None
            else f"Picked outlet from {self.basemap.get()} map"
        )
        self.app.messages.put(f"{action}: lon={lon:.8f}, lat={lat:.8f}\n")
        self.window.destroy()

    def _finish_area(self) -> None:
        if self.area_saved:
            return
        ring = self._selection_ring()
        try:
            self.app.save_drawn_area(ring)
        except (OSError, ValueError, LauncherError, json.JSONDecodeError, yaml.YAMLError) as exc:
            self.status.config(text=f"Could not save area: {exc}")
            return
        self.area_saved = True
        self.window.destroy()

    def _recenter(self, event) -> None:
        self.center_lon, self.center_lat = self._event_lonlat(event)
        self._draw_tiles()

    def _zoom(self, delta: int) -> None:
        provider = BASEMAP_PROVIDERS[self.basemap.get()]
        self.zoom = min(provider.max_zoom, clamp_zoom(self.zoom + delta))
        self._draw_tiles()

    def _reload_from_fields(self) -> None:
        self.center_lon = float(self.app.lon_var.get() or self.center_lon)
        self.center_lat = float(self.app.lat_var.get() or self.center_lat)
        self._draw_tiles()


class LauncherApp:
    """Small Tk-based launcher that writes no workflow logic of its own."""

    def __init__(self) -> None:
        tk = _require_tkinter()
        self.tk = tk
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            raise LauncherError(
                "The Tk launcher requires a graphical Linux session with DISPLAY set. "
                "Use SSH X forwarding, a desktop terminal, or the terminal commands instead."
            ) from exc
        self.root.title("GIStoOHQ DEM Workflow Launcher")
        self.messages: queue.Queue[Any] = queue.Queue()
        self.command_running = False
        self.runner: CommandRunner | None = None
        self.workflow_buttons: list[Any] = []
        self.step_buttons: dict[WorkflowStep, Any] = {}
        self.config_var = tk.StringVar(value=default_config_path())
        self.manifest_var = tk.StringVar(value="intermediate/dem_download_manifest.json")
        self.raw_dem_var = tk.StringVar(value="dem/raw")
        self.root_var = tk.StringVar(value=".")
        self.site_var = tk.StringVar(value=".")
        self.source_var = tk.StringVar(value="source_downloads")
        self.crs_var = tk.StringVar(value="")
        self.lon_var = tk.StringVar(value="")
        self.lat_var = tk.StringVar(value="")
        self.method_var = tk.StringVar(value="upstream_network")
        self.flowline_var = tk.StringVar(value="")
        self.tile_index_var = tk.StringVar(value="")
        self.reviewed_points_var = tk.BooleanVar(value=False)
        self.nhdplus_snap_var = tk.StringVar(value="50")
        self.overwrite_promoted_var = tk.BooleanVar(value=False)
        self.use_existing_outlet_var = tk.BooleanVar(value=False)
        self.reuse_downloads_var = tk.BooleanVar(value=False)
        self.reference_source_var = tk.StringVar(value="")
        self.reference_layer_var = tk.StringVar(value="")
        self.reference_name_field_var = tk.StringVar(value="")
        self.reference_name_var = tk.StringVar(value="")
        self.reference_title_var = tk.StringVar(value="")
        self.reference_org_var = tk.StringVar(value="")
        self.reference_url_var = tk.StringVar(value="")
        self.reference_license_var = tk.StringVar(value="")
        self._build()
        if Path(self.config_var.get()).exists():
            self.load_config()
        else:
            self._refresh_step_buttons()
        self._poll_messages()

    def _build(self) -> None:
        tk = self.tk
        frame = tk.Frame(self.root, padx=10, pady=10)
        frame.pack(fill="both", expand=True)
        rows = [
            ("Config", self.config_var),
            ("Manifest", self.manifest_var),
            ("Raw DEM dir", self.raw_dem_var),
            ("Root", self.root_var),
            ("Site", self.site_var),
            ("Source dir", self.source_var),
            ("Target CRS", self.crs_var),
            ("Outlet lon", self.lon_var),
            ("Outlet lat", self.lat_var),
            ("DEM method", self.method_var),
            ("Flowlines", self.flowline_var),
            ("Tile index", self.tile_index_var),
            ("Outlet/NHDPlus snap max (m)", self.nhdplus_snap_var),
        ]
        for row, (label, variable) in enumerate(rows):
            tk.Label(frame, text=label).grid(row=row, column=0, sticky="w")
            tk.Entry(frame, textvariable=variable, width=70).grid(row=row, column=1, sticky="ew")
            if label in {
                "Config", "Manifest", "Flowlines", "Tile index",
            }:
                tk.Button(
                    frame,
                    text="Browse…",
                    command=lambda value=variable, is_config=label == "Config": self.browse_file(
                        value, load_config=is_config
                    ),
                ).grid(row=row, column=2, sticky="w")
            elif label in {"Raw DEM dir", "Root", "Source dir"}:
                tk.Button(
                    frame,
                    text="Browse…",
                    command=lambda value=variable: self.browse_directory(value),
                ).grid(row=row, column=2, sticky="w")
        project_buttons = tk.LabelFrame(frame, text="Project and map")
        project_buttons.grid(row=len(rows), column=0, columnspan=2, sticky="ew", pady=4)
        project_specs = (
            ("Load config", self.load_config),
            ("Save config", self.save_config),
            ("Preview acquisition", self.preview_acquisition),
            ("Pick outlet", self.pick_outlet_map),
            ("Draw rectangle", lambda: self.open_map_picker("Rectangle")),
            ("Draw polygon", lambda: self.open_map_picker("Polygon")),
            ("Reset Sligo demo", self.reset_sligo_demo),
            ("Documented watershed…", self.configure_documented_watershed),
            (
                "Open Sligo example",
                lambda: self.open_example("examples/SligoCreek/dem_workflow.example.yaml"),
            ),
            (
                "Open John McCormack example",
                lambda: self.open_example(
                    "examples/JohnMcCormack3600/dem_workflow.example.yaml"
                ),
            ),
        )
        for index, (label, command) in enumerate(project_specs):
            tk.Button(project_buttons, text=label, command=command).grid(
                row=index // 4, column=index % 4, padx=2, pady=2, sticky="ew"
            )
        recommended_button = tk.Button(
            project_buttons,
            text="▶ RUN RECOMMENDED NEXT STEP",
            command=self.run_recommended_step,
        )
        recommended_button.grid(row=2, column=1, padx=2, pady=2, sticky="ew")
        self.workflow_buttons.append(recommended_button)
        tk.Checkbutton(
            project_buttons,
            text="Use reviewed pour points",
            variable=self.reviewed_points_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(
            project_buttons,
            text="Overwrite existing promoted pour points",
            variable=self.overwrite_promoted_var,
        ).grid(row=3, column=2, columnspan=2, sticky="w")
        tk.Checkbutton(
            project_buttons,
            text="Use edited existing outlet.shp",
            variable=self.use_existing_outlet_var,
        ).grid(row=4, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(
            project_buttons,
            text="Offline: reuse existing downloads",
            variable=self.reuse_downloads_var,
        ).grid(row=4, column=2, columnspan=2, sticky="w")
        tk.Button(
            project_buttons,
            text="Open generated layers in QGIS",
            command=self.open_generated_layers_in_qgis,
        ).grid(row=2, column=2, padx=2, pady=2, sticky="ew")
        self.stop_button = tk.Button(
            project_buttons, text="■ STOP", command=self.stop_workflow, state="disabled"
        )
        self.stop_button.grid(row=2, column=3, padx=2, pady=2, sticky="ew")
        for column in range(4):
            project_buttons.columnconfigure(column, weight=1, uniform="project_buttons")
        dem_buttons = tk.LabelFrame(frame, text="1. DEM acquisition")
        dem_buttons.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="ew", pady=4)
        for index, step in enumerate((
            "init-dem-config",
            "prepare-dem",
            "run-dem-prep",
            "download-dem-manifest",
            "materialize-inputs",
            "validate-dem",
        )):
            button = tk.Button(
                dem_buttons, text=step, command=lambda value=step: self.run_step(value)
            )
            button.grid(row=index // 4, column=index % 4, padx=2, pady=2, sticky="ew")
            self.workflow_buttons.append(button)
            self.step_buttons[step] = button
        tk.Button(dem_buttons, text="Use expanded area", command=self.apply_expanded_area).grid(
            row=1, column=2, padx=2, pady=2, sticky="ew"
        )
        for column in range(4):
            dem_buttons.columnconfigure(column, weight=1, uniform="dem_buttons")
        ohq_buttons = tk.LabelFrame(frame, text="2. Create final OHQ file")
        ohq_buttons.grid(row=len(rows) + 2, column=0, columnspan=2, sticky="ew", pady=4)
        for index, (label, step) in enumerate((
            ("Prepare hydrology", "prepare-hydrology"),
            ("Prepare GIS inputs", "prepare-inputs"),
            ("Check inputs", "check-inputs"),
            ("Build OHQ", "build-ohq"),
            ("Continue automatically to OHQ", "run-to-ohq"),
            ("FULL RUN: download all data to OHQ", "full-run"),
            ("Promote reviewed pour points", "promote-pour-points"),
            ("Import documented watershed", "import-watershed-reference"),
        )):
            button = tk.Button(
                ohq_buttons, text=label, command=lambda value=step: self.run_step(value)
            )
            button.grid(row=index // 3, column=index % 3, padx=2, pady=2, sticky="ew")
            self.workflow_buttons.append(button)
            self.step_buttons[step] = button
        for column in range(3):
            ohq_buttons.columnconfigure(column, weight=1, uniform="ohq_buttons")
        hms_buttons = tk.LabelFrame(frame, text="3. Native HEC-HMS project")
        hms_buttons.grid(row=len(rows) + 3, column=0, columnspan=2, sticky="ew", pady=4)
        for index, (label, step) in enumerate((
            ("Build HEC-HMS", "build-hms"),
            ("Validate HEC-HMS", "validate-hms"),
        )):
            button = tk.Button(
                hms_buttons, text=label, command=lambda value=step: self.run_step(value)
            )
            button.grid(row=0, column=index, padx=2, pady=2, sticky="ew")
            self.workflow_buttons.append(button)
            self.step_buttons[step] = button
            hms_buttons.columnconfigure(index, weight=1, uniform="hms_buttons")
        self.log = tk.Text(frame, height=14, width=100)
        self.log.grid(row=len(rows) + 4, column=0, columnspan=2, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(len(rows) + 4, weight=1)

    def configure_documented_watershed(self) -> None:
        """Edit reference provenance in a compact modal instead of the main form."""
        tk = self.tk
        dialog = tk.Toplevel(self.root)
        dialog.title("Documented Watershed Reference")
        dialog.transient(self.root)
        dialog.resizable(True, False)
        fields = (
            ("Local vector / ArcGIS layer URL", self.reference_source_var),
            ("Local container layer", self.reference_layer_var),
            ("Watershed name field", self.reference_name_field_var),
            ("Exact watershed name", self.reference_name_var),
            ("Dataset title *", self.reference_title_var),
            ("Publishing organization *", self.reference_org_var),
            ("Citation URL", self.reference_url_var),
            ("License / data terms", self.reference_license_var),
        )
        tk.Label(
            dialog,
            text=(
                "Use an agency polygon or ArcGIS numeric layer URL. "
                "Images and PDFs are evidence, not polygon inputs."
            ),
            justify="left",
            wraplength=620,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 6))
        for row, (label, variable) in enumerate(fields, start=1):
            tk.Label(dialog, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=2)
            tk.Entry(dialog, textvariable=variable, width=68).grid(
                row=row, column=1, sticky="ew", pady=2
            )
            if row == 1:
                tk.Button(
                    dialog,
                    text="Browse…",
                    command=lambda: self.browse_file(self.reference_source_var),
                ).grid(row=row, column=2, padx=(4, 10), pady=2)

        def done() -> None:
            if not self.reference_source_var.get().strip():
                self.messages.put("Reference source is required.\n")
                return
            if not self.reference_title_var.get().strip() or not self.reference_org_var.get().strip():
                self.messages.put("Reference title and publishing organization are required.\n")
                return
            dialog.destroy()
            self._refresh_step_buttons()

        buttons = tk.Frame(dialog)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=3, sticky="e", padx=10, pady=10)
        tk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right", padx=3)
        tk.Button(buttons, text="Save", command=done).pack(side="right", padx=3)
        dialog.columnconfigure(1, weight=1)
        dialog.grab_set()

    def pick_outlet_map(self) -> None:
        self.open_map_picker("Outlet")

    def run_recommended_step(self) -> None:
        try:
            state = self.state()
            config_path = Path(self.config_var.get()).expanduser()
            if config_path.exists():
                state = state_with_config_defaults(state, load_project_config(config_path))
            step = recommended_workflow_step(state)
            self.messages.put(f"Recommended next step: {step}\n")
            self.run_step(step)
        except (OSError, LauncherError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            self.messages.put(f"ERROR: {exc}\n")

    def open_generated_layers_in_qgis(self) -> None:
        """Launch QGIS detached with every generated watershed workflow layer."""
        try:
            state = self.state()
            config_path = Path(self.config_var.get()).expanduser()
            if config_path.exists():
                state = state_with_config_defaults(state, load_project_config(config_path))
            command = qgis_command(state)
            subprocess.Popen(command, start_new_session=True)
            self.messages.put(f"Opened {len(command) - 2} generated layer(s) in QGIS.\n")
        except (OSError, LauncherError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            self.messages.put(f"ERROR: Could not open QGIS: {exc}\n")

    def stop_workflow(self) -> None:
        if self.runner is None or not self.command_running:
            return
        self.messages.put("Stopping the active workflow command…\n")
        self.stop_button.config(state="disabled")
        self.runner.cancel()

    def _filedialog(self):
        return importlib.import_module("tkinter.filedialog")

    def browse_file(self, variable, *, load_config: bool = False) -> None:
        selected = self._filedialog().askopenfilename(initialfile=Path(variable.get()).name)
        if selected:
            variable.set(selected)
            if load_config:
                self.load_config()

    def browse_directory(self, variable) -> None:
        current = Path(variable.get() or ".").expanduser()
        selected = self._filedialog().askdirectory(
            initialdir=str(current if current.is_dir() else current.parent)
        )
        if selected:
            variable.set(selected)

    def open_example(self, path: str) -> None:
        candidate = Path(path)
        if not candidate.is_file():
            self.messages.put(f"ERROR: Example config not found: {candidate}\n")
            return
        self.config_var.set(str(candidate))
        self.load_config()

    def open_map_picker(self, mode: str) -> None:
        try:
            existing = getattr(self, "map_picker", None)
            if existing is not None and existing.window.winfo_exists():
                existing.mode.set(mode)
                existing._mode_changed(mode)
                existing.window.title(f"{mode} selection on OpenStreetMap")
                existing.window.lift()
                return
            self.map_picker = MapPicker(self, mode=mode)
        except Exception as exc:  # pragma: no cover - Tk/network UI boundary
            self.messages.put(f"Map picker failed: {exc}\n")

    def save_drawn_area(self, points: list[tuple[float, float]]) -> None:
        config_path = Path(self.config_var.get()).expanduser()
        config = load_project_config(config_path)
        dem = config.get("dem_acquisition")
        if not isinstance(dem, dict):
            raise LauncherError("dem_acquisition must be a mapping before drawing an area.")
        area_value = dem.get("acquisition_area") or "intermediate/dem_acquisition_area.geojson"
        area_path = Path(area_value).expanduser()
        if not area_path.is_absolute():
            area_path = config_path.parent / area_path
        write_drawn_acquisition(area_path, points)
        dem["method"] = "polygon"
        dem["acquisition_area"] = _path_for_config_value(area_path, config_path)
        save_project_config(config_path, config)
        self.method_var.set("polygon")
        self.messages.put(f"Saved user-drawn acquisition polygon: {area_path}\n")

    def apply_expanded_area(self) -> None:
        try:
            config_path = Path(self.config_var.get()).expanduser()
            config = load_project_config(config_path)
            expanded = use_expanded_acquisition(config_path, config)
            save_project_config(config_path, config)
            self.load_config()
            self.messages.put(
                f"Using expanded acquisition area: {expanded}. Run prepare-dem and download again.\n"
            )
        except (OSError, ValueError, LauncherError, json.JSONDecodeError, yaml.YAMLError) as exc:
            self.messages.put(f"ERROR: {exc}\n")

    def reset_sligo_demo(self) -> None:
        try:
            from ohqbuilder.dem_workflow import write_dem_config_template

            write_dem_config_template(
                **sligo_demo_reset_args(self.config_var.get(), self.state().lon, self.state().lat)
            )
            self.load_config()
            self.messages.put("Reset Sligo demo config.\n")
        except Exception as exc:  # pragma: no cover - UI boundary
            self.messages.put(f"ERROR: {exc}\n")

    def state(self) -> LauncherState:
        crs = self.crs_var.get().strip() or None

        def optional_path(value: str) -> Path | None:
            text = value.strip()
            return Path(text).expanduser() if text else None

        def optional_float(value: str) -> float | None:
            text = value.strip()
            return float(text) if text else None

        return LauncherState(
            config_path=Path(self.config_var.get()).expanduser(),
            manifest_path=optional_path(self.manifest_var.get()),
            raw_dem_dir=optional_path(self.raw_dem_var.get()),
            root=optional_path(self.root_var.get()),
            site=self.site_var.get().strip() or None,
            source_dir=optional_path(self.source_var.get()),
            target_crs=crs,
            lon=optional_float(self.lon_var.get()),
            lat=optional_float(self.lat_var.get()),
            method=self.method_var.get().strip() or None,
            flowline_path=optional_path(self.flowline_var.get()),
            tile_index=optional_path(self.tile_index_var.get()),
            use_reviewed_pour_points=self.reviewed_points_var.get(),
            nhdplus_snap_distance_m=optional_float(self.nhdplus_snap_var.get()) or 50.0,
            overwrite_promoted_pour_points=self.overwrite_promoted_var.get(),
            use_existing_outlet=self.use_existing_outlet_var.get(),
            reuse_downloads=self.reuse_downloads_var.get(),
            reference_source=self.reference_source_var.get().strip() or None,
            reference_layer=self.reference_layer_var.get().strip() or None,
            reference_name_field=self.reference_name_field_var.get().strip() or None,
            reference_name=self.reference_name_var.get().strip() or None,
            reference_title=self.reference_title_var.get().strip() or None,
            reference_organization=self.reference_org_var.get().strip() or None,
            reference_url=self.reference_url_var.get().strip() or None,
            reference_license=self.reference_license_var.get().strip() or None,
        )

    def apply_state(self, state: LauncherState) -> None:
        self.config_var.set(str(state.config_path))
        self.manifest_var.set(str(state.manifest_path or ""))
        self.raw_dem_var.set(str(state.raw_dem_dir or ""))
        self.root_var.set(str(state.root or "."))
        self.site_var.set(state.site or "")
        self.source_var.set(str(state.source_dir or ""))
        self.crs_var.set(state.target_crs or "")
        self.lon_var.set("" if state.lon is None else str(state.lon))
        self.lat_var.set("" if state.lat is None else str(state.lat))
        self.method_var.set(state.method or "")
        self.flowline_var.set(str(state.flowline_path or ""))
        self.tile_index_var.set(str(state.tile_index or ""))
        self.reviewed_points_var.set(state.use_reviewed_pour_points)
        self.nhdplus_snap_var.set(str(state.nhdplus_snap_distance_m))
        self.overwrite_promoted_var.set(state.overwrite_promoted_pour_points)
        self.use_existing_outlet_var.set(state.use_existing_outlet)
        self.reuse_downloads_var.set(state.reuse_downloads)
        self.reference_source_var.set(state.reference_source or "")
        self.reference_layer_var.set(state.reference_layer or "")
        self.reference_name_field_var.set(state.reference_name_field or "")
        self.reference_name_var.set(state.reference_name or "")
        self.reference_title_var.set(state.reference_title or "")
        self.reference_org_var.set(state.reference_organization or "")
        self.reference_url_var.set(state.reference_url or "")
        self.reference_license_var.set(state.reference_license or "")

    def _refresh_step_buttons(self) -> None:
        """Disable commands whose on-disk prerequisites are not yet available."""

        try:
            state = self.state()
            config_path = Path(self.config_var.get()).expanduser()
            if config_path.exists():
                state = state_with_config_defaults(state, load_project_config(config_path))
            for step, button in self.step_buttons.items():
                button.config(
                    state="disabled" if workflow_prerequisite_error(step, state) else "normal"
                )
        except (OSError, LauncherError, ValueError, json.JSONDecodeError, yaml.YAMLError):
            # Keep configuration/init/full-run paths accessible while the user fixes fields.
            for step, button in self.step_buttons.items():
                button.config(state="normal" if step in {"init-dem-config", "full-run"} else "disabled")

    def load_config(self) -> None:
        try:
            config = load_project_config(self.config_var.get())
            self.apply_state(state_from_config(self.config_var.get(), config))
            self._refresh_step_buttons()
            self.messages.put("Loaded config.\n")
        except (OSError, LauncherError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            self.messages.put(f"ERROR: {exc}\n")

    def save_config(self) -> None:
        try:
            current = (
                load_project_config(self.config_var.get())
                if Path(self.config_var.get()).exists()
                else {}
            )
            save_project_config(
                self.config_var.get(), update_config_from_state(current, self.state())
            )
            self.messages.put("Saved config.\n")
        except (OSError, LauncherError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            self.messages.put(f"ERROR: {exc}\n")

    def preview_acquisition(self) -> None:
        try:
            config = load_project_config(self.config_var.get())
            dem = (
                config.get("dem_acquisition")
                if isinstance(config.get("dem_acquisition"), dict)
                else {}
            )
            area = dem.get("acquisition_area")
            if not isinstance(area, str) or not area:
                raise LauncherError("dem_acquisition.acquisition_area is not configured.")
            path = Path(area).expanduser()
            if not path.is_absolute():
                path = Path(self.config_var.get()).expanduser().parent / path
            self.messages.put(f"Acquisition preview: {geojson_preview_summary(path)}\n")
        except (OSError, LauncherError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            self.messages.put(f"ERROR: {exc}\n")

    def run_step(self, step: WorkflowStep) -> None:
        if self.command_running:
            self.messages.put(
                "A workflow command is already running. Wait for it to finish before starting the next step.\n"
            )
            return
        try:
            state = self.state()
            config_path = Path(self.config_var.get()).expanduser()
            if config_path.exists():
                state = state_with_config_defaults(state, load_project_config(config_path))
            if step == "init-dem-config" and config_path.exists():
                from tkinter import messagebox

                if not messagebox.askyesno(
                    "Replace DEM workflow configuration?",
                    "This will replace the existing DEM workflow configuration.\n\n"
                    "Review the flowline and tile-index fields first. Demo inputs are not "
                    "inserted unless explicitly requested from the CLI. Continue?",
                ):
                    self.messages.put("Initialize DEM Config cancelled; existing config preserved.\n")
                    return
                state = replace(state, overwrite_config=True)
            prerequisite = workflow_prerequisite_error(step, state)
            if prerequisite:
                raise LauncherError(prerequisite)
            command = command_for_step(step, state)
        except (OSError, LauncherError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            self.messages.put(f"ERROR: {exc}\n")
            return
        self.command_running = True
        for button in self.workflow_buttons:
            button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.runner = CommandRunner(command, self.messages)
        self.runner.start()

    def _poll_messages(self) -> None:
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            if isinstance(message, RunnerFinished):
                self.command_running = False
                self.runner = None
                self.stop_button.config(state="disabled")
                for button in self.workflow_buttons:
                    button.config(state="normal")
                self._refresh_step_buttons()
                continue
            self.log.insert("end", message)
            self.log.see("end")
        self.root.after(100, self._poll_messages)

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    try:
        LauncherApp().run()
    except LauncherError as exc:
        print(f"ui failed: {exc}")
        return 2
    return 0

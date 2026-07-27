from __future__ import annotations

import importlib
import importlib.util
import json
import math
import queue
import subprocess
import tempfile
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_TILE_SIZE = 256
OSM_CACHE_DIR = Path(tempfile.gettempdir()) / "gistoohq_osm_tiles"
MIN_MAP_ZOOM = 1
MAX_MAP_ZOOM = 19

SLIGO_DEMO_SITE = "SligoCreekDemo"
SLIGO_DEMO_LON = -76.9765
SLIGO_DEMO_LAT = 38.9921
SLIGO_DEMO_CRS = "EPSG:26918"
SLIGO_DEMO_FLOWLINES = Path("hydro/NHDFlowline.demo.geojson")
SLIGO_DEMO_TILE_INDEX = Path("indexes/usgs_3dep_tiles.demo.geojson")


def osm_tile_cache_path(zoom: int, x: int, y: int, *, cache_dir: Path = OSM_CACHE_DIR) -> Path:
    """Return the cache path for a downloaded OSM tile."""

    return cache_dir / str(zoom) / str(x) / f"{y}.png"


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


def _path_for_config_value(path: Path, config_path: Path) -> str:
    """Return a path string suitable for writing into ``config_path``."""

    config_dir = config_path.expanduser().parent
    try:
        return str(path.expanduser().relative_to(config_dir))
    except ValueError:
        return str(path)


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
    base = Path(config_path).expanduser().parent
    dem = config.get("dem_acquisition") if isinstance(config.get("dem_acquisition"), dict) else {}
    site_config = config.get("site") if isinstance(config.get("site"), dict) else {}
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}

    def path_value(value: Any) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        path = Path(value).expanduser()
        return path if path.is_absolute() else base / path

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
    )


def update_config_from_state(config: dict[str, Any], state: LauncherState) -> dict[str, Any]:
    updated = dict(config)
    _set_nested(updated, "dem_acquisition", "tile_manifest", str(state.manifest_path or ""))
    _set_nested(updated, "paths", "raw_dem_dir", str(state.raw_dem_dir or ""))
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
        _set_nested(updated, "dem_acquisition", "flowline_path", str(state.flowline_path))
    if state.tile_index is not None:
        _set_nested(updated, "dem_acquisition", "tile_index", str(state.tile_index))
    updated["root"] = str(state.root or ".")
    updated["download_dir"] = str(state.source_dir or "source_downloads")
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
    )


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

    def run(self) -> None:
        status = 0
        commands = (self.command.argv, *self.command.followup_argv)
        try:
            for argv in commands:
                self.messages.put(f"$ {' '.join(argv)}\n")
                process = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    self.messages.put(line)
                status = process.wait()
                if status != 0:
                    break
        except OSError as exc:
            status = 2
            self.messages.put(f"Could not start workflow command: {exc}\n")
        self.messages.put(f"\n[{self.command.label} exited with {status}]\n")
        if self.command.label == "Validate DEM" and status == 3:
            self.messages.put(
                "DEM validation requested a larger acquisition area. This is an actionable "
                "EXPAND result, not a crash; review the generated expanded GeoJSON, then draw "
                "or select the larger area and repeat DEM preparation.\n"
            )
        self.messages.put(RunnerFinished(status))


class MapPicker:
    """Small OpenStreetMap tile picker for choosing an outlet in the Tk launcher."""

    def __init__(
        self, app: "LauncherApp", *, zoom: int = 14, width: int = 768, height: int = 512
    ) -> None:
        self.app = app
        self.tk = app.tk
        self.zoom = clamp_zoom(zoom)
        self.width = width
        self.height = height
        self.center_lon = float(app.lon_var.get() or -76.9765)
        self.center_lat = float(app.lat_var.get() or 38.9921)
        self.images = []
        self.flowlines = self._load_flowlines()
        self.selection_points: list[tuple[float, float]] = []
        self.mode = self.tk.StringVar(value="Outlet")
        self.window = self.tk.Toplevel(app.root)
        self.window.title("Pick Outlet on OpenStreetMap")
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
        self.status.config(
            text=f"Left-click to set outlet; right-click to recenter. Center={self.center_lon:.6f}, {self.center_lat:.6f}; zoom={self.zoom}. © OpenStreetMap contributors"
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
                    self.status.config(text=f"Could not load OSM tile: {exc}")
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
        self._draw_tiles()

    def _clear_selection(self) -> None:
        self.selection_points = []
        self._draw_tiles()

    def _selection_ring(self) -> list[tuple[float, float]]:
        if self.mode.get() == "Rectangle" and len(self.selection_points) == 2:
            (x1, y1), (x2, y2) = self.selection_points
            return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
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
        cache_path = osm_tile_cache_path(self.zoom, x, y)
        if not cache_path.exists():
            url = OSM_TILE_URL.format(z=self.zoom, x=x, y=y)
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
                self._finish_area()
            return
        snapped = nearest_point_on_lines(lon, lat, self.flowlines)
        if snapped is not None:
            lon, lat = snapped
        self.app.lon_var.set(f"{lon:.8f}")
        self.app.lat_var.set(f"{lat:.8f}")
        action = (
            "Picked and snapped outlet to flowline"
            if snapped is not None
            else "Picked outlet from OSM map"
        )
        self.app.messages.put(f"{action}: lon={lon:.8f}, lat={lat:.8f}\n")
        self.window.destroy()

    def _finish_area(self) -> None:
        ring = self._selection_ring()
        try:
            self.app.save_drawn_area(ring)
        except (OSError, ValueError, LauncherError, json.JSONDecodeError, yaml.YAMLError) as exc:
            self.status.config(text=f"Could not save area: {exc}")
            return
        self.window.destroy()

    def _recenter(self, event) -> None:
        self.center_lon, self.center_lat = self._event_lonlat(event)
        self._draw_tiles()

    def _zoom(self, delta: int) -> None:
        self.zoom = clamp_zoom(self.zoom + delta)
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
        self._build()
        if Path(self.config_var.get()).exists():
            self.load_config()
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
        ]
        for row, (label, variable) in enumerate(rows):
            tk.Label(frame, text=label).grid(row=row, column=0, sticky="w")
            tk.Entry(frame, textvariable=variable, width=70).grid(row=row, column=1, sticky="ew")
        project_buttons = tk.LabelFrame(frame, text="Project and map")
        project_buttons.grid(row=len(rows), column=0, columnspan=2, sticky="ew", pady=4)
        tk.Button(project_buttons, text="Load config", command=self.load_config).pack(side="left")
        tk.Button(project_buttons, text="Save config", command=self.save_config).pack(side="left")
        tk.Button(
            project_buttons, text="Preview acquisition", command=self.preview_acquisition
        ).pack(side="left")
        tk.Button(
            project_buttons, text="Pick outlet / draw area", command=self.pick_outlet_map
        ).pack(side="left")
        tk.Button(project_buttons, text="Reset Sligo demo", command=self.reset_sligo_demo).pack(
            side="left"
        )
        dem_buttons = tk.LabelFrame(frame, text="1. DEM acquisition")
        dem_buttons.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="ew", pady=4)
        for step in (
            "init-dem-config",
            "prepare-dem",
            "run-dem-prep",
            "download-dem-manifest",
            "materialize-inputs",
            "validate-dem",
        ):
            tk.Button(dem_buttons, text=step, command=lambda value=step: self.run_step(value)).pack(
                side="left"
            )
        tk.Button(dem_buttons, text="Use expanded area", command=self.apply_expanded_area).pack(
            side="left"
        )
        ohq_buttons = tk.LabelFrame(frame, text="2. Create final OHQ file")
        ohq_buttons.grid(row=len(rows) + 2, column=0, columnspan=2, sticky="ew", pady=4)
        for label, step in (
            ("Prepare hydrology", "prepare-hydrology"),
            ("Prepare GIS inputs", "prepare-inputs"),
            ("Check inputs", "check-inputs"),
            ("Build OHQ", "build-ohq"),
            ("Continue automatically to OHQ", "run-to-ohq"),
        ):
            tk.Button(
                ohq_buttons, text=label, command=lambda value=step: self.run_step(value)
            ).pack(side="left")
        self.log = tk.Text(frame, height=24, width=100)
        self.log.grid(row=len(rows) + 3, column=0, columnspan=2, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(len(rows) + 3, weight=1)

    def pick_outlet_map(self) -> None:
        try:
            self.map_picker = MapPicker(self)
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

    def load_config(self) -> None:
        try:
            config = load_project_config(self.config_var.get())
            self.apply_state(state_from_config(self.config_var.get(), config))
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
            command = command_for_step(step, state)
        except (OSError, LauncherError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            self.messages.put(f"ERROR: {exc}\n")
            return
        self.command_running = True
        CommandRunner(command, self.messages).start()

    def _poll_messages(self) -> None:
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            if isinstance(message, RunnerFinished):
                self.command_running = False
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

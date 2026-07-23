from __future__ import annotations

import subprocess
from pathlib import Path


def _read_config(path: Path):
    import json
    import yaml

    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_config(path: Path, data) -> None:
    import json
    import yaml

    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_geojson_polygon(path: Path, coords: list[tuple[float, float]], *, source: str) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"source": source},
                        "geometry": {"type": "Polygon", "coordinates": [[list(point) for point in coords]]},
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_manifest_footprints(manifest_path: Path) -> Path | None:
    import json

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list):
        return None
    features = []
    for item in items:
        if not isinstance(item, dict):
            continue
        bounds = item.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            continue
        minx, miny, maxx, maxy = (float(value) for value in bounds)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "path": item.get("path", ""),
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [minx, miny],
                        [maxx, miny],
                        [maxx, maxy],
                        [minx, maxy],
                        [minx, miny],
                    ]],
                },
            }
        )
    if not features:
        return None
    output = manifest_path.with_name(manifest_path.stem + "_footprints.geojson")
    output.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, indent=2),
        encoding="utf-8",
    )
    return output


class OutletCaptureTool:
    def __init__(self, dock):
        from qgis.gui import QgsMapToolEmitPoint

        self.dock = dock
        self.tool = QgsMapToolEmitPoint(dock.iface.mapCanvas())
        self.tool.canvasClicked.connect(self.capture)

    def activate(self):
        self.dock.iface.mapCanvas().setMapTool(self.tool)
        self.dock.log.append("Click the outlet point on the map canvas.")

    def capture(self, point, button):
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

        canvas = self.dock.iface.mapCanvas()
        source_crs = canvas.mapSettings().destinationCrs()
        target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
        lonlat = transform.transform(point)
        self.dock.write_outlet(lonlat.x(), lonlat.y())
        self.dock.log.append(f"Outlet set to lon={lonlat.x():.8f}, lat={lonlat.y():.8f}")


class AcquisitionPolygonTool:
    def __init__(self, dock):
        from qgis.gui import QgsMapToolEmitPoint

        self.dock = dock
        self.points = []
        self.tool = QgsMapToolEmitPoint(dock.iface.mapCanvas())
        self.tool.canvasClicked.connect(self.capture)

    def activate(self):
        self.points = []
        self.dock.iface.mapCanvas().setMapTool(self.tool)
        self.dock.log.append("Left-click DEM area vertices; right-click to finish polygon.")

    def capture(self, point, button):
        from qgis.PyQt.QtCore import Qt
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

        if button == Qt.RightButton:
            self.finish()
            return
        canvas = self.dock.iface.mapCanvas()
        source_crs = canvas.mapSettings().destinationCrs()
        target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
        lonlat = transform.transform(point)
        self.points.append((lonlat.x(), lonlat.y()))
        self.dock.log.append(f"Added DEM area vertex {len(self.points)}: {lonlat.x():.8f}, {lonlat.y():.8f}")

    def finish(self):
        if len(self.points) < 3:
            self.dock.log.append("Need at least three vertices for DEM acquisition polygon.")
            return
        coords = [*self.points, self.points[0]]
        self.dock.write_acquisition_polygon(coords, "qgis_drawn_polygon")
        self.dock.log.append("Wrote DEM acquisition polygon from clicked vertices.")


class DemWorkflowDock:
    """QGIS dock skeleton that delegates workflow work to ohqbuild commands."""

    def __init__(self, iface):
        from qgis.PyQt.QtWidgets import (
            QDockWidget,
            QWidget,
            QVBoxLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QPushButton,
            QTextEdit,
        )

        self.iface = iface
        self.widget = QDockWidget("GIStoOHQ DEM Workflow", iface.mainWindow())
        self.panel = QWidget(self.widget)
        layout = QVBoxLayout(self.panel)
        row = QHBoxLayout()
        row.addWidget(QLabel("Config"))
        self.config = QLineEdit("config.example.json")
        row.addWidget(self.config)
        layout.addLayout(row)
        outlet_button = QPushButton("Pick Outlet on Map")
        outlet_button.clicked.connect(self.pick_outlet)
        layout.addWidget(outlet_button)
        extent_button = QPushButton("Use Canvas Extent as DEM Area")
        extent_button.clicked.connect(self.use_canvas_extent_as_area)
        layout.addWidget(extent_button)
        draw_button = QPushButton("Draw DEM Area Polygon")
        draw_button.clicked.connect(self.draw_acquisition_polygon)
        layout.addWidget(draw_button)
        for label, command in (
            ("Prepare DEM", "prepare-dem"),
            ("Download DEM Tiles", "download-dem-manifest"),
            ("Materialize Inputs", "materialize-inputs"),
            ("Validate DEM", "validate-dem"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=command: self.run_command(value))
            layout.addWidget(button)
        load_button = QPushButton("Load Configured Layers")
        load_button.clicked.connect(self.load_configured_layers)
        layout.addWidget(load_button)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)
        self.widget.setWidget(self.panel)

    def __getattr__(self, name):
        return getattr(self.widget, name)

    def pick_outlet(self) -> None:
        self.outlet_tool = OutletCaptureTool(self)
        self.outlet_tool.activate()

    def draw_acquisition_polygon(self) -> None:
        self.polygon_tool = AcquisitionPolygonTool(self)
        self.polygon_tool.activate()

    def write_outlet(self, lon: float, lat: float) -> None:
        config_path = Path(self.config.text()).expanduser()
        data = _read_config(config_path)
        if not isinstance(data, dict):
            data = {}
        outlet = data.setdefault("outlet", {})
        if not isinstance(outlet, dict):
            outlet = {}
            data["outlet"] = outlet
        outlet["longitude"] = lon
        outlet["latitude"] = lat
        outlet.setdefault("input_crs", "EPSG:4326")
        _write_config(config_path, data)

    def write_acquisition_polygon(self, coords: list[tuple[float, float]], source: str) -> Path:
        config_path = Path(self.config.text()).expanduser()
        data = _read_config(config_path)
        if not isinstance(data, dict):
            data = {}
        dem = data.setdefault("dem_acquisition", {})
        if not isinstance(dem, dict):
            dem = {}
            data["dem_acquisition"] = dem
        area_value = dem.get("acquisition_area") or "intermediate/dem_acquisition_area.geojson"
        area_path = Path(area_value).expanduser()
        if not area_path.is_absolute():
            area_path = config_path.parent / area_path
        _write_geojson_polygon(area_path, coords, source=source)
        dem["method"] = "polygon"
        dem["acquisition_area"] = str(area_path if area_path.is_absolute() else area_value)
        _write_config(config_path, data)
        return area_path

    def use_canvas_extent_as_area(self) -> None:
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        source_crs = canvas.mapSettings().destinationCrs()
        target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
        corners = [
            transform.transform(extent.xMinimum(), extent.yMinimum()),
            transform.transform(extent.xMaximum(), extent.yMinimum()),
            transform.transform(extent.xMaximum(), extent.yMaximum()),
            transform.transform(extent.xMinimum(), extent.yMaximum()),
        ]
        coords = [(point.x(), point.y()) for point in corners]
        coords.append(coords[0])
        area_path = self.write_acquisition_polygon(coords, "qgis_canvas_extent")
        self.log.append(f"Wrote DEM acquisition area from canvas extent: {area_path}")

    def run_command(self, command: str) -> None:
        argv = ["ohqbuild", command, "--config", self.config.text()]
        self.log.append("$ " + " ".join(argv))
        process = subprocess.run(argv, capture_output=True, text=True, check=False)
        if process.stdout:
            self.log.append(process.stdout)
        if process.stderr:
            self.log.append(process.stderr)
        self.log.append(f"[{command} exited with {process.returncode}]")

    def load_configured_layers(self) -> None:
        from qgis.core import QgsProject, QgsVectorLayer

        config_path = Path(self.config.text()).expanduser()
        data = _read_config(config_path)
        dem = data.get("dem_acquisition", {}) if isinstance(data, dict) else {}
        layer_values = {
            key: dem.get(key)
            for key in ("acquisition_area", "expanded_acquisition_area", "watershed_boundary", "tile_index")
        }
        manifest_value = dem.get("tile_manifest")
        if manifest_value:
            manifest_path = Path(manifest_value).expanduser()
            if not manifest_path.is_absolute():
                manifest_path = config_path.parent / manifest_path
            if manifest_path.exists():
                footprint_path = _write_manifest_footprints(manifest_path)
                if footprint_path is not None:
                    layer_values["selected_tile_footprints"] = str(footprint_path)
        for key, value in layer_values.items():
            if not value:
                continue
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = config_path.parent / path
            if path.exists():
                layer = QgsVectorLayer(str(path), key, "ogr")
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                    self.log.append(f"Loaded layer: {path}")

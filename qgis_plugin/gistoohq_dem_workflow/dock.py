from __future__ import annotations

import subprocess
from pathlib import Path


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
        import json
        import yaml
        from qgis.core import QgsProject, QgsVectorLayer

        config_path = Path(self.config.text()).expanduser()
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.suffix.lower() != ".json" else json.loads(config_path.read_text(encoding="utf-8"))
        dem = data.get("dem_acquisition", {}) if isinstance(data, dict) else {}
        for key in ("acquisition_area", "expanded_acquisition_area", "watershed_boundary"):
            value = dem.get(key)
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

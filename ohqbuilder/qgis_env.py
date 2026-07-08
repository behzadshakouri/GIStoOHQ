from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def module_available(name: str) -> bool:
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ModuleNotFoundError, ValueError):
        return False


def qgis_plugin_paths() -> list[Path]:
    paths: list[Path] = []
    try:
        from qgis.core import QgsApplication
    except ImportError:
        return paths

    prefix = Path(QgsApplication.prefixPath()) if QgsApplication.prefixPath() else None
    if prefix:
        paths.extend([
            prefix / "python" / "plugins",
            prefix / "share" / "qgis" / "python" / "plugins",
        ])
    paths.append(Path(sys.prefix) / "share" / "qgis" / "python" / "plugins")
    paths.append(Path("/usr/share/qgis/python/plugins"))
    return paths


def ensure_processing_available() -> bool:
    if module_available("processing"):
        return True
    for path in qgis_plugin_paths():
        if path.is_dir() and str(path) not in sys.path:
            sys.path.append(str(path))
        if module_available("processing"):
            return True
    return False

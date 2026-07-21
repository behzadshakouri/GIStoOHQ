from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

_QGIS_APP = None


def module_available(name: str) -> bool:
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ModuleNotFoundError, ValueError):
        return False


def _qgis_prefix() -> Path:
    return Path(os.environ.get("QGIS_PREFIX_PATH", "/usr"))


def qgis_plugin_paths() -> list[Path]:
    paths: list[Path] = []
    try:
        from qgis.core import QgsApplication
    except ImportError:
        return paths

    if not QgsApplication.prefixPath():
        QgsApplication.setPrefixPath(str(_qgis_prefix()), True)
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


GRASS_PROVIDER_CLASSES = (
    ("grassprovider.Grass7AlgorithmProvider", "Grass7AlgorithmProvider"),
    ("grassprovider.GrassProvider", "GrassProvider"),
    ("processing.algs.grass7.Grass7AlgorithmProvider", "Grass7AlgorithmProvider"),
    ("processing.algs.grass.GrassAlgorithmProvider", "GrassAlgorithmProvider"),
)


def ensure_qgis_application() -> bool:
    """Create a minimal QgsApplication for standalone CLI checks when needed."""

    global _QGIS_APP
    if not module_available("qgis.core"):
        return False
    try:
        from qgis.core import QgsApplication
    except ImportError:
        return False

    if QgsApplication.instance() is not None:
        return True
    if not QgsApplication.prefixPath():
        QgsApplication.setPrefixPath(str(_qgis_prefix()), True)
    _QGIS_APP = QgsApplication([], False)
    _QGIS_APP.initQgis()
    return True


def initialize_processing() -> bool:
    """Initialize QGIS Processing when the loaded processing module exposes it."""

    if not ensure_qgis_application() or not ensure_processing_available():
        return False
    import processing

    processing_class = getattr(processing, "Processing", None)
    initialize = getattr(processing_class, "initialize", None)
    if initialize is not None:
        try:
            initialize()
        except Exception:
            pass
    return True


def _module_spec_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def register_grass_provider() -> bool:
    """Explicitly register the QGIS GRASS provider in standalone Python runs."""

    if not ensure_qgis_application() or not initialize_processing():
        return False
    from qgis.core import QgsApplication

    registry = QgsApplication.processingRegistry()
    if registry.algorithmById("grass:r.watershed") or registry.algorithmById("grass7:r.watershed"):
        return True
    for path in qgis_plugin_paths():
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
    for module_name, class_name in GRASS_PROVIDER_CLASSES:
        if not _module_spec_available(module_name):
            continue
        module = importlib.import_module(module_name)
        provider_class = getattr(module, class_name)
        provider = provider_class()
        load = getattr(provider, "load", None)
        if load is not None:
            load()
        registry.addProvider(provider)
        if registry.algorithmById("grass:r.watershed") or registry.algorithmById("grass7:r.watershed"):
            return True
    return False


def processing_algorithm_available(*algorithm_ids: str) -> bool:
    """Return True when QGIS Processing has at least one requested algorithm."""

    if not ensure_qgis_application() or not initialize_processing():
        return False
    from qgis.core import QgsApplication

    register_grass_provider()
    registry = QgsApplication.processingRegistry()
    return any(registry.algorithmById(algorithm_id) for algorithm_id in algorithm_ids)

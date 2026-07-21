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


def add_qgis_plugin_paths() -> None:
    for path in reversed(qgis_plugin_paths()):
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


def ensure_processing_available() -> bool:
    add_qgis_plugin_paths()
    return module_available("processing")


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




def register_native_provider() -> bool:
    """Explicitly register QGIS Native algorithms in standalone Python runs."""

    if not ensure_qgis_application():
        return False
    if not _module_spec_available("qgis.analysis"):
        return False
    from qgis.analysis import QgsNativeAlgorithms
    from qgis.core import QgsApplication

    registry = QgsApplication.processingRegistry()
    if registry.providerById("native") is not None:
        return True
    registry.addProvider(QgsNativeAlgorithms())
    return registry.providerById("native") is not None


def _processing_class():
    import processing

    processing_class = getattr(processing, "Processing", None)
    if processing_class is not None:
        return processing_class
    module_name = "processing.core.Processing"
    if not _module_spec_available(module_name):
        return None
    module = importlib.import_module(module_name)
    return getattr(module, "Processing", None)


def initialize_processing() -> bool:
    """Initialize QGIS Processing when the loaded processing module exposes it."""

    if not ensure_qgis_application() or not ensure_processing_available():
        return False
    processing_class = _processing_class()
    initialize = getattr(processing_class, "initialize", None)
    if initialize is not None:
        try:
            initialize()
        except Exception:
            pass
    register_native_provider()
    return True


def _module_spec_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _current_algorithm_ids() -> set[str]:
    from qgis.core import QgsApplication

    return {algorithm.id() for algorithm in QgsApplication.processingRegistry().algorithms()}


def registered_algorithm_ids() -> set[str]:
    """Return all registered QGIS Processing algorithm IDs after initialization."""

    if not ensure_qgis_application() or not initialize_processing():
        return set()
    register_native_provider()
    register_grass_provider()
    return _current_algorithm_ids()


def registered_provider_ids() -> list[str]:
    """Return registered QGIS Processing provider IDs after initialization."""

    if not ensure_qgis_application() or not initialize_processing():
        return []
    register_native_provider()
    register_grass_provider()
    from qgis.core import QgsApplication

    return sorted(provider.id() for provider in QgsApplication.processingRegistry().providers())


def _registered_algorithm_ids() -> set[str]:
    return _current_algorithm_ids()


def _has_grass_watershed_algorithm() -> bool:
    return bool({"grass:r.watershed", "grass7:r.watershed"} & _registered_algorithm_ids())


def register_grass_provider() -> bool:
    """Explicitly register the QGIS GRASS provider in standalone Python runs."""

    if not ensure_qgis_application() or not initialize_processing():
        return False
    from qgis.core import QgsApplication

    registry = QgsApplication.processingRegistry()
    if _has_grass_watershed_algorithm():
        return True
    add_qgis_plugin_paths()
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
        if _has_grass_watershed_algorithm():
            return True
    return False


def processing_algorithm_available(*algorithm_ids: str) -> bool:
    """Return True when QGIS Processing has at least one requested algorithm."""

    if not ensure_qgis_application() or not initialize_processing():
        return False
    register_native_provider()
    register_grass_provider()
    registered = _registered_algorithm_ids()
    return any(algorithm_id in registered for algorithm_id in algorithm_ids)

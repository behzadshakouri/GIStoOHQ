"""Small I/O helpers shared by retained legacy QGIS scripts."""

import os
from pathlib import Path


SHAPEFILE_SIDECARS = (
    ".shp",
    ".shx",
    ".dbf",
    ".prj",
    ".cpg",
    ".qpj",
    ".sbn",
    ".sbx",
    ".shp.xml",
)


def _source_path(source: str) -> str:
    """Strip QGIS provider suffixes such as ``|layername=...`` from a source."""

    return source.split("|", 1)[0]


def _remove_loaded_layers(path: Path) -> None:
    """Drop QGIS project layers pointing at ``path`` so files can be overwritten."""

    from qgis.core import QgsProject

    target = os.path.normcase(os.path.abspath(path))
    project = QgsProject.instance()
    for layer in list(project.mapLayers().values()):
        source = os.path.normcase(os.path.abspath(_source_path(layer.source())))
        if source == target:
            project.removeMapLayer(layer.id())


def release_and_delete(path: str | os.PathLike[str]) -> bool:
    """Release loaded QGIS layers for ``path`` and delete the on-disk dataset.

    Legacy processing scripts frequently overwrite GeoPackages and Shapefiles.
    A loaded QGIS layer can hold the target open, especially on desktop/Windows
    runs, so this helper first removes matching layers from the active project
    and then deletes the target dataset. Shapefile sidecars are removed as a
    complete dataset.
    """

    target = Path(path).expanduser().resolve()
    _remove_loaded_layers(target)

    deleted = False
    if target.suffix.lower() == ".shp":
        base = target.with_suffix("")
        for suffix in SHAPEFILE_SIDECARS:
            sidecar = base.with_suffix(suffix)
            if sidecar.exists():
                _remove_loaded_layers(sidecar)
                sidecar.unlink()
                deleted = True
        return deleted

    if target.exists():
        target.unlink()
        deleted = True
    return deleted

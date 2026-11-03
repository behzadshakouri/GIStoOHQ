"""Shared provenance manifest for the generic GIS source downloaders.

Every downloader in :mod:`ohqbuilder.sources` writes one manifest entry per
output layer, recording exactly what was asked of the provider and what came
back: the source URL and query parameters, layer metadata, download time,
feature count, CRS, and the output layer name actually written to the
GeoPackage. This is read-back provenance, not a cache or request log -- it
does not deduplicate or reuse prior downloads the way
``watershed_data.catalog.AssetCatalog`` does for site-centric acquisitions.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SourceLayerRecord:
    """Provenance for one layer written to the output GeoPackage."""

    output_layer: str
    source_url: str
    query_parameters: dict[str, Any]
    feature_count: int
    crs: str | None
    layer_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_layer": self.output_layer,
            "source_url": self.source_url,
            "query_parameters": dict(self.query_parameters),
            "feature_count": self.feature_count,
            "crs": self.crs,
            "layer_metadata": dict(self.layer_metadata),
        }


def build_manifest(
    *,
    provider: str,
    source: str,
    output_path: str | Path,
    records: Sequence[SourceLayerRecord],
    downloaded_at: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the manifest document for one download run.

    ``provider`` identifies the downloader (for example ``"arcgis_rest"`` or
    ``"socrata"``); ``source`` is the service URL or domain/dataset the
    layers came from. ``extra`` is merged in verbatim for provider-specific
    fields (for example the originating MapServer/FeatureServer root URL)
    without widening this shared schema's required keys.
    """
    if not records:
        raise ValueError("A manifest requires at least one layer record")
    document: dict[str, Any] = {
        "provider": provider,
        "source": source,
        "output_path": str(output_path),
        "downloaded_at": downloaded_at or utc_now_iso(),
        "layers": [record.to_dict() for record in records],
    }
    if extra:
        document.update(extra)
    return document


def write_manifest(path: str | Path, manifest: dict[str, Any]) -> Path:
    """Atomically write ``manifest`` as pretty, deterministically-ordered JSON."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target

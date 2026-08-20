from __future__ import annotations

import mimetypes
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any

import yaml

from .catalog import AssetCatalog, ObjectStore
from .schemas import SiteSpec, WatershedDataError, canonical_request_key


def write_site_spec(
    path: str | Path,
    *,
    site_id: str,
    name: str | None,
    longitude: float,
    latitude: float,
    start: str,
    end: str,
    overwrite: bool = False,
) -> Path:
    output = Path(path).expanduser().resolve()
    if output.exists() and not overwrite:
        raise WatershedDataError(f"SiteSpec already exists: {output}; use --force to replace it")
    spec = SiteSpec.from_dict(
        {
            "site_id": site_id,
            "name": name or site_id,
            "geometry": {"outlet": {"longitude": longitude, "latitude": latitude}},
            "study_period": {"start": start, "end": end},
            "target_timestep": "1h",
            "sources": {
                "discharge": {"selection": "auto", "policy_version": "1.0"},
                "meteorology": {"selection": "auto", "policy_version": "1.0"},
                "pet": {"selection": "auto", "policy_version": "1.0"},
            },
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(spec.to_dict(), sort_keys=False), encoding="utf-8")
    return output


def acquire_url(
    *,
    url: str,
    provider: str,
    product: str,
    product_version: str,
    cache: str | Path,
    catalog: str | Path,
    parameters: dict[str, Any] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Acquire one explicitly declared URL into the generic immutable store."""

    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise WatershedDataError("provider product URL must be an absolute HTTPS URL")
    request_key = canonical_request_key(provider, url, parameters or {}, product_version)
    catalog_store = AssetCatalog(catalog)
    if not refresh and (cached := catalog_store.cached_request(request_key, cache)) is not None:
        return cached
    request = urllib.request.Request(url, headers={"User-Agent": "GIStoOHQ/0.1"})
    try:
        with urllib.request.urlopen(request) as response:
            stored = ObjectStore(cache).put(response)
            media_type = response.headers.get_content_type()
    except OSError as exc:
        raise WatershedDataError(f"could not acquire {url}: {exc}") from exc
    if media_type == "application/octet-stream":
        media_type = mimetypes.guess_type(url)[0] or media_type
    return catalog_store.register(
        {
            "provider": provider,
            "product": product,
            "product_version": product_version,
            "request_key": request_key,
            "content_digest": stored.content_digest,
            "size": stored.size,
            "media_type": media_type,
            "source_url": url,
            "processing_status": "native",
        }
    )

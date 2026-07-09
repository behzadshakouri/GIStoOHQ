from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/tabular/post.rest"


def sanitize(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "site"


def bbox_wkt(bounds: tuple[float, float, float, float], buffer: float = 0.0) -> str:
    minx, miny, maxx, maxy = bounds
    minx -= buffer
    miny -= buffer
    maxx += buffer
    maxy += buffer
    return (
        "POLYGON (("
        f"{minx} {miny}, {maxx} {miny}, {maxx} {maxy}, {minx} {maxy}, {minx} {miny}"
        "))"
    )


def post_json(url: str, payload: dict[str, Any], *, retries: int = 3, timeout: float = 60.0) -> Any:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed USDA endpoint by default
                return json.loads(response.read().decode("utf-8"))
        except OSError as exc:
            last_error = exc
            if attempt + 1 == retries:
                break
            time.sleep(2**attempt)
    raise RuntimeError(f"USDA SDA request failed after {retries} attempts: {last_error}")


def query_sda(sql: str, *, url: str = SDA_URL, retries: int = 3, timeout: float = 60.0) -> list[dict[str, Any]]:
    data = post_json(url, {"format": "JSON+COLUMNNAME", "query": sql}, retries=retries, timeout=timeout)
    table = data.get("Table") if isinstance(data, dict) else None
    if not table:
        return []
    header = table[0]
    return [dict(zip(header, row)) for row in table[1:]]


def site_soils_dir(root: str | Path, site: str) -> Path:
    path = Path(root).expanduser().resolve() / site / "soils"
    path.mkdir(parents=True, exist_ok=True)
    return path

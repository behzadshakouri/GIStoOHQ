from __future__ import annotations

import http.client
import time
from typing import Any, Callable

from .schemas import WatershedDataError


def download_bytes(
    resource: Any,
    *,
    opener: Callable[..., object],
    timeout: float,
    label: str,
    attempts: int = 3,
    base_delay: float = 0.25,
) -> tuple[bytes, Any, int]:
    """Read a complete provider response with bounded transient retries."""
    if attempts < 1:
        raise WatershedDataError("download attempts must be at least one")
    failures = []
    for attempt in range(1, attempts + 1):
        try:
            with opener(resource, timeout=timeout) as response:
                return response.read(), getattr(response, "headers", None), attempt
        except (OSError, http.client.HTTPException) as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
            if attempt < attempts:
                time.sleep(base_delay * (2 ** (attempt - 1)))
    detail = failures[-1] if failures else "unknown provider error"
    raise WatershedDataError(f"{label} failed after {attempts} attempts: {detail}")

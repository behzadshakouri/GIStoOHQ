"""Apply an operator-reviewed categorical land-cover crosswalk for the CN path."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_crosswalk(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("source_dataset", "source_year", "source_url", "method", "classes"):
        if not data.get(key):
            raise ValueError(f"Land-cover crosswalk requires {key}")
    if not isinstance(data["classes"], dict):
        raise ValueError("Land-cover classes must map source integer codes to NLCD codes")
    lookup = Path(__file__).resolve().parent.parent / "cn_lookup.csv"
    with lookup.open(newline="", encoding="utf-8") as stream:
        accepted = {int(row["nlcd"]) for row in csv.DictReader(stream)}
    classes = {}
    for source, target in data["classes"].items():
        if isinstance(target, bool) or not isinstance(target, int) or target not in accepted:
            raise ValueError(f"NLCD target {target!r} is absent from cn_lookup.csv")
        try:
            code = int(source)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid source land-cover class {source!r}") from exc
        if code < 0 or code > 65535 or str(code) != source:
            raise ValueError(f"Invalid source land-cover class {source!r}")
        classes[code] = target
    data["classes"] = classes
    return data


def map_landcover(source: str | Path, crosswalk: str | Path, output: str | Path) -> Path:
    """Write a categorical raster and provenance JSON; reject uncovered classes."""

    import numpy as np
    import rasterio

    src = Path(source).expanduser().resolve()
    dst = Path(output).expanduser().resolve()
    if src == dst:
        raise ValueError("Input and output land-cover paths must differ")
    mapping = read_crosswalk(crosswalk)
    dst.parent.mkdir(parents=True, exist_ok=True)
    temporary = dst.with_name(dst.name + ".partial")
    counts: dict[int, int] = {}
    try:
        with rasterio.open(src) as raster:
            if raster.count != 1 or raster.crs is None or raster.nodata is None:
                raise ValueError("Land cover needs one band, a CRS, and explicit nodata")
            if not np.issubdtype(np.dtype(raster.dtypes[0]), np.integer):
                raise ValueError("Land-cover classes must be stored as integers")
            profile = raster.profile.copy()
            profile.update(driver="GTiff", dtype="uint8", count=1, nodata=255)
            with rasterio.open(temporary, "w", **profile) as mapped:
                for _, window in raster.block_windows(1):
                    block = raster.read(1, window=window, masked=True)
                    values = block.compressed()
                    if values.size:
                        unique, quantities = np.unique(values, return_counts=True)
                        unknown = [int(value) for value in unique if int(value) not in mapping["classes"]]
                        if unknown:
                            raise ValueError(f"Unmapped land-cover class(es): {unknown}")
                        for value, quantity in zip(unique, quantities):
                            code = int(value)
                            counts[code] = counts.get(code, 0) + int(quantity)
                    result = np.full(block.shape, 255, dtype="uint8")
                    for code, target in mapping["classes"].items():
                        result[(block.data == code) & ~np.ma.getmaskarray(block)] = target
                    mapped.write(result, 1, window=window)
        if not counts:
            raise ValueError("Land-cover raster has no valid classified pixels")
        crosswalk_path = Path(crosswalk).expanduser().resolve()
        source_hash = _sha256_file(src)
        crosswalk_hash = _sha256_file(crosswalk_path)
        provenance = {
            "source_path": str(src), "source_sha256": source_hash,
            "crosswalk_path": str(crosswalk_path), "crosswalk_sha256": crosswalk_hash,
            "source_dataset": mapping["source_dataset"], "source_year": mapping["source_year"],
            "source_url": mapping["source_url"], "method": mapping["method"],
            "classes": {str(k): v for k, v in mapping["classes"].items()},
            "source_pixel_counts": {str(k): v for k, v in counts.items()},
            "note": "NLCD-compatible class codes; source is not NLCD. CN and impervious fractions require local review.",
        }
        sidecar = dst.with_name(dst.name + ".provenance.json")
        sidecar_tmp = sidecar.with_name(sidecar.name + ".partial")
        try:
            sidecar_tmp.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
            temporary.replace(dst)
            sidecar_tmp.replace(sidecar)
        finally:
            sidecar_tmp.unlink(missing_ok=True)
        return dst
    finally:
        temporary.unlink(missing_ok=True)

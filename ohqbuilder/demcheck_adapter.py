from __future__ import annotations

import csv
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .phase1_fetcher import write_outlet_shapefile


class DemcheckError(RuntimeError):
    """Raised when the external DEMDownloader/demcheck command fails."""


@dataclass(frozen=True)
class DemcheckResult:
    site_path: Path
    download_dir: Path
    outlet_path: Path


def find_demcheck(explicit_path: str | Path | None = None) -> Path | None:
    """Resolve an explicit demcheck executable or discover it on PATH."""
    if explicit_path:
        candidate = Path(explicit_path).expanduser().resolve()
        if not candidate.is_file():
            raise DemcheckError(f"DEMDownloader demcheck executable not found: {candidate}")
        return candidate
    discovered = shutil.which("demcheck")
    return Path(discovered).resolve() if discovered else None


def download_with_demcheck(
    executable: str | Path,
    root: str | Path,
    site: str,
    *,
    lon: float,
    lat: float,
    buffer_m: float = 5000.0,
    download_dir: str | Path | None = None,
) -> DemcheckResult:
    """Run ArashMassoudieh/DEMDownloader for one site using its CSV interface."""
    program = find_demcheck(executable)
    if program is None:  # pragma: no cover - explicit executable always resolves
        raise DemcheckError("DEMDownloader demcheck executable was not found.")
    root_path = Path(root).expanduser().resolve()
    site_path = root_path / site
    outputs = site_path / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (site_path / "demlr").mkdir(parents=True, exist_ok=True)
    downloads = (
        Path(download_dir).expanduser().resolve()
        if download_dir
        else site_path / "source_downloads"
    )
    outlet = write_outlet_shapefile(outputs / "outlet.shp", lon, lat)

    with tempfile.TemporaryDirectory() as temporary:
        coordinates = Path(temporary) / "site.csv"
        with coordinates.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["site_id", "lat", "lon"])
            writer.writeheader()
            writer.writerow({"site_id": site_path.name, "lat": lat, "lon": lon})
        command = [
            str(program),
            str(coordinates),
            "--id-col",
            "site_id",
            "--products",
            "all",
            "--download",
            str(downloads),
            "--buffer",
            str(buffer_m),
        ]
        completed = subprocess.run(command, text=True, capture_output=True)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise DemcheckError(
                f"demcheck exited with code {completed.returncode}: {detail}"
            )
    return DemcheckResult(site_path, downloads, outlet)

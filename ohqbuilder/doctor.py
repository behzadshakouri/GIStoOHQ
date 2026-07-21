from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from .legacy_inputs import default_script_dir
from .qgis_env import ensure_processing_available, module_available, processing_algorithm_available


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    required: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "required": self.required,
        }


@dataclass
class DoctorReport:
    checks: list[DoctorCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok or not check.required for check in self.checks)

    def lines(self) -> list[str]:
        out = []
        for check in self.checks:
            status = "OK" if check.ok else ("ERROR" if check.required else "WARN")
            out.append(f"{status}: {check.name} - {check.detail}")
        return out

    def to_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "checks": [check.to_dict() for check in self.checks]}


def _script_check(script_dir: Path, filename: str) -> DoctorCheck:
    path = script_dir / filename
    return DoctorCheck(
        name=filename,
        ok=path.is_file(),
        detail=str(path),
        required=True,
    )


def run_doctor(script_dir: str | Path | None = None, strict_gis: bool = False) -> DoctorReport:
    report = DoctorReport()
    report.checks.append(
        DoctorCheck(
            name="python",
            ok=sys.version_info >= (3, 9),
            detail=sys.version.split()[0],
            required=True,
        )
    )
    report.checks.append(
        DoctorCheck(
            name="pyyaml",
            ok=module_available("yaml"),
            detail="required runtime dependency",
            required=True,
        )
    )
    report.checks.append(
        DoctorCheck(
            name="geopandas",
            ok=module_available("geopandas"),
            detail="needed for GeoPackage reading/schema checks; install with pip install -e .[gis]",
            required=False,
        )
    )
    report.checks.append(
        DoctorCheck(
            name="qgis",
            ok=module_available("qgis.core"),
            detail="needed for prepare-inputs/run without --skip-prepare",
            required=strict_gis,
        )
    )
    report.checks.append(
        DoctorCheck(
            name="qgis processing",
            ok=ensure_processing_available(),
            detail="needed by retained QGIS scripts such as clip_only.py",
            required=strict_gis,
        )
    )

    resolved_script_dir = Path(script_dir).expanduser().resolve() if script_dir else default_script_dir()
    report.checks.append(
        DoctorCheck(
            name="qgis grass provider",
            ok=processing_algorithm_available("grass:r.watershed", "grass7:r.watershed"),
            detail="needed by prepare-hydrology for r.watershed; install/enable QGIS GRASS provider",
            required=strict_gis,
        )
    )

    report.checks.append(
        DoctorCheck(
            name="legacy script directory",
            ok=resolved_script_dir.is_dir(),
            detail=str(resolved_script_dir),
            required=True,
        )
    )
    report.checks.append(_script_check(resolved_script_dir, "run_phase1.py"))
    report.checks.append(_script_check(resolved_script_dir, "run_phase2.py"))
    return report

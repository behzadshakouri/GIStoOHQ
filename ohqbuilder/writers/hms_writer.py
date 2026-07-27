from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..model.watershed import Watershed


@dataclass(frozen=True)
class HmsProjectResult:
    project_file: Path
    basin_file: Path
    meteorology_file: Path
    control_file: Path
    run_file: Path


def _safe(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "_-" else "_" for character in value
    )
    return "_".join(cleaned.split()) or "GIStoOHQ"


class HmsWriter:
    """Write a native text HEC-HMS project skeleton from validated GIStoOHQ inputs."""

    def write(self, watershed: Watershed, output_dir: str | Path) -> HmsProjectResult:
        destination = Path(output_dir).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        name = _safe(watershed.name)
        basin_name = f"{name}_Basin"
        met_name = f"{name}_Meteorology"
        control_name = f"{name}_Control"
        run_name = f"{name}_Run"
        project = destination / f"{name}.hms"
        basin = destination / f"{basin_name}.basin"
        meteorology = destination / f"{met_name}.met"
        control = destination / f"{control_name}.control"
        run = destination / f"{run_name}.run"

        project.write_text(
            f"Project: {name}\n     Version: 4.12\nEnd:\n\n"
            f"Basin: {basin_name}\n     Filename: {basin.name}\nEnd:\n\n"
            f"Meteorology: {met_name}\n     Filename: {meteorology.name}\nEnd:\n\n"
            f"Control: {control_name}\n     Filename: {control.name}\nEnd:\n\n"
            f"Run: {run_name}\n     Filename: {run.name}\nEnd:\n",
            encoding="utf-8",
        )
        basin.write_text(self._basin_text(watershed, basin_name), encoding="utf-8")
        meteorology.write_text(
            f"Meteorologic Model: {met_name}\n"
            "     Version: 4.12\n     Precipitation: None\n     Unit System: Metric\nEnd:\n",
            encoding="utf-8",
        )
        control.write_text(
            f"Control: {control_name}\n     Version: 4.12\n"
            "     Start Date: 01 January 2000\n     Start Time: 00:00\n"
            "     End Date: 02 January 2000\n     End Time: 00:00\n"
            "     Time Interval: 15\nEnd:\n",
            encoding="utf-8",
        )
        run.write_text(
            f"Run: {run_name}\n     Version: 4.12\n     Basin: {basin_name}\n"
            f"     Meteorology: {met_name}\n     Control: {control_name}\nEnd:\n",
            encoding="utf-8",
        )
        return HmsProjectResult(project, basin, meteorology, control, run)

    def _basin_text(self, watershed: Watershed, basin_name: str) -> str:
        blocks = [f"Basin: {basin_name}\n     Version: 4.12\n     Unit System: Metric\nEnd:\n"]
        for subbasin in watershed.subbasins:
            lines = [f"Subbasin: {_safe(subbasin.name)}"]
            if subbasin.downstream:
                lines.append(f"     Downstream: {_safe(subbasin.downstream)}")
            if subbasin.area_km2 is not None:
                lines.append(f"     Area: {subbasin.area_km2:.6g}")
            lines.extend(("     LossRate: SCS Curve Number",))
            if subbasin.curve_number is not None:
                lines.append(f"     Curve Number: {subbasin.curve_number:.6g}")
            lines.append("     Transform: SCS Unit Hydrograph")
            if subbasin.lag_min is not None:
                lines.append(f"     Lag: {subbasin.lag_min:.6g}")
            blocks.append("\n".join(lines) + "\nEnd:\n")
        for reach in watershed.reaches:
            lines = [f"Reach: {_safe(reach.name)}"]
            if reach.downstream:
                lines.append(f"     Downstream: {_safe(reach.downstream)}")
            lines.extend(("     Route: Muskingum", "     Muskingum X: 0.2"))
            length = reach.length_m or 1.0
            slope = max(reach.slope or 0.0005, 0.0005)
            lines.append(f"     Muskingum K: {max(length / (1000.0 * slope**0.5), 0.1):.6g}")
            blocks.append("\n".join(lines) + "\nEnd:\n")
        for junction in watershed.junctions:
            lines = [f"Junction: {_safe(junction.name)}"]
            if junction.downstream:
                lines.append(f"     Downstream: {_safe(junction.downstream)}")
            blocks.append("\n".join(lines) + "\nEnd:\n")
        blocks.append(f"Sink: {_safe(watershed.outlet.name)}\nEnd:\n")
        return "\n".join(blocks)

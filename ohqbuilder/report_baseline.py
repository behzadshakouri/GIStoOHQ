from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_REPORTS = (
    "pour_points_generation_report.json",
    "subwatershed_partition_report.json",
    "reaches_nhd_comparison.json",
    "watershed_wbd_comparison.json",
    "watershed_documented_comparison.json",
    "watershed_nhdplus_comparison.json",
)

IGNORED_KEYS = {
    "started_at",
    "finished_at",
    "duration_seconds",
    "run_id",
    "source_path",
    "output_path",
    "pour_point_path",
}


class ReportBaselineError(RuntimeError):
    """Raised when a report baseline cannot be created or compared."""


@dataclass(frozen=True)
class BaselineComparison:
    passed: bool
    differences: tuple[dict[str, Any], ...]


def _stable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable_value(item)
            for key, item in sorted(value.items())
            if key not in IGNORED_KEYS and not key.endswith("_path")
        }
    if isinstance(value, list):
        return [_stable_value(item) for item in value]
    return value


def create_report_baseline(
    outputs_dir: str | Path,
    baseline_path: str | Path,
    *,
    report_names: tuple[str, ...] | None = None,
) -> Path:
    """Capture stable fields from available workflow reports as a baseline."""

    outputs = Path(outputs_dir).expanduser().resolve()
    names = report_names or DEFAULT_REPORTS
    reports: dict[str, Any] = {}
    for name in names:
        path = outputs / name
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReportBaselineError(f"Could not read report {path}: {exc}") from exc
        reports[name] = _stable_value(payload)
    if not reports:
        raise ReportBaselineError(f"No supported JSON reports found in {outputs}")
    destination = Path(baseline_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps({"schema_version": 1, "reports": reports}, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def _compare(expected: Any, actual: Any, path: str, differences: list, atol: float, rtol: float):
    if isinstance(expected, bool) or isinstance(actual, bool):
        if expected != actual:
            differences.append({"path": path, "expected": expected, "actual": actual})
        return
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if not math.isclose(float(actual), float(expected), abs_tol=atol, rel_tol=rtol):
            differences.append({
                "path": path, "expected": expected, "actual": actual,
                "absolute_difference": abs(float(actual) - float(expected)),
            })
        return
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}" if path else key
            if key not in actual:
                differences.append({"path": child, "expected": expected[key], "actual": "<missing>"})
            elif key not in expected:
                differences.append({"path": child, "expected": "<missing>", "actual": actual[key]})
            else:
                _compare(expected[key], actual[key], child, differences, atol, rtol)
        return
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            differences.append({"path": path + ".length", "expected": len(expected), "actual": len(actual)})
        for index, (left, right) in enumerate(zip(expected, actual)):
            _compare(left, right, f"{path}[{index}]", differences, atol, rtol)
        return
    if expected != actual:
        differences.append({"path": path, "expected": expected, "actual": actual})


def compare_report_baseline(
    outputs_dir: str | Path,
    baseline_path: str | Path,
    *,
    absolute_tolerance: float = 1e-6,
    relative_tolerance: float = 0.0,
) -> BaselineComparison:
    """Compare current reports with a captured baseline using numeric tolerances."""

    baseline = Path(baseline_path).expanduser().resolve()
    try:
        expected = json.loads(baseline.read_text(encoding="utf-8"))["reports"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ReportBaselineError(f"Could not read baseline {baseline}: {exc}") from exc
    outputs = Path(outputs_dir).expanduser().resolve()
    actual = {}
    for name in expected:
        path = outputs / name
        if not path.is_file():
            actual[name] = "<missing report>"
            continue
        actual[name] = _stable_value(json.loads(path.read_text(encoding="utf-8")))
    differences: list[dict[str, Any]] = []
    _compare(expected, actual, "reports", differences, absolute_tolerance, relative_tolerance)
    return BaselineComparison(not differences, tuple(differences))

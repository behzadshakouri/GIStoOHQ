from __future__ import annotations
from ..model.watershed import Watershed

class ParameterValidator:
    def validate(self, watershed: Watershed) -> None:
        warnings = []
        for s in watershed.subbasins:
            if s.area_km2 is None or s.area_km2 <= 0:
                warnings.append(f"{s.name}: missing/nonpositive area_km2")
            if s.curve_number is None:
                warnings.append(f"{s.name}: missing Curve Number")
        for r in watershed.reaches:
            if r.length_m is None or r.length_m <= 0:
                warnings.append(f"{r.name}: missing/nonpositive length_m")
        if warnings:
            print("Parameter warnings:")
            for w in warnings:
                print("  -", w)

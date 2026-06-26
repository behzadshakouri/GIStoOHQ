from __future__ import annotations
from ..model.watershed import Watershed

def rainfall_lines(watershed: Watershed) -> list[str]:
    return [
        "Meteorology: DesignStorm",
        "    Type: ExternalOrAtlas14",
        "    Note: Replace this block with exact OpenHydroQual precipitation grammar.",
        "End",
    ]

from __future__ import annotations
from dataclasses import dataclass, field
from .junction import Junction
from .outlet import Outlet
from .reach import Reach
from .subbasin import Subbasin
from .topology import TopologyLink

@dataclass
class Watershed:
    name: str
    subbasins: list[Subbasin] = field(default_factory=list)
    reaches: list[Reach] = field(default_factory=list)
    junctions: list[Junction] = field(default_factory=list)
    outlet: Outlet = field(default_factory=Outlet)
    topology: list[TopologyLink] = field(default_factory=list)

    def element_names(self) -> set[str]:
        return {s.name for s in self.subbasins} | {r.name for r in self.reaches} | {j.name for j in self.junctions} | {self.outlet.name}

    def summary(self) -> dict[str, int | str]:
        return {"name": self.name, "subbasins": len(self.subbasins), "reaches": len(self.reaches), "junctions": len(self.junctions), "topology_links": len(self.topology)}

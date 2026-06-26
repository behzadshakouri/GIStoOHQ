from __future__ import annotations
from ..model.watershed import Watershed
from ..readers.junction_reader import JunctionReader
from ..readers.reach_reader import ReachReader
from ..readers.subbasin_reader import SubbasinReader
from ..readers.topology_reader import TopologyReader
from ..settings import BuilderSettings
from .topology_builder import apply_topology

class WatershedBuilder:
    def __init__(self, settings: BuilderSettings):
        self.settings = settings

    def build(self) -> Watershed:
        p = self.settings.paths
        topology = TopologyReader().read(p.output_file(p.topology))
        subbasins = SubbasinReader().read(p.output_file(p.subbasins))
        reaches = ReachReader().read(p.output_file(p.reaches))
        junctions = JunctionReader().read(p.output_file(p.junctions))
        ws = Watershed(name=self.settings.project_name, subbasins=subbasins, reaches=reaches, junctions=junctions, topology=topology)
        apply_topology(ws)
        return ws

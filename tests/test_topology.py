from ohqbuilder.model.watershed import Watershed
from ohqbuilder.model.subbasin import Subbasin
from ohqbuilder.model.topology import TopologyLink
from ohqbuilder.validation.topology_validator import TopologyValidator

def test_valid_topology():
    ws = Watershed(
        name="T",
        subbasins=[Subbasin(id=1, name="Subbasin_1")],
        topology=[TopologyLink(1, "subbasin", "Subbasin_1", "sink", None, "Outlet")],
    )
    TopologyValidator().validate(ws)

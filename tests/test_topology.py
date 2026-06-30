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


def test_topology_rejects_dangling_downstream():
    ws = Watershed(
        name="T",
        subbasins=[Subbasin(id=1, name="Subbasin_1")],
        topology=[TopologyLink(1, "subbasin", "Subbasin_1", "reach", 99, "Reach_99")],
    )
    import pytest

    with pytest.raises(ValueError, match="Dangling downstream target"):
        TopologyValidator().validate(ws)


def test_topology_rejects_cycles():
    ws = Watershed(
        name="T",
        subbasins=[
            Subbasin(id=1, name="Subbasin_1"),
            Subbasin(id=2, name="Subbasin_2"),
        ],
        topology=[
            TopologyLink(1, "subbasin", "Subbasin_1", "subbasin", 2, "Subbasin_2"),
            TopologyLink(2, "subbasin", "Subbasin_2", "subbasin", 1, "Subbasin_1"),
        ],
    )
    import pytest

    with pytest.raises(ValueError, match="Cycle"):
        TopologyValidator().validate(ws)

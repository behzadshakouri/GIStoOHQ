from ohqbuilder.model.watershed import Watershed
from ohqbuilder.model.subbasin import Subbasin
from ohqbuilder.model.topology import TopologyLink
from ohqbuilder.validation.topology_validator import TopologyValidator
from ohqbuilder.builders.topology_builder import retain_topology_elements
from ohqbuilder.model.junction import Junction
from ohqbuilder.model.reach import Reach

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


def test_topology_prunes_extracted_reaches_and_junctions_not_in_final_graph():
    ws = Watershed(
        name="T",
        subbasins=[Subbasin(id=1, name="Subbasin_1")],
        reaches=[Reach(id=1, name="Reach_1"), Reach(id=2, name="Reach_2")],
        junctions=[Junction(id=1, name="Junction_1"), Junction(id=2, name="Junction_2")],
        topology=[
            TopologyLink(1, "subbasin", "Subbasin_1", "junction", 1, "Junction_1"),
            TopologyLink(1, "junction", "Junction_1", "reach", 2, "Reach_2"),
            TopologyLink(2, "reach", "Reach_2", "sink", None, "Outlet"),
            TopologyLink(0, "sink", "Outlet", "", None, None),
        ],
    )

    retain_topology_elements(ws)

    assert [reach.name for reach in ws.reaches] == ["Reach_2"]
    assert [junction.name for junction in ws.junctions] == ["Junction_1"]
    TopologyValidator().validate(ws)


def test_topology_rejects_unlinked_extracted_model_element():
    ws = Watershed(
        name="T",
        subbasins=[Subbasin(id=1, name="Subbasin_1"), Subbasin(id=2, name="Subbasin_2")],
        topology=[TopologyLink(1, "subbasin", "Subbasin_1", "sink", None, "Outlet")],
    )

    import pytest

    with pytest.raises(ValueError, match="Model element has no topology entry: Subbasin_2"):
        TopologyValidator().validate(ws)

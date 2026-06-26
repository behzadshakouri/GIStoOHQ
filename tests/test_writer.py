from ohqbuilder.model.watershed import Watershed
from ohqbuilder.model.subbasin import Subbasin
from ohqbuilder.model.topology import TopologyLink
from ohqbuilder.writers.ohq_writer import OHQWriter

def test_writer_renders_subbasin():
    ws = Watershed(
        name="Test",
        subbasins=[Subbasin(id=1, name="Subbasin_1", area_km2=1.0, curve_number=75, downstream="Outlet")],
        topology=[TopologyLink(1, "subbasin", "Subbasin_1", "sink", None, "Outlet")],
    )
    txt = OHQWriter().render(ws)
    assert "Subbasin: Subbasin_1" in txt
    assert "Connect: Subbasin_1 -> Outlet" in txt

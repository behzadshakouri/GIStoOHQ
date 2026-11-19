from types import SimpleNamespace
from ohqbuilder.hms_pipeline import build_hms_project,validate_hms_project
from ohqbuilder.model.watershed import Watershed
from ohqbuilder.model.subbasin import Subbasin
from ohqbuilder.model.reach import Reach
from ohqbuilder.model.outlet import Outlet
from ohqbuilder.model.topology import TopologyLink


def test_hms_uses_only_final_topology_elements_like_ohq(monkeypatch,tmp_path):
    watershed=Watershed(name='Saved GIS',
        subbasins=[Subbasin(id=1,name='Subbasin_1',area_km2=1.,curve_number=75,lag_min=20,downstream='Reach_1')],
        reaches=[Reach(id=1,name='Reach_1',length_m=100,slope=.01,downstream='Outlet'),
                 Reach(id=2,name='Reach_2',length_m=50,slope=.02)],
        outlet=Outlet(name='Outlet'),
        topology=[TopologyLink(1,'subbasin','Subbasin_1','reach',1,'Reach_1'),
                  TopologyLink(1,'reach','Reach_1','sink',None,'Outlet')])
    monkeypatch.setattr('ohqbuilder.hms_pipeline.WatershedBuilder',lambda _:SimpleNamespace(build=lambda:watershed))
    result=build_hms_project(SimpleNamespace(paths=SimpleNamespace(outputs_path=tmp_path)),tmp_path/'hms')
    assert len(validate_hms_project(result.project_file))==4
    text=result.basin_file.read_text()
    assert 'Reach: Reach_1' in text
    assert 'Reach: Reach_2' not in text
    assert [r.name for r in watershed.reaches]==['Reach_1']

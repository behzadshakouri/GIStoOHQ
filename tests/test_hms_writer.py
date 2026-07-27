from ohqbuilder.hms_pipeline import validate_hms_project
from ohqbuilder.model.junction import Junction
from ohqbuilder.model.outlet import Outlet
from ohqbuilder.model.reach import Reach
from ohqbuilder.model.subbasin import Subbasin
from ohqbuilder.model.watershed import Watershed
from ohqbuilder.writers.hms_writer import HmsWriter


def test_hms_writer_creates_native_project_file_set(tmp_path):
    watershed = Watershed(
        name="Demo HMS",
        subbasins=[
            Subbasin(
                id=1,
                name="Subbasin-1",
                area_km2=2.5,
                curve_number=78,
                lag_min=42,
                downstream="Junction-1",
            )
        ],
        reaches=[
            Reach(
                id=1,
                name="Reach-1",
                length_m=1200,
                slope=0.01,
                downstream="Junction-1",
            )
        ],
        junctions=[Junction(id=1, name="Junction-1", downstream="Outlet")],
        outlet=Outlet(name="Outlet"),
    )

    result = HmsWriter().write(watershed, tmp_path)

    assert result.project_file.suffix == ".hms"
    assert set(validate_hms_project(result.project_file)) == {
        result.basin_file,
        result.meteorology_file,
        result.control_file,
        result.run_file,
    }
    basin = result.basin_file.read_text(encoding="utf-8")
    assert "Subbasin: Subbasin-1" in basin
    assert "LossRate: SCS Curve Number" in basin
    assert "Reach: Reach-1" in basin
    assert "Junction: Junction-1" in basin
    assert "Sink: Outlet" in basin

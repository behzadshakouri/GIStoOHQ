from __future__ import annotations

import csv

import pytest

from ohqbuilder.channel_sections import fit_trapezoid, load_channel_segments, write_fit_report
from ohqbuilder.channel_profile_sampler import sample_channel_profiles
from ohqbuilder.model.outlet import Outlet
from ohqbuilder.model.reach import Reach
from ohqbuilder.model.subbasin import Subbasin
from ohqbuilder.model.topology import TopologyLink
from ohqbuilder.model.watershed import Watershed
from ohqbuilder.writers.ohq_writer import OHQWriter


def _profiles(path):
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("reach_id", "station_m", "offset_m", "elev_m", "bank_left_m", "bank_right_m"))
        for station in (25, 75):
            for offset in range(-3, 4):
                writer.writerow((1, station, offset, 100-station/100 + max(0, abs(offset)-1), -3, 3))


def _watershed():
    return Watershed(
        name="Channels",
        subbasins=[Subbasin(1, "Subbasin_1", area_km2=1, centroid_x=10, centroid_y=5)],
        reaches=[Reach(1, "Reach_1", length_m=100, z_up_m=100, z_dn_m=99,
                       x_up_act=0, y_up_act=0, x_dn_act=100, y_dn_act=0,
                       centerline_xy=((0, 0), (100, 0)))],
        outlet=Outlet(x_act=110, y_act=0),
        topology=[TopologyLink(1, "subbasin", "Subbasin_1", "reach", 1, "Reach_1",
                               x_dn_act=20, y_dn_act=0),
                  TopologyLink(1, "reach", "Reach_1", "sink", None, "Outlet")],
    )


def test_trapezoid_fit_recovers_known_area_depth_curve():
    samples = [(i/2, max(0, abs(i/2)-1)) for i in range(-6, 7)]
    width, side, bed, rmse = fit_trapezoid(samples, -3, 3)
    assert width == pytest.approx(2)
    assert side == pytest.approx(1)
    assert bed == 0
    assert rmse < 1e-10


def test_flat_water_surface_is_rejected():
    with pytest.raises(ValueError, match="hydroflattening"):
        fit_trapezoid([(i, 100) for i in range(-3, 4)], -3, 3)


def test_segmented_ohq_preserves_runoff_and_groundwater_paths(tmp_path):
    path = tmp_path / "profiles.csv"
    _profiles(path)
    watershed = _watershed()
    sections = load_channel_segments(path, {"Reach_1": watershed.reaches[0]})
    assert [s.name for s in sections["Reach_1"]] == ["Reach_1__S001", "Reach_1__S002"]
    assert [s.end_m-s.start_m for s in sections["Reach_1"]] == [50, 50]

    text = OHQWriter(formulation="mixed_hru", channel_profiles_path=path).render(watershed)
    assert text.count("create block;type=Trapezoidal Channel Segment") == 2
    assert "name=Reach_1__S001,base_width=2[m],side_slope=1" in text
    assert "from=Subbasin_1,to=Reach_1__S001,type=Reach_link" in text
    assert "from=Subbasin_1,to=Reach_1__S001,type=Impervious_Reach_link" in text
    assert "from=Subbasin_1,to=Reach_1__S001,type=groundwater_to_stream" in text
    assert "from=Reach_1__S001,to=Reach_1__S002,type=Trapezoidal_Channel_link" in text
    assert "from=Reach_1__S002,to=Outlet,type=channel2fixed" in text
    # The existing model stays one reach when no detailed input is given.
    original = OHQWriter(formulation="mixed_hru").render(watershed)
    assert "name=Reach_1,base_width=" in original
    assert "Reach_1__S001" not in original

    report = tmp_path / "fit.csv"
    write_fit_report(report, sections, {"Reach_1": watershed.reaches[0]})
    rows = list(csv.DictReader(report.open()))
    assert len(rows) == 2
    assert rows[0]["bathymetry_status"] == "DEM_ONLY_UNVERIFIED"
    assert float(rows[-1]["ohq_invert_m"]) == 99


def test_profile_requires_reviewed_bank_offsets(tmp_path):
    path = tmp_path / "unreviewed.csv"
    path.write_text("reach_id,station_m,offset_m,elev_m\n1,25,0,100\n")
    with pytest.raises(ValueError, match="bank_left_m"):
        load_channel_segments(path, {"Reach_1": _watershed().reaches[0]})


def test_sampler_rejects_nonpositive_spacing_before_loading_gis(tmp_path):
    with pytest.raises(ValueError, match="positive metres"):
        sample_channel_profiles("reaches.gpkg", "dem.tif", tmp_path / "profiles.csv", spacing_m=0)


def test_geopackage_dem_sampling_and_reviewed_fit(tmp_path):
    gpd = pytest.importorskip("geopandas")
    rasterio = pytest.importorskip("rasterio")
    np = pytest.importorskip("numpy")
    from rasterio.transform import from_origin
    from shapely.geometry import LineString

    reaches = tmp_path / "reaches.gpkg"
    dem = tmp_path / "dem.tif"
    profiles = tmp_path / "profiles.csv"
    gpd.GeoDataFrame([{"reach_id": 1, "length_m": 100.0, "z_up_m": 100.0,
                       "z_dn_m": 99.0, "geometry": LineString([(0, 0), (100, 0)])}],
                     crs="EPSG:32618").to_file(reaches)
    yy, xx = np.mgrid[0:80, 0:180]
    x, y = -40 + xx + 0.5, 40 - yy - 0.5
    elevations = (100 - x/100 + np.maximum(0, np.abs(y)-1)).astype("float32")
    with rasterio.open(dem, "w", driver="GTiff", height=80, width=180, count=1,
                       dtype="float32", crs="EPSG:32618", transform=from_origin(-40, 40, 1, 1),
                       nodata=-9999) as raster:
        raster.write(elevations, 1)
    count = sample_channel_profiles(reaches, dem, profiles, spacing_m=50,
                                    half_width_m=3, sample_step_m=0.5)
    with profiles.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert count == len(rows) > 20
    assert {row["station_m"] for row in rows} == {"25.0", "75.0"}
    assert all(row["bank_left_m"] == row["bank_right_m"] == "" for row in rows)
    for row in rows:
        row.update(bank_left_m="-3", bank_right_m="3")
    with profiles.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    reach = Reach(1, "Reach_1", length_m=100, z_up_m=100, z_dn_m=99)
    segments = load_channel_segments(profiles, {"Reach_1": reach})["Reach_1"]
    assert len(segments) == 2
    for segment in segments:
        # Raster cell centers discretize an ideal 2 m/1H:1V trapezoid.
        assert 1.5 < segment.base_width_m < 2.5
        assert 0.7 < segment.side_slope_z < 1.3

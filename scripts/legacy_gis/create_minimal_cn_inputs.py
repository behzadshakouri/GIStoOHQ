#!/usr/bin/env python3
from pathlib import Path
import sys
import numpy as np

from osgeo import gdal, ogr, osr


def make_raster_like(ref_tif, out_tif, value, dtype=gdal.GDT_Byte):
    ref = gdal.Open(str(ref_tif))
    if ref is None:
        raise RuntimeError(f"Cannot open reference raster: {ref_tif}")

    xsize = ref.RasterXSize
    ysize = ref.RasterYSize
    gt = ref.GetGeoTransform()
    proj = ref.GetProjection()

    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(str(out_tif), xsize, ysize, 1, dtype, ["COMPRESS=LZW"])
    ds.SetGeoTransform(gt)
    ds.SetProjection(proj)

    arr = np.full((ysize, xsize), value, dtype=np.uint8)
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    band.SetNoDataValue(255)
    band.FlushCache()
    ds = None
    ref = None


def make_hsg_gpkg(ref_tif, out_gpkg):
    ref = gdal.Open(str(ref_tif))
    if ref is None:
        raise RuntimeError(f"Cannot open reference raster: {ref_tif}")

    gt = ref.GetGeoTransform()
    proj = ref.GetProjection()
    xsize = ref.RasterXSize
    ysize = ref.RasterYSize

    xmin = gt[0]
    ymax = gt[3]
    xmax = xmin + gt[1] * xsize
    ymin = ymax + gt[5] * ysize

    if out_gpkg.exists():
        out_gpkg.unlink()

    drv = ogr.GetDriverByName("GPKG")
    ds = drv.CreateDataSource(str(out_gpkg))

    srs = osr.SpatialReference()
    srs.ImportFromWkt(proj)

    lyr = ds.CreateLayer("hydrologic_soil_groups", srs, ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn("hsg", ogr.OFTString))
    lyr.CreateField(ogr.FieldDefn("HSG", ogr.OFTString))

    ring = ogr.Geometry(ogr.wkbLinearRing)
    ring.AddPoint(xmin, ymin)
    ring.AddPoint(xmax, ymin)
    ring.AddPoint(xmax, ymax)
    ring.AddPoint(xmin, ymax)
    ring.AddPoint(xmin, ymin)

    poly = ogr.Geometry(ogr.wkbPolygon)
    poly.AddGeometry(ring)

    feat = ogr.Feature(lyr.GetLayerDefn())
    feat.SetGeometry(poly)
    feat.SetField("hsg", "B")
    feat.SetField("HSG", "B")
    lyr.CreateFeature(feat)

    feat = None
    ds = None
    ref = None


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 create_minimal_cn_inputs.py <SITE_DIR>")
        sys.exit(1)

    site = Path(sys.argv[1]).resolve()
    if not site.exists():
        raise RuntimeError(f"Site not found: {site}")

    ref_tif = site / "demlr" / "cliped_utm.tif"
    if not ref_tif.exists():
        ref_tif = site / "outputs" / "dem_carved.tif"

    if not ref_tif.exists():
        raise RuntimeError("No reference DEM found.")

    landcover = site / "landcover"
    soils = site / "soils"
    landcover.mkdir(parents=True, exist_ok=True)
    soils.mkdir(parents=True, exist_ok=True)

    nlcd = landcover / f"nlcd_2023_{site.name}.tif"
    hsg_tif = soils / "hsg.tif"
    hsg_gpkg = soils / "hydrologic_soil_groups.gpkg"

    print(f"Reference raster: {ref_tif}")
    print(f"Creating NLCD dummy raster: {nlcd}")
    make_raster_like(ref_tif, nlcd, value=21)

    print(f"Creating HSG dummy raster: {hsg_tif}")
    make_raster_like(ref_tif, hsg_tif, value=2)

    print(f"Creating HSG dummy GeoPackage: {hsg_gpkg}")
    make_hsg_gpkg(ref_tif, hsg_gpkg)

    print()
    print("Done. Minimal CN inputs created.")
    print("NOTE: These are dummy testing inputs, not real NLCD/SSURGO data.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run the production boundary script on preserved routing in a new directory.

This verifies delineation from conditioned routing, not DEM acquisition,
conditioning, independent drainage truth or solver execution.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    names = ['fdir.tif', 'facc.tif', 'basin.tif', 'watershed_boundary.gpkg', 'mouth_snapped.txt']
    before = {name: sha(source / name) for name in names}
    output.mkdir(parents=True)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ohqbuilder.qgis_env import ensure_qgis_application, initialize_processing
    assert ensure_qgis_application() and initialize_processing()
    from osgeo import gdal, ogr, osr
    import numpy as np
    x, y = map(float, (source / 'mouth_snapped.txt').read_text().strip().split(','))
    flow = gdal.Open(str(source / 'fdir.tif'))
    srs = osr.SpatialReference()
    srs.ImportFromWkt(flow.GetProjection())
    flow = None
    point = ogr.Geometry(ogr.wkbPoint)
    point.AddPoint(x, y)
    dataset = ogr.GetDriverByName('GPKG').CreateDataSource(str(output / 'outlet.gpkg'))
    layer = dataset.CreateLayer('outlet', srs, ogr.wkbPoint)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(point)
    layer.CreateFeature(feature)
    feature = layer = dataset = None
    script = Path(__file__).resolve().parent / 'legacy_gis/delineate_whole_watershed.py'
    runpy.run_path(str(script), init_globals={
        'ROOT': str(output), 'OUT_DIR': str(output),
        'OUTLET_PATH': str(output / 'outlet.gpkg'),
        'FLOWDIR_PATH': str(source / 'fdir.tif'),
        'FLOWACC_PATH': str(source / 'facc.tif'),
        'ADD_TO_PROJECT': False, 'MAX_OUTLET_SNAP_M': 50.0,
    })
    reference = gdal.Open(str(source / 'basin.tif'))
    generated = gdal.Open(str(output / 'temp/whole_wshed.tif'))
    if (reference.RasterXSize, reference.RasterYSize, reference.GetGeoTransform(), reference.GetProjection()) != (generated.RasterXSize, generated.RasterYSize, generated.GetGeoTransform(), generated.GetProjection()):
        raise ValueError('Generated grid differs from reference')
    expected, actual = reference.ReadAsArray() == 1, generated.ReadAsArray() == 1
    differing = int(np.count_nonzero(expected != actual))
    cells = int(actual.sum())
    affine = generated.GetGeoTransform()
    area = cells * abs(affine[1]*affine[5]-affine[2]*affine[4]) / 1e6
    reference = generated = None
    def polygon(path):
        dataset = ogr.Open(str(path))
        geometry = None
        for feature in dataset.GetLayer():
            part = feature.GetGeometryRef().Clone()
            geometry = part if geometry is None else geometry.Union(part)
        return geometry
    a = polygon(source / 'watershed_boundary.gpkg')
    b = polygon(output / 'watershed_boundary.gpkg')
    iou = a.Intersection(b).GetArea() / a.Union(b).GetArea()
    if before != {name: sha(source / name) for name in names}:
        raise RuntimeError('Preserved source changed')
    receipt = {'source_sha256': before, 'script_sha256': sha(script),
               'cells': cells, 'area_km2': area, 'different_reference_cells': differing,
               'reference_boundary_iou': iou, 'source_unchanged': True,
               'limitations': 'Production boundary generation on preserved conditioned routing. Not fresh DEM conditioning, independent basin truth or a full-run/solver acceptance.'}
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    if differing or iou < .999999:
        raise RuntimeError('Production delineation differs from preserved reference; inspect receipt')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('preserved_reference', type=Path)
    parser.add_argument('new_output', type=Path)
    args = parser.parse_args()
    run(args.preserved_reference, args.new_output)

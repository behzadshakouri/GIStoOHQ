#!/usr/bin/env python3
"""Reproduce the preserved Sligo reference basin without reading observations.

Inputs must be a local preserved GIS copy; outputs go to a new directory.
This verifies the reference domain, not a newly regenerated GIS/model pipeline.
"""
import argparse
import hashlib
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
from pyproj import Transformer
import rasterio

from audit_sligo_gauge_catchment import trace


def verify(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    protected = Path('/home/arash/Dropbox/OHQ Watershed Models').resolve()
    if any(path == protected or protected in path.parents for path in (source, output)):
        raise ValueError('Use the preserved HydroPINN GIS copy, not the OHQ folder')
    if output.exists():
        raise FileExistsError('Refusing to overwrite verification output')
    files = ['mouth_snapped.txt', 'fdir.tif', 'facc.tif', 'basin.tif',
             'watershed_boundary.gpkg', 'outlets.gpkg']
    for name in files:
        resolved = (source / name).resolve()
        if resolved == protected or protected in resolved.parents:
            raise ValueError('Reference file points into the protected OHQ folder')
    x, y = map(float, (source / files[0]).read_text().strip().split(','))
    if not np.isfinite([x, y]).all():
        raise ValueError('Finite outlet coordinates required')
    with rasterio.open(source / 'fdir.tif') as ds:
        flow, affine, crs = ds.read(1), ds.transform, ds.crs
        row, col = ds.index(x, y)
    if not crs.is_projected or crs.linear_units != 'metre':
        raise ValueError('Projected metre reference required')
    if not (0 <= row < flow.shape[0] and 0 <= col < flow.shape[1]):
        raise ValueError('Reference outlet outside flow grid')
    arrays = {}
    for name in ['facc.tif', 'basin.tif']:
        with rasterio.open(source / name) as ds:
            if (ds.shape, ds.transform, ds.crs) != (flow.shape, affine, crs):
                raise ValueError('Reference grids do not align')
            arrays[name] = ds.read(1)
    mask = trace(flow, (row, col), {
        1: (-1, 1), 2: (-1, 0), 3: (-1, -1), 4: (0, -1),
        5: (1, -1), 6: (1, 0), 7: (1, 1), 8: (0, 1)})
    if not np.array_equal(mask, arrays['basin.tif'] == 1):
        raise ValueError('Traced basin differs from preserved reference mask')
    cell_area = abs(affine.a * affine.e - affine.b * affine.d)
    area = int(mask.sum()) * cell_area / 1e6
    boundary = gpd.read_file(source / 'watershed_boundary.gpkg').to_crs(crs)
    if abs(boundary.geometry.area.sum() / 1e6 - area) > cell_area / 1e6:
        raise ValueError('Reference vector/raster areas disagree')
    outlets = gpd.read_file(source / 'outlets.gpkg').to_crs(crs)
    terminal = outlets[outlets['ds_id'] == 0]
    gauge = outlets[outlets['control'].astype(str) == '01650800']
    if len(terminal) != 1 or len(gauge) != 1:
        raise ValueError('Expected one full-domain outlet and one internal gauge')
    if abs(float(terminal.iloc[0]['da_km2']) - area) > 1e-4:
        raise ValueError('Terminal topology area disagrees with traced basin')
    longitude, latitude = Transformer.from_crs(crs, 4326, always_xy=True).transform(x, y)
    report = {
        'verified_reference_only': True, 'new_pipeline_verified': False,
        'area_km2': area, 'display_area_km2': round(area, 1),
        'cells': int(mask.sum()), 'reference_mask_exact': True,
        'outlet_longitude': longitude, 'outlet_latitude': latitude,
        'internal_gauge_area_km2': float(gauge.iloc[0]['da_km2']),
        'subcatchments': len(outlets),
        'source_sha256': {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                          for name in files},
        'limitations': 'No observation or forcing series read; no new model run or research fit.'}
    output.mkdir(parents=True)
    (output / 'reference_verification.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('preserved_gis', type=Path)
    parser.add_argument('new_output_directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.preserved_gis, args.new_output_directory), indent=2))

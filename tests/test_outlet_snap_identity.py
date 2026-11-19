"""Exercise production snapping without a QGIS process or GDAL ABI dependency."""
import ast
from pathlib import Path
from types import SimpleNamespace
import numpy as np


def snapper(values):
    source = Path('scripts/legacy_gis/delineate_whole_watershed.py')
    function = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'snap_to_flow_accumulation')
    class Band:
        def GetNoDataValue(self): return -9999
        def ReadAsArray(self, x, y, width, height): return values[y:y+height, x:x+width]
    dataset = SimpleNamespace(GetRasterBand=lambda _: Band(),
                              GetGeoTransform=lambda: (0, 10, 0, 50, 0, -10),
                              RasterXSize=5, RasterYSize=5)
    scope = dict(np=np, gdal=SimpleNamespace(Open=lambda _: dataset),
                 MAX_OUTLET_SNAP_M=25., MIN_SNAP_ACC_CELLS=50., SNAP_DISTANCE_WEIGHT=0.)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), scope)
    return scope['snap_to_flow_accumulation']


def test_repeat_snap_keeps_routed_cell_identity_despite_larger_downstream_accumulation():
    values = np.ones((5, 5))
    values[2, 2] = 500
    values[3, 2] = 2000
    snap = snapper(values)
    first = snap(25., 25., 'fixture', 40.)
    second = snap(first[0], first[1], 'fixture', 40.)
    assert first == second == (25., 25., 500., 0., 2, 2)


def test_centred_nonchannel_cell_still_snaps_to_routing():
    values = np.ones((5, 5))
    values[3, 2] = 2000
    result = snapper(values)(25., 25., 'fixture', 40.)
    assert result == (25., 15., 2000., 10., 2, 3)

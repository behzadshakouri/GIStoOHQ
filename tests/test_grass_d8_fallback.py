"""Exercise the actual QGIS fallback function without running a QGIS workflow."""
import ast
from collections import deque
from pathlib import Path

import numpy as np
import pytest

SOURCE = Path('scripts/legacy_gis/delineate_whole_watershed.py').read_text()
FUNCTION = next(
    node for node in ast.parse(SOURCE).body
    if isinstance(node, ast.FunctionDef) and node.name == 'delineate_watershed_with_flowdir'
)


class Raster:
    def __init__(self, array):
        self.array = array
        self.RasterYSize, self.RasterXSize = array.shape

    def GetRasterBand(self, _):
        return self

    def ReadAsArray(self):
        return self.array

    def GetGeoTransform(self):
        return (0, 1, 0, 3, 0, -1)

    def GetProjection(self):
        return 'test'

    def SetGeoTransform(self, _):
        pass

    def SetProjection(self, _):
        pass

    def SetNoDataValue(self, _):
        pass

    def WriteArray(self, array):
        self.array = array.copy()

    def FlushCache(self):
        pass


class Gdal:
    GDT_Byte = 1

    def __init__(self, flow):
        self.source = Raster(flow)

    def Open(self, _):
        return self.source

    def GetDriverByName(self, _):
        return self

    def Create(self, _, cols, rows, *args):
        self.result = Raster(np.zeros((rows, cols)))
        return self.result


def call_fallback(flow, x=1.5, y=1.5):
    gdal = Gdal(flow)
    namespace = dict(np=np, deque=deque, gdal=gdal, release_and_delete=lambda _: None)
    module = ast.Module(body=[FUNCTION], type_ignores=[])
    exec(compile(module, 'actual_fallback', 'exec'), namespace)
    namespace[FUNCTION.name]('unused', x, y, 'unused')
    return gdal.result.array


def test_all_eight_directions_point_to_center():
    # Hand-built GRASS directions: all eight surrounding cells drain inward.
    flow = np.array([[7, 6, 5], [8, 0, 4], [1, 2, 3]])
    assert call_fallback(flow).sum() == 9


@pytest.mark.parametrize('row,col,code', [
    (0, 0, 7), (0, 1, 6), (0, 2, 5), (1, 0, 8),
    (1, 2, 4), (2, 0, 1), (2, 1, 2), (2, 2, 3),
])
def test_each_grass_direction(row, col, code):
    flow = np.zeros((3, 3), dtype=int)
    flow[row, col] = code
    mask = call_fallback(flow)
    assert mask.sum() == 2 and mask[row, col] == 1


def test_chain_and_no_input_mutation():
    flow = np.zeros((3, 3), dtype=int)
    flow[1, 0] = 8
    flow[1, 1] = 8
    original = flow.copy()
    assert call_fallback(flow, x=2.5).sum() == 3
    assert np.array_equal(flow, original)


def test_invalid_outlet():
    with pytest.raises(Exception, match='outside'):
        call_fallback(np.zeros((3, 3)), x=-2)

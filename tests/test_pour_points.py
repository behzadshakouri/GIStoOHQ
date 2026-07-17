import builtins

import pytest

from ohqbuilder.pour_points import PourPointGenerationError, generate_pour_points


def test_generate_pour_points_explains_missing_gis_dependency(monkeypatch, tmp_path):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "geopandas":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(PourPointGenerationError, match=r"pip install -e .\[gis\]"):
        generate_pour_points(tmp_path / "junctions.gpkg", tmp_path / "pour_points.shp")

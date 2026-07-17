import builtins

import pytest

from ohqbuilder.outlet_creator import OutletCreationError, create_outlet_from_flow_accumulation


def test_create_outlet_explains_missing_gis_dependency(monkeypatch, tmp_path):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "geopandas":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(OutletCreationError, match=r"pip install -e .\[gis\]"):
        create_outlet_from_flow_accumulation(
            tmp_path / "flow_acc.tif", tmp_path / "outlet.shp"
        )

import io
import json
from pathlib import Path

from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.hydropinn import export_hydropinn
from ohqbuilder.watershed_data.package import freeze_package
from ohqbuilder.watershed_data.temporal import harmonize_asset
from ohqbuilder.watershed_data.workflow import write_site_spec


def test_hydropinn_export_is_thin_deterministic_and_named(tmp_path):
    site = write_site_spec(
        tmp_path / "site.yaml", site_id="test", name="Test", longitude=-77, latitude=39,
        start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z",
    )
    store = ObjectStore(tmp_path / "store")
    raw = Path("tests/fixtures/nasa_power_hourly.json").read_bytes()
    stored = store.put(io.BytesIO(raw))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    native = catalog.register({
        "provider": "nasa-power", "product": "historical-meteorology",
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json",
    })
    harmonize_asset(
        asset_id=native["asset_id"], catalog=catalog.path, object_store=store.root,
        qc_output=tmp_path / "qc.json", provenance_output=tmp_path / "provenance.json",
    )
    freeze_package(site_spec=site, catalog=catalog.path, output=tmp_path / "package")
    manifest_path = export_hydropinn(
        package=tmp_path / "package", object_store=store.root, output=tmp_path / "hydropinn"
    )
    manifest = json.loads(manifest_path.read_text())
    variables = json.loads((tmp_path / "hydropinn" / "variables.json").read_text())
    assert manifest["profile"] == "water-balance-v1"
    assert "normalization" in manifest["transformations_not_performed"]
    assert {item["name"] for item in variables["variables"]} == {"PRECTOTCORR", "T2M"}
    assert variables["variables"][0]["normalization"] is None
    assert (tmp_path / "hydropinn" / "observations" / "temporal_1.csv").is_file()

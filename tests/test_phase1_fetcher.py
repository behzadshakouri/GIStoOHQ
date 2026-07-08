from pathlib import Path

from ohqbuilder.cli import main
from ohqbuilder.phase1_fetcher import fetch_phase1_inputs


def test_fetch_phase1_inputs_creates_layout_and_manifest(monkeypatch, tmp_path):
    calls = {}

    def fake_outlet(path, lon, lat):
        outlet = Path(path)
        outlet.parent.mkdir(parents=True, exist_ok=True)
        outlet.write_text(f"{lon},{lat}", encoding="utf-8")
        return outlet

    def fake_process_csv(input_csv, output_csv, **kwargs):
        calls["kwargs"] = kwargs
        Path(output_csv).write_text("site_id,product,status\nSITE_A,dem,ok\n", encoding="utf-8")
        return []

    monkeypatch.setattr("ohqbuilder.phase1_fetcher.write_outlet_shapefile", fake_outlet)
    monkeypatch.setattr("ohqbuilder.phase1_fetcher.process_csv", fake_process_csv)

    result = fetch_phase1_inputs(
        tmp_path,
        "SITE_A",
        lon=-111.2,
        lat=35.1,
        site_id="AZ12-100",
        products="dem",
        buffer_m=750,
    )

    assert result.outlet_path == tmp_path / "SITE_A" / "outputs" / "outlet.shp"
    assert (tmp_path / "SITE_A" / "demlr").is_dir()
    assert calls["kwargs"]["id_col"] == "site_id"
    assert calls["kwargs"]["buffer_m"] == 750
    manifest = result.manifest_path.read_text(encoding="utf-8")
    assert "demlr/cliped_utm.tif" in manifest
    assert "outputs/NHDFlowline_clip.gpkg" in manifest


def test_cli_fetch_phase1_inputs(monkeypatch, tmp_path, capsys):
    def fake_fetch(root, site, **kwargs):
        site_path = Path(root) / site
        return type(
            "Result",
            (),
            {
                "outlet_path": site_path / "outputs" / "outlet.shp",
                "download_dir": site_path / "source_downloads",
                "summary_csv": site_path / "source_downloads_summary.csv",
                "manifest_path": site_path / "PHASE1_INPUTS.md",
            },
        )()

    monkeypatch.setattr("ohqbuilder.cli.fetch_phase1_inputs", fake_fetch)

    assert (
        main(
            [
                "fetch-phase1-inputs",
                "--root",
                str(tmp_path),
                "--site",
                "SITE_A",
                "--lat",
                "35.1",
                "--lon",
                "-111.2",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Created outlet:" in out
    assert "Wrote manifest:" in out

import csv
from types import SimpleNamespace

import pytest

from ohqbuilder.demcheck_adapter import DemcheckError, download_with_demcheck, find_demcheck


def test_find_demcheck_rejects_missing_explicit_executable(tmp_path):
    with pytest.raises(DemcheckError, match="not found"):
        find_demcheck(tmp_path / "demcheck")


def test_download_with_demcheck_uses_documented_csv_cli(monkeypatch, tmp_path):
    executable = tmp_path / "demcheck"
    executable.write_text("", encoding="utf-8")
    calls = []

    def fake_outlet(path, lon, lat):
        calls.append(("outlet", path, lon, lat))
        return path

    def fake_run(command, text, capture_output):
        with open(command[1], newline="", encoding="utf-8") as stream:
            row = next(csv.DictReader(stream))
        calls.append(("command", command[2:], row))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("ohqbuilder.demcheck_adapter.write_outlet_shapefile", fake_outlet)
    monkeypatch.setattr("ohqbuilder.demcheck_adapter.subprocess.run", fake_run)
    result = download_with_demcheck(
        executable, tmp_path, "SITE_A", lon=-111.2, lat=34.1, buffer_m=750
    )

    assert result.download_dir == tmp_path / "SITE_A" / "source_downloads"
    arguments = calls[1][1]
    assert arguments == [
        "--id-col", "site_id", "--products", "all", "--download",
        str(result.download_dir), "--buffer", "750",
    ]
    assert calls[1][2] == {"site_id": "SITE_A", "lat": "34.1", "lon": "-111.2"}

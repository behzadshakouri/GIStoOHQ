from pathlib import Path

import ohqbuilder.dem_downloader as dd
from ohqbuilder.cli import main


def test_parse_products_all_and_subset():
    assert dd.parse_products("all") == ["dem", "demlr", "hydro"]
    assert dd.parse_products("dem,hydro") == ["dem", "hydro"]
    assert dd.parse_products("demhr,demlr") == ["dem", "demlr"]


def test_query_tnm_reads_current_nested_download_url(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return (
                b'{"items":[{"title":"USGS tile","urls":{"TIFF":"https://example.test/a.tif"},'
                b'"publicationDate":"2025-01-02","sizeInBytes":"42"}]}'
            )

    monkeypatch.setattr(dd.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    item = dd.query_tnm(-111.2, 35.1, dd.ELEVATION_TIERS[0], 30)[0]

    assert item.url == "https://example.test/a.tif"
    assert item.publication_date == "2025-01-02"
    assert item.size_bytes == 42


def test_process_csv_writes_summary_and_downloads(monkeypatch, tmp_path):
    source = tmp_path / "sites.csv"
    source.write_text("Project No.,Latitude,Longitude\nAZ12-100,35.1,-111.2\n", encoding="utf-8")
    summary = tmp_path / "summary.csv"
    downloads = tmp_path / "GIS"

    def fake_query(lon, lat, tier, buffer_m, timeout=60.0):
        assert lon == -111.2
        assert lat == 35.1
        assert buffer_m == 500
        return [
            dd.DownloadItem(
                "tile", "https://example.test/tile.tif", tier.dataset, tier.resolution_label
            )
        ]

    def fake_download(url, destination, timeout=120.0, expected_size=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("data", encoding="utf-8")
        return True

    monkeypatch.setattr(dd, "query_tnm", fake_query)
    monkeypatch.setattr(dd, "download_file", fake_download)

    results = dd.process_csv(
        source,
        summary,
        products=["dem"],
        download_dir=downloads,
        id_col="Project No.",
        buffer_m=500,
    )

    assert results[0].site_id == "AZ12-100"
    assert results[0].downloaded == 1
    assert (downloads / "AZ12-100" / "dem" / "tile.tif").read_text(encoding="utf-8") == "data"
    assert "AZ12-100,dem,ok,1,1" in summary.read_text(encoding="utf-8")


def test_process_csv_limits_hydro_to_smallest_archive(monkeypatch, tmp_path):
    source = tmp_path / "sites.csv"
    source.write_text("site_id,lat,lon\nAZ12-100,35.1,-111.2\n", encoding="utf-8")
    summary = tmp_path / "summary.csv"
    downloads = tmp_path / "downloads"
    progress_messages = []

    def fake_query(lon, lat, tier, buffer_m, timeout=60.0):
        return [
            dd.DownloadItem(
                "large",
                "https://example.test/large.zip",
                tier.dataset,
                tier.resolution_label,
                size_bytes=5000,
            ),
            dd.DownloadItem(
                "small",
                "https://example.test/small.zip",
                tier.dataset,
                tier.resolution_label,
                size_bytes=50,
            ),
        ]

    def fake_download(url, destination, timeout=120.0, expected_size=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(url, encoding="utf-8")
        return True

    monkeypatch.setattr(dd, "query_tnm", fake_query)
    monkeypatch.setattr(dd, "download_file", fake_download)

    results = dd.process_csv(
        source,
        summary,
        products=["hydro"],
        download_dir=downloads,
        id_col="site_id",
        buffer_m=5000,
        progress=progress_messages.append,
    )

    assert results[0].count == 2
    assert results[0].downloaded == 1
    assert results[0].url == "https://example.test/small.zip"
    assert (downloads / "AZ12-100" / "hydro" / "small.zip").is_file()
    assert any("downloading 1" in message for message in progress_messages)


def test_download_file_skips_valid_existing_file(monkeypatch, tmp_path):
    destination = tmp_path / "tile.tif"
    destination.write_bytes(b"12345")

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("valid cached file should not be downloaded")

    monkeypatch.setattr(dd.urllib.request, "urlopen", fail_urlopen)

    assert not dd.download_file("https://example.test/tile.tif", destination, expected_size=5)
    assert destination.read_bytes() == b"12345"


def test_download_file_redownloads_corrupt_existing_file(monkeypatch, tmp_path):
    destination = tmp_path / "tile.tif"
    destination.write_bytes(b"bad")

    class Response:
        def __init__(self):
            self.remaining = b"correct"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size=-1):
            data = self.remaining
            self.remaining = b""
            return data

    monkeypatch.setattr(dd.urllib.request, "urlopen", lambda *args, **kwargs: Response())

    assert dd.download_file("https://example.test/tile.tif", destination, expected_size=7)
    assert destination.read_bytes() == b"correct"


def test_process_csv_marks_all_oversized_candidates_too_large(monkeypatch, tmp_path):
    source = tmp_path / "sites.csv"
    source.write_text("site_id,lat,lon\nAZ12-100,35.1,-111.2\n", encoding="utf-8")

    def fake_query(lon, lat, tier, buffer_m, timeout=60.0):
        return [
            dd.DownloadItem(
                "huge",
                "https://example.test/huge.zip",
                tier.dataset,
                tier.resolution_label,
                size_bytes=10 * 1024 * 1024,
            )
        ]

    monkeypatch.setattr(dd, "query_tnm", fake_query)

    results = dd.process_csv(
        source,
        None,
        products=["hydro"],
        download_dir=tmp_path / "downloads",
        id_col="site_id",
        max_file_size_mb=1,
    )

    assert results[0].status == "too large"
    assert results[0].downloaded == 0


def test_cli_download_data(monkeypatch, tmp_path, capsys):
    source = tmp_path / "sites.csv"
    source.write_text("lat,lon\n35,-111\n", encoding="utf-8")

    monkeypatch.setattr(
        "ohqbuilder.cli.process_csv",
        lambda *args, **kwargs: [
            dd.SiteDownloadResult("site_1", "dem", "no coverage", 0, 0, Path("out"))
        ],
    )

    assert main(["download-data", str(source), "--products", "dem"]) == 0
    assert "site_1 dem: no coverage" in capsys.readouterr().out

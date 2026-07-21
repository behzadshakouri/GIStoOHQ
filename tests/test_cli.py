from pathlib import Path

from ohqbuilder.cli import build_parser, main
from ohqbuilder.legacy_inputs import LegacyInputWorkflowError
from ohqbuilder.pour_points import PourPointGenerationError, PourPointResult
from ohqbuilder.outlet_creator import OutletCreationError, OutletCreationResult
from ohqbuilder.full_runner import FullRunError, FullRunResult


def test_prepare_inputs_parser_defaults_to_all_phases():
    args = build_parser().parse_args([
        "prepare-inputs",
        "--root",
        "/tmp/root",
        "--site",
        "SITE_A",
    ])

    assert args.command == "prepare-inputs"
    assert args.phase == "all"
    assert args.script_dir is None
    assert args.out_dir is None
    assert args.dem_path is None
    assert args.no_force is False
    assert args.no_auto_pour_points is False
    assert args.no_auto_outlet is False


def test_create_outlet_cli_uses_site_defaults(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_create(flow_acc, output, overwrite=False):
        calls.append((flow_acc, output, overwrite))
        return OutletCreationResult(Path(output).resolve(), 10.0, 20.0, 99.0)

    monkeypatch.setattr("ohqbuilder.cli.create_outlet_from_flow_accumulation", fake_create)
    status = main([
        "create-outlet", "--root", str(tmp_path), "--site", "SITE_A", "--overwrite"
    ])

    outputs = tmp_path / "SITE_A" / "outputs"
    assert status == 0
    assert calls == [(outputs / "flow_acc.tif", outputs / "outlet.shp", True)]
    assert "Created outlet at (10.000, 20.000)" in capsys.readouterr().out


def test_create_outlet_cli_reports_error(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise OutletCreationError("no valid cells")

    monkeypatch.setattr("ohqbuilder.cli.create_outlet_from_flow_accumulation", fail)
    assert main([
        "create-outlet", "--root", str(tmp_path), "--site", "SITE_A"
    ]) == 2


def test_full_run_cli_forwards_one_command_options(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_full(root, site, **kwargs):
        calls.append((root, site, kwargs))
        return FullRunResult(tmp_path / "final.ohq")

    monkeypatch.setattr("ohqbuilder.cli.run_full_pipeline", fake_full)
    status = main([
        "full-run", "--root", str(tmp_path), "--site", "SITE_A",
        "--lat", "34.1", "--lon", "-111.2",
        "--site-id", "source-id", "--download-dir", str(tmp_path / "raw"),
        "--max-tiles", "6", "--soil-pixel-size", "0.0002",
        "--soil-top-depth", "20",
    ])
    assert status == 0
    assert calls[0][2]["lat"] == 34.1
    assert calls[0][2]["lon"] == -111.2
    assert calls[0][2]["site_id"] == "source-id"
    assert calls[0][2]["download_dir"] == str(tmp_path / "raw")
    assert calls[0][2]["max_tiles"] == 6
    assert calls[0][2]["soil_pixel_size"] == 0.0002
    assert calls[0][2]["soil_top_depth"] == 20
    assert "Full pipeline complete" in capsys.readouterr().out


def test_full_run_cli_reports_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "ohqbuilder.cli.run_full_pipeline",
        lambda *args, **kwargs: (_ for _ in ()).throw(FullRunError("download failed")),
    )
    assert main([
        "full-run", "--root", str(tmp_path), "--site", "SITE_A",
        "--lat", "34.1", "--lon", "-111.2",
    ]) == 2


def test_create_pour_points_cli_uses_site_defaults(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_generate(junctions, output, overwrite=False):
        calls.append((junctions, output, overwrite))
        return PourPointResult(Path(output).resolve(), 3)

    monkeypatch.setattr("ohqbuilder.cli.generate_pour_points", fake_generate)
    status = main([
        "create-pour-points",
        "--root",
        str(tmp_path),
        "--site",
        "SITE_A",
        "--overwrite",
    ])

    outputs = tmp_path / "SITE_A" / "outputs"
    assert status == 0
    assert calls == [(outputs / "junctions.gpkg", outputs / "pour_points.shp", True)]
    assert "Generated 3 pour point(s)" in capsys.readouterr().out


def test_create_pour_points_cli_reports_generation_error(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise PourPointGenerationError("bad junctions")

    monkeypatch.setattr("ohqbuilder.cli.generate_pour_points", fail)
    assert main([
        "create-pour-points",
        "--root",
        str(tmp_path),
        "--site",
        "SITE_A",
    ]) == 2


def test_prepare_inputs_cli_returns_error_when_legacy_workflow_fails(monkeypatch):
    def fail(*args, **kwargs):
        raise LegacyInputWorkflowError("no qgis")

    monkeypatch.setattr("ohqbuilder.cli.run_legacy_input_workflow", fail)

    assert main(["prepare-inputs", "--root", "/tmp/root", "--site", "SITE_A"]) == 2




def test_watershed_bounds_cli_prints_bounds(monkeypatch, capsys):
    from ohqbuilder.watershed_bounds import WatershedBoundsResult

    monkeypatch.setattr(
        "ohqbuilder.cli.resolve_materialization_bounds",
        lambda **kwargs: WatershedBoundsResult((-77.1, 39.0, -77.0, 39.1), "nldi", "url"),
    )

    status = main([
        "watershed-bounds",
        "--lat",
        "39.0",
        "--lon",
        "-77.0",
        "--buffer",
        "20000",
    ])

    assert status == 0
    assert capsys.readouterr().out.strip() == "-77.1,39.0,-77.0,39.1"

def test_prepare_hydrology_parser_accepts_legacy_paths():
    args = build_parser().parse_args([
        "prepare-hydrology",
        "--root",
        "/tmp/root",
        "--site",
        "SITE_A",
        "--dem-path",
        "/tmp/dem.tif",
        "--flowline-path",
        "/tmp/flowlines.gpkg",
        "--target-epsg",
        "26918",
        "--dry-run",
    ])

    assert args.command == "prepare-hydrology"
    assert args.dem_path == "/tmp/dem.tif"
    assert args.flowline_path == "/tmp/flowlines.gpkg"
    assert args.target_epsg == "26918"
    assert args.dry_run is True


def test_prepare_hydrology_cli_runs_before_phase1_inputs(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_hydrology(root, site, script_dir, options):
        calls.append((root, site, script_dir, options))

    monkeypatch.setattr("ohqbuilder.cli.run_hydrology_preprocessing", fake_hydrology)

    status = main([
        "prepare-hydrology",
        "--root",
        str(tmp_path),
        "--site",
        "SITE_A",
        "--flowdir-path",
        str(tmp_path / "flow_dir.tif"),
        "--flowacc-path",
        str(tmp_path / "flow_acc.tif"),
        "--no-force",
    ])

    assert status == 0
    assert calls[0][0] == str(tmp_path)
    assert calls[0][1] == "SITE_A"
    assert calls[0][2] is None
    assert calls[0][3].flowdir_path == str(tmp_path / "flow_dir.tif")
    assert calls[0][3].flowacc_path == str(tmp_path / "flow_acc.tif")
    assert calls[0][3].force is False
    assert "Hydrology preprocessing complete." in capsys.readouterr().out


def test_prepare_hydrology_cli_reports_legacy_errors(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise LegacyInputWorkflowError("missing flowlines")

    monkeypatch.setattr("ohqbuilder.cli.run_hydrology_preprocessing", fail)

    assert main(["prepare-hydrology", "--root", str(tmp_path), "--site", "SITE_A"]) == 2

def test_check_inputs_parser_supports_no_schema():
    args = build_parser().parse_args([
        "check-inputs",
        "--root",
        "/tmp/root",
        "--site",
        "SITE_A",
        "--no-schema",
    ])

    assert args.command == "check-inputs"
    assert args.no_schema is True


def test_run_parser_supports_end_to_end_options():
    args = build_parser().parse_args([
        "run",
        "--root",
        "/tmp/root",
        "--site",
        "SITE_A",
        "--project-name",
        "Custom",
        "--out",
        "/tmp/out.ohq",
        "--skip-prepare",
        "--no-schema",
    ])

    assert args.command == "run"
    assert args.project_name == "Custom"
    assert args.skip_prepare is True
    assert args.no_schema is True
    assert args.out_dir is None
    assert args.prepare_dry_run is False


def test_run_command_can_skip_prepare_and_build(monkeypatch, tmp_path):
    calls = []

    class OkValidator:
        def validate(self, settings, check_schema=True):
            calls.append(("validate", settings.project_name, check_schema))
            return type("Result", (), {"warnings": [], "errors": [], "ok": True})()

    def fake_build(settings, output_path=None, dry_run=False):
        calls.append(("build", settings.project_name, output_path, dry_run))
        return str(output_path or "default.ohq")

    monkeypatch.setattr("ohqbuilder.cli.InputValidator", OkValidator)
    monkeypatch.setattr("ohqbuilder.cli.build_ohq_project", fake_build)

    out = tmp_path / "out.ohq"
    status = main([
        "run",
        "--root",
        str(tmp_path),
        "--site",
        "SITE_A",
        "--project-name",
        "Custom",
        "--out",
        str(out),
        "--skip-prepare",
    ])

    assert status == 0
    assert calls == [
        ("validate", "Custom", True),
        ("build", "Custom", out.resolve(), False),
    ]


def test_run_command_stops_when_prepare_fails(monkeypatch):
    def fail(*args, **kwargs):
        raise LegacyInputWorkflowError("no qgis")

    monkeypatch.setattr("ohqbuilder.cli.run_legacy_input_workflow", fail)

    status = main(["run", "--root", "/tmp/root", "--site", "SITE_A"])

    assert status == 2


def test_build_stops_when_input_check_fails(monkeypatch):
    calls = []

    class BadValidator:
        def validate(self, settings, check_schema=True):
            calls.append(("validate", check_schema))
            return type("Result", (), {"warnings": [], "errors": ["missing"], "ok": False})()

    def fake_build(*args, **kwargs):
        calls.append(("build",))
        return "out.ohq"

    monkeypatch.setattr("ohqbuilder.cli.InputValidator", BadValidator)
    monkeypatch.setattr("ohqbuilder.cli.build_ohq_project", fake_build)

    status = main(["build", "--root", "/tmp/root", "--site", "SITE_A"])

    assert status == 2
    assert calls == [("validate", True)]


def test_build_can_skip_input_check(monkeypatch):
    calls = []

    class BadValidator:
        def validate(self, settings, check_schema=True):
            calls.append(("validate",))
            return type("Result", (), {"warnings": [], "errors": ["missing"], "ok": False})()

    def fake_build(settings, output_path=None, dry_run=False):
        calls.append(("build", settings.project_name, dry_run))
        return "out.ohq"

    monkeypatch.setattr("ohqbuilder.cli.InputValidator", BadValidator)
    monkeypatch.setattr("ohqbuilder.cli.build_ohq_project", fake_build)

    status = main(["build", "--root", "/tmp/root", "--site", "SITE_A", "--skip-input-check"])

    assert status == 0
    assert calls == [("build", "SITE_A", False)]


def test_validate_supports_no_schema_input_check(monkeypatch):
    calls = []

    class OkValidator:
        def validate(self, settings, check_schema=True):
            calls.append(("validate_inputs", check_schema))
            return type("Result", (), {"warnings": [], "errors": [], "ok": True})()

    def fake_build(settings, output_path=None, dry_run=False):
        calls.append(("build", dry_run))
        return None

    monkeypatch.setattr("ohqbuilder.cli.InputValidator", OkValidator)
    monkeypatch.setattr("ohqbuilder.cli.build_ohq_project", fake_build)

    status = main(["validate", "--root", "/tmp/root", "--site", "SITE_A", "--no-schema"])

    assert status == 0
    assert calls == [("validate_inputs", False), ("build", True)]


def test_doctor_cli_returns_report_status(monkeypatch):
    class Report:
        ok = False

        def lines(self):
            return ["ERROR: qgis - missing"]

    monkeypatch.setattr("ohqbuilder.cli.run_doctor", lambda script_dir, strict_gis: Report())

    status = main(["doctor", "--strict-gis"])

    assert status == 2


def test_doctor_cli_can_emit_json(monkeypatch, capsys):
    class Report:
        ok = True

        def to_dict(self):
            return {"ok": True, "checks": []}

        def lines(self):
            return ["OK: python - 3.14"]

    monkeypatch.setattr("ohqbuilder.cli.run_doctor", lambda script_dir, strict_gis: Report())

    status = main(["doctor", "--json"])

    assert status == 0
    assert '"ok": true' in capsys.readouterr().out


def test_check_inputs_cli_can_emit_json(monkeypatch, capsys):
    class OkValidator:
        def validate(self, settings, check_schema=True):
            return type(
                "Result",
                (),
                {"ok": True, "to_dict": lambda self: {"ok": True, "errors": [], "warnings": []}},
            )()

    monkeypatch.setattr("ohqbuilder.cli.InputValidator", OkValidator)

    status = main(["check-inputs", "--root", "/tmp/root", "--site", "SITE_A", "--json"])

    assert status == 0
    assert '"errors": []' in capsys.readouterr().out


def test_init_inputs_cli_creates_manifest(tmp_path):
    status = main(["init-inputs", "--root", str(tmp_path), "--site", "SITE_A"])

    assert status == 0
    assert (tmp_path / "SITE_A" / "INPUTS.md").is_file()

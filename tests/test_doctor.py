from ohqbuilder.doctor import DoctorCheck, DoctorReport, run_doctor


def test_doctor_report_fails_only_required_checks():
    report = DoctorReport([
        DoctorCheck("required", False, "missing", required=True),
        DoctorCheck("optional", False, "missing", required=False),
    ])

    assert not report.ok
    assert report.lines() == [
        "ERROR: required - missing",
        "WARN: optional - missing",
    ]


def test_doctor_accepts_existing_legacy_script_dir(tmp_path):
    (tmp_path / "run_phase1.py").write_text("", encoding="utf-8")
    (tmp_path / "run_phase2.py").write_text("", encoding="utf-8")

    report = run_doctor(tmp_path)

    by_name = {check.name: check for check in report.checks}
    assert by_name["legacy script directory"].ok
    assert by_name["run_phase1.py"].ok
    assert by_name["run_phase2.py"].ok


def test_doctor_strict_gis_makes_gis_checks_required(tmp_path, monkeypatch):
    (tmp_path / "run_phase1.py").write_text("", encoding="utf-8")
    (tmp_path / "run_phase2.py").write_text("", encoding="utf-8")

    def fake_find_spec(name):
        return None if name in {"geopandas", "qgis.core"} else object()

    monkeypatch.setattr("ohqbuilder.doctor.importlib.util.find_spec", fake_find_spec)

    report = run_doctor(tmp_path, strict_gis=True)

    by_name = {check.name: check for check in report.checks}
    assert by_name["geopandas"].required
    assert by_name["qgis"].required
    assert not report.ok

from ohqbuilder.pour_point_candidates import _confluence_nodes, _too_close_pairs
from ohqbuilder.cli import main


class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def distance(self, other):
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5


def test_confluence_nodes_require_two_incoming_reaches():
    records = [
        ("headwater", "n1"),
        ("left", "junction"),
        ("right", "junction"),
        ("third", "junction"),
        ("outlet", "mouth"),
    ]

    assert _confluence_nodes(records) == {"junction": ["left", "right", "third"]}


def test_too_close_pairs_uses_strict_minimum_spacing():
    points = [Point(0, 0), Point(99, 0), Point(200, 0)]

    assert _too_close_pairs(points, 100) == [(0, 1)]


def test_promote_cli_uses_review_and_boundary_defaults(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(
        "ohqbuilder.cli.promote_pour_point_candidates",
        lambda *args, **kwargs: calls.append((args, kwargs)) or args[2],
    )

    assert main(["promote-pour-points", "--root", str(tmp_path), "--site", "SITE"]) == 0

    outputs = tmp_path / "SITE" / "outputs"
    assert calls[0][0] == (
        outputs / "pour_point_candidates.gpkg",
        outputs / "watershed_boundary.gpkg",
        outputs / "pour_points.shp",
    )
    assert calls[0][1]["minimum_spacing_m"] == 100.0
    assert "Wrote approved Phase 2 pour points" in capsys.readouterr().out

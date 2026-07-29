import pytest

from ohqbuilder.nhdplus_trace import _field, _trace_upstream_indices


def test_field_alias_matching_is_case_insensitive():
    assert _field(["nhdplusid", "FROMNODE"], ("NHDPlusID",), "ID") == "nhdplusid"
    assert _field(["nhdplusid", "FROMNODE"], ("FromNode",), "node") == "FROMNODE"


def test_field_alias_reports_required_connectivity():
    with pytest.raises(Exception, match="Missing downstream node field"):
        _field(["NHDPlusID"], ("ToNode", "ToNodeID"), "downstream node")


def test_trace_upstream_indices_follows_branches_and_ignores_downstream():
    records = [
        ("outlet", "n2", "n1"),
        ("left", "n3", "n2"),
        ("right", "n4", "n2"),
        ("headwater", "n5", "n3"),
        ("downstream", "n1", "n0"),
    ]

    assert _trace_upstream_indices(records, "outlet") == {
        "outlet", "left", "right", "headwater"
    }

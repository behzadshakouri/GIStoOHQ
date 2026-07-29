from ohqbuilder.pour_point_candidates import _confluence_nodes


def test_confluence_nodes_require_two_incoming_reaches():
    records = [
        ("headwater", "n1"),
        ("left", "junction"),
        ("right", "junction"),
        ("third", "junction"),
        ("outlet", "mouth"),
    ]

    assert _confluence_nodes(records) == {"junction": ["left", "right", "third"]}

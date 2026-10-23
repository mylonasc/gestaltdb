"""PropEnh-14 (#127): relation hierarchies for live typed traversal."""

import random

import pytest

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LMDBStore
from gestaltdb.sampling import RelationHierarchy
from gestaltdb.serializers import PickleSerializer


@pytest.fixture
def graph(tmp_path):
    pytest.importorskip("lmdb")
    db = GraphDB(LMDBStore(path=str(tmp_path / "db")), PickleSerializer())
    try:
        db.put_node(Node(node_id="hub", labels=["Warehouse"], properties={}))
        for node_id in ("ocean", "air", "rail", "generic", "destination"):
            db.put_node(Node(node_id=node_id, labels=["Freight"], properties={}))
        for edge_id, target, edge_type in (
            ("e-ocean", "ocean", "Ocean_Freight"),
            ("e-air", "air", "Air_Freight"),
            ("e-rail", "rail", "Rail"),
            ("e-generic", "generic", "Transport_Edge"),
        ):
            db.put_edge(Edge(
                edge_id=edge_id, source="hub", target=target,
                properties={"type": edge_type},
            ))
            db.put_edge(Edge(
                edge_id=f"{edge_id}-final", source=target,
                target="destination", properties={"type": "DELIVERS_TO"},
            ))
        yield db
    finally:
        db.close()


def hierarchy():
    return RelationHierarchy({
        "Ocean_Freight": ["Transport_Edge"],
        "Air_Freight": ["Transport_Edge"],
        "Rail": ["Transport_Edge"],
    })


def test_live_neighbor_sampling_expands_parent_relation(graph):
    literal = graph.sample_neighbors("hub", "Transport_Edge", sample_size=10)
    assert [record["edge_type"] for record in literal] == ["Transport_Edge"]

    expanded = graph.sample_neighbors(
        "hub", "Transport_Edge", sample_size=10, relation_hierarchy=hierarchy()
    )
    assert {record["edge_type"] for record in expanded} == {
        "Transport_Edge", "Ocean_Freight", "Air_Freight", "Rail"}
    assert {record["neighbor_id"] for record in expanded} == {
        b"ocean", b"air", b"rail", b"generic"}


def test_live_neighbor_fanout_is_shared_across_expanded_relations(graph):
    for seed in range(25):
        sampled = graph.sample_neighbors(
            "hub",
            "Transport_Edge",
            sample_size=2,
            rng=random.Random(seed),
            relation_hierarchy=hierarchy(),
        )
        assert len(sampled) == 2
        assert all(
            record["edge_type"] in {
                "Transport_Edge", "Ocean_Freight", "Air_Freight", "Rail"}
            for record in sampled
        )


def test_live_typed_paths_apply_hierarchy_per_hop(graph):
    pattern = [
        {"edge_type": "Transport_Edge", "sample_size": 10},
        {"edge_type": "DELIVERS_TO", "sample_size": 10},
    ]
    literal = graph.sample_typed_paths(["hub"], pattern)
    assert len(literal) == 1
    assert literal[0]["path"][0]["edge_type"] == "Transport_Edge"

    paths = graph.sample_typed_paths(
        ["hub"], pattern, relation_hierarchy=hierarchy()
    )
    assert len(paths) == 4
    assert {path["path"][0]["edge_type"] for path in paths} == {
        "Transport_Edge", "Ocean_Freight", "Air_Freight", "Rail"}
    assert all(path["path"][1]["edge_type"] == "DELIVERS_TO" for path in paths)


def test_live_typed_subgraph_materializes_expanded_relations(graph):
    subgraph = graph.sample_typed_subgraph(
        ["hub"],
        [{"edge_type": "Transport_Edge", "sample_size": 10}],
        relation_hierarchy=hierarchy(),
    )
    assert len(subgraph["paths"]) == 4
    assert len(subgraph["edges"]) == 4
    assert {edge.properties["type"] for edge in subgraph["edges"].values()} == {
        "Transport_Edge", "Ocean_Freight", "Air_Freight", "Rail"}


def test_invalid_hierarchy_type_fails_closed(graph):
    with pytest.raises(ValueError, match="RelationHierarchy"):
        graph.sample_neighbors(
            "hub", "Transport_Edge", relation_hierarchy={"Rail": ["Transport_Edge"]}
        )
    with pytest.raises(ValueError, match="RelationHierarchy"):
        graph.sample_typed_paths(
            ["hub"],
            [{"edge_type": "Transport_Edge", "sample_size": 10}],
            relation_hierarchy={},
        )
    with pytest.raises(ValueError, match="RelationHierarchy"):
        graph.sample_typed_paths(
            [],
            [{"edge_type": "Transport_Edge", "sample_size": 10}],
            relation_hierarchy={},
        )

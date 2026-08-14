import random

import pytest

from gestaltdb.graphdb import Edge, Node
from gestaltdb.sampling import SamplingHop, SamplingPattern
from gestaltdb.sampling import as_sampling_pattern

from .conftest import populate_typed_graph


def test_sampling_hop_round_trips_dict_config():
    hop = SamplingHop.from_dict({"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2})

    assert hop.edge_type == "drug-to-protein"
    assert hop.direction == "out"
    assert hop.sample_size == 2
    assert hop.to_dict() == {"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2}


def test_sampling_pattern_normalizes_dicts_and_hops():
    pattern = SamplingPattern([
        SamplingHop("drug-to-protein", sample_size=2),
        {"edge_type": "protein-to-disease", "direction": "out", "sample_size": 1},
    ])

    assert len(pattern) == 2
    assert pattern.hops[0].edge_type == "drug-to-protein"
    assert pattern.hops[1].sample_size == 1


def test_sampling_pattern_from_dicts_and_to_dicts_round_trip():
    pattern = SamplingPattern.from_dicts([
        {"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2},
    ])

    assert pattern.to_dicts() == [{"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2}]
    assert as_sampling_pattern(pattern) is pattern


def test_sampling_hop_validates_values():
    with pytest.raises(ValueError, match="direction"):
        SamplingHop("drug-to-protein", direction="sideways")
    with pytest.raises(ValueError, match="sample_size"):
        SamplingHop("drug-to-protein", sample_size=0)


def test_sample_neighbors_uses_typed_frontier(graph_db):
    populate_typed_graph(graph_db)

    sample = graph_db.sample_neighbors(
        "drug-1",
        "drug-to-protein",
        direction="out",
        sample_size=1,
        rng=random.Random(7),
    )

    assert len(sample) == 1
    assert sample[0]["edge_type"] == "drug-to-protein"
    assert sample[0]["neighbor_id"] in {b"protein-1", b"protein-2"}


def test_sample_neighbors_streams_typed_adjacency(graph_db):
    populate_typed_graph(graph_db)

    def fail_materialized_adjacency(*args, **kwargs):
        raise AssertionError("sample_neighbors should not materialize full typed adjacency")

    graph_db.get_typed_adjacency = fail_materialized_adjacency

    sample = graph_db.sample_neighbors(
        "drug-1",
        "drug-to-protein",
        direction="out",
        sample_size=1,
        rng=random.Random(7),
    )

    assert len(sample) == 1
    assert sample[0]["edge_type"] == "drug-to-protein"


def test_sample_typed_paths_respects_edge_type_sequence(graph_db):
    populate_typed_graph(graph_db)

    paths = graph_db.sample_typed_paths(
        ["drug-1", "drug-2"],
        SamplingPattern([
            SamplingHop("drug-to-protein", direction="out", sample_size=2),
            SamplingHop("protein-to-disease", direction="out", sample_size=1),
        ]),
        rng=random.Random(3),
    )

    assert paths
    for sampled_path in paths:
        assert len(sampled_path["path"]) == 2
        assert sampled_path["path"][0]["edge_type"] == "drug-to-protein"
        assert sampled_path["path"][1]["edge_type"] == "protein-to-disease"
        assert sampled_path["path"][0]["target_id"].startswith(b"protein-")
        assert sampled_path["path"][1]["target_id"].startswith(b"disease-")


def test_sample_typed_subgraph_materializes_sampled_records(graph_db):
    populate_typed_graph(graph_db)

    subgraph = graph_db.sample_typed_subgraph(
        ["drug-1"],
        [
            {"edge_type": "drug-to-protein", "direction": "out", "sample_size": 1},
            {"edge_type": "protein-to-disease", "direction": "out", "sample_size": 1},
        ],
        rng=random.Random(11),
    )

    assert b"drug-1" in subgraph["nodes"]
    assert subgraph["edges"]
    assert subgraph["paths"]
    assert all(node is not None for node in subgraph["nodes"].values())
    assert all(edge is not None for edge in subgraph["edges"].values())


def test_put_edges_bulk_append_only_skips_existing_edge_reads(graph_db):
    for node_id in ["drug-1", "protein-1"]:
        graph_db.put_node(Node(node_id=node_id))

    def fail_get_edge(*args, **kwargs):
        raise AssertionError("append-only ingestion should skip existing-edge reads")

    graph_db.get_edge = fail_get_edge

    graph_db.put_edges_bulk(
        [Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "drug-to-protein"})],
        check_existing=False,
    )

    assert graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein", direction="out") == [b"protein-1"]


def test_put_edges_bulk_uses_bulk_typed_adjacency_writer(graph_db):
    for node_id in ["drug-1", "protein-1"]:
        graph_db.put_node(Node(node_id=node_id))

    def fail_single_typed_adjacency(*args, **kwargs):
        raise AssertionError("put_edges_bulk should use put_typed_adjacency_bulk")

    graph_db.store.put_typed_adjacency = fail_single_typed_adjacency

    graph_db.put_edges_bulk(
        [Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "drug-to-protein"})],
        check_existing=False,
    )

    assert graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein", direction="out") == [b"protein-1"]
